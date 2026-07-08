#!/usr/bin/env python3
"""Reconcile prod to be an exact one-way mirror of dev (image granularity).

Dev is the source of truth. Each run computes a per-image content fingerprint
for both environments and makes prod match dev:

  ADD      image in dev, not in prod          -> copy the dev image whole
  REPLACE  image in both, content differs      -> delete prod image, re-copy dev
  DELETE   image in prod, not in dev's corpus  -> delete from prod

REPLACE is delete-then-recopy (never a word-level merge), so it is correct for
re-OCR renumbering and merges that would otherwise corrupt an insert-only sync.
DELETE propagates dev cleanups (orphans, twins, empties) to prod.

The fingerprint covers only content that must match across environments:
word (line_id, word_id, text), label (line_id, word_id, label, validation_status),
place (merchant_name, place_id), and the set of receipt_ids. It deliberately
excludes env-specific fields the copy rewrites (S3 bucket names, embedding_status).

Empty images (0 receipts) are never promoted; if an image became empty on dev,
its prod copy is DELETEd.

Usage:
    python scripts/reconcile_dev_to_prod.py                 # dry run (default)
    python scripts/reconcile_dev_to_prod.py --no-dry-run    # apply
    python scripts/reconcile_dev_to_prod.py --skip-health-gate

The ChromaDB/embedding leg self-heals via the prod stream processor + embedding
step functions, but only if that chain is healthy — so apply is gated on a
compaction-queue health check (DLQs empty, backlog bounded) unless overridden.
"""

import argparse
import hashlib
import json
import logging
import shutil
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

import boto3

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))

from receipt_dynamo import DynamoClient
from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo.data.export_image import export_image

# Reuse the proven copy engine (bucket rewrite + embedding reset + batch writes)
from copy_dynamodb_dev_to_prod import copy_all_images, get_table_and_bucket_names

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

# Prod compaction health-gate targets (see `pulumi stack output --stack prod`)
PROD_QUEUE_PREFIX = "chromadb-prod-queues"
PROD_QUEUE_NAMES = [
    "chromadb-prod-queues-words-queue-f9c636f",
    "chromadb-prod-queues-lines-queue-bf0a05b",
    "chromadb-prod-queues-summary-queue-367f9ad",
]
PROD_DLQ_NAMES = [
    "chromadb-prod-queues-words-dlq-e715852",
    "chromadb-prod-queues-lines-dlq-c67b0c1",
    "chromadb-prod-queues-summary-dlq-1ebd871",
]
BACKLOG_LIMIT = 500  # refuse to start if a live queue is already this deep


def _fingerprint_env(client: DynamoClient) -> dict:
    """Return {image_id: {"fp": <hex>, "has_receipts": bool}} for one env.

    One bulk scan per child entity, grouped by image, then hashed. Only
    content that must match across environments is included.
    """
    words = defaultdict(list)      # image_id -> [(line, word, text)]
    labels = defaultdict(list)     # image_id -> [(line, word, label, status)]
    places = defaultdict(list)     # image_id -> [(receipt_id, merchant, place_id)]
    receipts = defaultdict(set)    # image_id -> {receipt_id}

    def _scan(list_fn, sink):
        lek = None
        while True:
            batch, lek = list_fn(limit=1000, last_evaluated_key=lek)
            sink(batch)
            if not lek:
                break

    _scan(
        client.list_receipt_words,
        lambda b: [words[w.image_id].append((w.line_id, w.word_id, w.text)) for w in b],
    )
    _scan(
        client.list_receipt_word_labels,
        lambda b: [
            labels[l.image_id].append(
                (l.line_id, l.word_id, l.label, str(l.validation_status))
            )
            for l in b
        ],
    )
    _scan(
        client.list_receipts,
        lambda b: [receipts[r.image_id].add(r.receipt_id) for r in b],
    )
    _scan(
        client.list_receipt_places,
        lambda b: [
            places[p.image_id].append(
                (p.receipt_id, p.merchant_name or "", getattr(p, "place_id", "") or "")
            )
            for p in b
        ],
    )

    all_ids = set(words) | set(labels) | set(receipts) | set(places)
    out = {}
    for iid in all_ids:
        payload = {
            "words": sorted(words[iid]),
            "labels": sorted(labels[iid]),
            "places": sorted(places[iid]),
            "receipts": sorted(receipts[iid]),
        }
        fp = hashlib.sha256(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode()
        ).hexdigest()
        out[iid] = {"fp": fp, "has_receipts": bool(receipts[iid])}
    return out


def plan(dev_fp: dict, prod_fp: dict) -> dict:
    """Compute ADD / REPLACE / DELETE sets. Dev source = images with receipts."""
    dev_source = {iid for iid, v in dev_fp.items() if v["has_receipts"]}
    prod_ids = set(prod_fp)

    adds = sorted(dev_source - prod_ids)
    replaces = sorted(
        iid for iid in (dev_source & prod_ids) if dev_fp[iid]["fp"] != prod_fp[iid]["fp"]
    )
    deletes = sorted(prod_ids - dev_source)
    # dev images with no receipts are simply never promoted (not an error)
    empties_skipped = sorted(
        iid for iid, v in dev_fp.items() if not v["has_receipts"] and iid not in prod_ids
    )
    return {
        "add": adds,
        "replace": replaces,
        "delete": deletes,
        "empty_skipped": empties_skipped,
    }


def _queue_depths(sqs, names: list) -> dict:
    out = {}
    for name in names:
        try:
            url = sqs.get_queue_url(QueueName=name)["QueueUrl"]
            attrs = sqs.get_queue_attributes(
                QueueUrl=url,
                AttributeNames=[
                    "ApproximateNumberOfMessages",
                    "ApproximateNumberOfMessagesNotVisible",
                ],
            )["Attributes"]
            out[name] = int(attrs["ApproximateNumberOfMessages"]) + int(
                attrs["ApproximateNumberOfMessagesNotVisible"]
            )
        except Exception as e:  # noqa: BLE001
            out[name] = f"ERR:{type(e).__name__}"
    return out


def health_gate(strict: bool = True) -> bool:
    """Pre-apply check on the prod compaction chain. Returns True if healthy."""
    sqs = boto3.client("sqs", region_name="us-east-1")
    live = _queue_depths(sqs, PROD_QUEUE_NAMES)
    dead = _queue_depths(sqs, PROD_DLQ_NAMES)
    logger.info("Prod compaction health:")
    logger.info(f"  live queues: {live}")
    logger.info(f"  DLQs:        {dead}")

    dlq_bad = [n for n, v in dead.items() if isinstance(v, int) and v > 0]
    backlog_bad = [n for n, v in live.items() if isinstance(v, int) and v > BACKLOG_LIMIT]
    err = [n for n, v in {**live, **dead}.items() if isinstance(v, str)]

    if dlq_bad:
        logger.warning(f"  ⚠️  messages in DLQ (stuck compaction): {dlq_bad}")
    if backlog_bad:
        logger.warning(f"  ⚠️  live backlog > {BACKLOG_LIMIT}: {backlog_bad}")
    if err:
        logger.warning(f"  ⚠️  could not read queues: {err}")

    healthy = not (dlq_bad or backlog_bad or err)
    if healthy:
        logger.info("  ✅ compaction chain healthy")
    elif strict:
        logger.error(
            "  ❌ compaction chain unhealthy — refusing to apply. "
            "Drain/redrive the DLQ and re-check, or pass --skip-health-gate."
        )
    return healthy


def apply_plan(p: dict, dev_client, prod_client, dev_config, prod_config):
    """Execute DELETE, then REPLACE-delete + copy, then ADD copy."""
    dev_table = dev_config["table"]

    # 1. Deletes (pure deletes + the delete half of replaces)
    to_delete = p["delete"] + p["replace"]
    logger.info(f"Deleting {len(to_delete)} prod images (pure + replace)...")
    for iid in to_delete:
        res = prod_client.delete_image_details(iid)
        logger.info(f"  deleted {iid}: {sum(res.values()) if res else 0} items")

    # 2. Export the dev images we need to (re)create, then copy them whole
    to_copy = p["add"] + p["replace"]
    if not to_copy:
        logger.info("No images to add/replace.")
        return
    tmp = Path(tempfile.mkdtemp(prefix="reconcile_export_"))
    try:
        logger.info(f"Exporting {len(to_copy)} dev images → {tmp}...")
        for iid in to_copy:
            export_image(dev_table, iid, str(tmp))
        logger.info("Copying to prod...")
        stats = copy_all_images(
            tmp,
            prod_client,
            dev_config,
            prod_config,
            dry_run=False,
            skip_existing=True,   # replaced images were just deleted
            skip_empty=True,
        )
        logger.info(
            f"Copied: {stats['copied']}, skipped_empty: {stats['skipped_empty']}, "
            f"failed: {stats['failed']}"
        )
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser(description="Mirror prod to dev (image granularity)")
    ap.add_argument("--dry-run", action="store_true", default=True)
    ap.add_argument("--no-dry-run", action="store_false", dest="dry_run")
    ap.add_argument(
        "--skip-health-gate",
        action="store_true",
        help="Apply even if the prod compaction chain looks unhealthy",
    )
    ap.add_argument("--limit-list", type=int, default=25, help="Ids to print per bucket")
    args = ap.parse_args()

    logger.info("Fingerprinting dev...")
    dev_client = DynamoClient(load_env("dev")["dynamodb_table_name"])
    dev_fp = _fingerprint_env(dev_client)
    logger.info("Fingerprinting prod...")
    prod_client = DynamoClient(load_env("prod")["dynamodb_table_name"])
    prod_fp = _fingerprint_env(prod_client)

    p = plan(dev_fp, prod_fp)
    logger.info("=" * 60)
    logger.info("RECONCILE PLAN (dev → prod mirror)")
    logger.info("=" * 60)
    logger.info(f"  ADD     (new dev images):        {len(p['add'])}")
    logger.info(f"  REPLACE (content changed):       {len(p['replace'])}")
    logger.info(f"  DELETE  (gone/empty on dev):     {len(p['delete'])}")
    logger.info(f"  skipped (empty dev images):      {len(p['empty_skipped'])}")
    for bucket in ("add", "replace", "delete"):
        if p[bucket]:
            shown = p[bucket][: args.limit_list]
            logger.info(f"  {bucket}: {shown}{' ...' if len(p[bucket]) > len(shown) else ''}")

    if not (p["add"] or p["replace"] or p["delete"]):
        logger.info("\n✅ prod already matches dev. Nothing to do.")
        return

    if args.dry_run:
        logger.info("\n✅ Dry run. Re-run with --no-dry-run to apply.")
        return

    if not args.skip_health_gate and not health_gate(strict=True):
        sys.exit(1)

    dev_config = get_table_and_bucket_names("dev")
    prod_config = get_table_and_bucket_names("prod")
    apply_plan(p, dev_client, prod_client, dev_config, prod_config)
    logger.info(
        "\n✅ Reconcile applied. Kick prod embedding step functions "
        "(start_ingestion_prod.sh) and re-run health_gate to confirm drain."
    )


if __name__ == "__main__":
    main()
