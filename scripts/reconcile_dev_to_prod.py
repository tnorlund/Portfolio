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
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import boto3

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR.parent))
# Also add the package source dir so `receipt_dynamo` resolves to the package,
# not the outer namespace directory, in a fresh checkout with no editable install.
sys.path.insert(0, str(SCRIPT_DIR.parent / "receipt_dynamo"))

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

# Entity TYPEs (DynamoDB "TYPE" attr) that a REPLACE can safely delete, because
# they are either restored by the copy/OCR-sync path or are derived data that
# regenerates after copy. If a prod image partition contains any TYPE outside
# this set, the REPLACE is skipped (not deleted) to avoid silent data loss.
RESTORABLE_TYPES = {
    # restored by copy_image_entities
    "IMAGE", "RECEIPT", "LINE", "WORD", "LETTER",
    "RECEIPT_LINE", "RECEIPT_WORD", "RECEIPT_LETTER", "RECEIPT_WORD_LABEL",
    # Legacy metadata is replayed by copy_image_entities after a whole-image
    # REPLACE; keeping it restorable prevents the mirror from dropping dev rows.
    "RECEIPT_METADATA", "RECEIPT_PLACE", "RECEIPT_BARCODE",
    "OCR_ROUTING_DECISION",
    # restored by the filtered sync_ocr_jobs step
    "OCR_JOB",
    # derived / regenerated after copy (safe to drop on replace)
    "RECEIPT_SUMMARY", "COMPACTION_RUN", "EMBEDDING_STATUS",
}


def _fingerprint_env(client: DynamoClient) -> dict:
    """Return {image_id: {"fp": <hex>, "has_receipts": bool}} for one env.

    One bulk scan per child entity, grouped by image, then hashed. Only
    content that must match across environments is included.
    """
    words = defaultdict(list)      # image_id -> [(receipt, line, word, text)]
    labels = defaultdict(list)     # image_id -> [(receipt, line, word, label, status)]
    places = defaultdict(list)     # image_id -> [(receipt_id, merchant, place_id)]
    receipts = defaultdict(set)    # image_id -> {receipt_id}
    barcodes = defaultdict(list)   # image_id -> [(receipt, barcode_id, symbology, text)]
    images = set()                 # image_ids with an Image row (may be childless)

    def _scan(list_fn, sink):
        lek = None
        while True:
            batch, lek = list_fn(limit=1000, last_evaluated_key=lek)
            sink(batch)
            if not lek:
                break

    # receipt_id is included in the word/label tuples so a word/label that moves
    # between receipts on a multi-receipt image changes the fingerprint.
    _scan(
        client.list_receipt_words,
        lambda b: [
            words[w.image_id].append((w.receipt_id, w.line_id, w.word_id, w.text))
            for w in b
        ],
    )
    _scan(
        client.list_receipt_word_labels,
        lambda b: [
            labels[l.image_id].append(
                (l.receipt_id, l.line_id, l.word_id, l.label, str(l.validation_status))
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
    # Barcodes are restored only via REPLACE, so a barcode-only change must
    # change the fingerprint or prod would stay stale.
    _scan(
        client.list_receipt_barcodes,
        lambda b: [
            barcodes[bc.image_id].append(
                (bc.receipt_id, bc.barcode_id, bc.symbology, bc.text or "")
            )
            for bc in b
        ],
    )
    # NOTE: ReceiptMetadata is intentionally NOT fingerprinted. It is a legacy
    # entity superseded by ReceiptPlace (nothing in the pipeline writes it now),
    # so its rows are stale/orphaned and would cause perpetual spurious REPLACEs.
    # Merchant/place state is mirrored via ReceiptPlace (fingerprinted above).

    # Include bare Image rows so childless shells are still ADD/REPLACE/DELETE'd
    # correctly (otherwise a prod Image with no children is invisible here and a
    # skip_existing copy would silently skip it).
    _scan(
        client.list_images,
        lambda b: [images.add(im.image_id) for im in b],
    )

    all_ids = (
        set(words) | set(labels) | set(receipts) | set(places)
        | set(barcodes) | images
    )
    out = {}
    for iid in all_ids:
        # Deliberately a curated set of STABLE, decision-level fields (text,
        # labels+status, merchant/place, barcodes, metadata resolution), not the
        # full entity rows. Volatile fields (timestamps, label/metadata
        # `reasoning`, per-word confidence) are excluded on purpose: hashing them
        # would trigger a REPLACE on every re-evaluation even when the mirrored
        # decision is unchanged. Geometry/bbox changes only ever occur via
        # re-OCR, which also renumbers word ids/text and is therefore already
        # captured by `words`.
        payload = {
            "words": sorted(words[iid]),
            "labels": sorted(labels[iid]),
            "places": sorted(places[iid]),
            "receipts": sorted(receipts[iid]),
            "barcodes": sorted(barcodes[iid]),
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


def _partition_types(ddb, table: str, image_id: str) -> set:
    """Distinct entity TYPEs under a prod image partition (read-only)."""
    types, lek = set(), None
    while True:
        kwargs = dict(
            TableName=table,
            KeyConditionExpression="PK = :pk",
            ExpressionAttributeValues={":pk": {"S": f"IMAGE#{image_id}"}},
            ProjectionExpression="#T",
            ExpressionAttributeNames={"#T": "TYPE"},
        )
        if lek:
            kwargs["ExclusiveStartKey"] = lek
        resp = ddb.query(**kwargs)
        for item in resp["Items"]:
            t = item.get("TYPE", {}).get("S")
            if t:
                types.add(t)
        lek = resp.get("LastEvaluatedKey")
        if not lek:
            break
    return types


def guard_replaces(prod_table: str, replaces: list, max_workers: int = 16) -> tuple:
    """Split REPLACE ids into safe (only restorable types) and guarded (would
    lose data). A guarded image is left untouched, not deleted. Queries run in
    parallel over a shared boto3 client (clients are thread-safe)."""
    ddb = boto3.client("dynamodb", region_name="us-east-1")
    safe, guarded = [], []

    def _check(iid):
        return iid, sorted(_partition_types(ddb, prod_table, iid) - RESTORABLE_TYPES)

    with ThreadPoolExecutor(max_workers=max_workers) as ex:
        for iid, bad in ex.map(_check, replaces):
            if bad:
                guarded.append((iid, bad))
            else:
                safe.append(iid)
    return safe, guarded


def apply_plan(p: dict, dev_client, prod_client, dev_config, prod_config):
    """Export sources FIRST, then DELETE (pure + replace), then copy.

    Exporting before any destructive delete means a transient dev-read failure
    aborts the run before prod data is removed, rather than leaving prod missing
    the image until a successful rerun.
    """
    dev_table = dev_config["table"]
    to_copy = p["add"] + p["replace"]
    to_delete = p["delete"] + p["replace"]

    tmp = Path(tempfile.mkdtemp(prefix="reconcile_export_"))
    try:
        # 1. Export every add/replace source up front (no prod writes yet).
        if to_copy:
            logger.info(f"Exporting {len(to_copy)} dev images → {tmp}...")
            for iid in to_copy:
                export_image(dev_table, iid, str(tmp))

        # 2. Now it is safe to delete (pure deletes + the delete half of replaces).
        # Parallelized — each image is an independent partition and each cascade
        # delete removes thousands of child items, so serial deletes dominate the
        # wall clock on a large run.
        logger.info(f"Deleting {len(to_delete)} prod images (pure + replace)...")

        def _del(iid):
            res = prod_client.delete_image_details(iid)
            return iid, (sum(res.values()) if res else 0)

        done = 0
        with ThreadPoolExecutor(max_workers=16) as ex:
            for iid, n in ex.map(_del, to_delete):
                done += 1
                if done % 25 == 0 or done == len(to_delete):
                    logger.info(f"  deleted {done}/{len(to_delete)} (last: {iid} {n} items)")

        if not to_copy:
            logger.info("No images to add/replace.")
            return
        logger.info("Copying to prod...")
        stats = copy_all_images(
            tmp,
            prod_client,
            dev_config,
            prod_config,
            dry_run=False,
            # The plan already decided exactly what to copy (adds don't exist,
            # replaces were just deleted). skip_existing would re-check via an
            # eventually-consistent read and could wrongly skip a just-deleted
            # REPLACE image, leaving prod missing it — so force the copy.
            skip_existing=False,
            skip_empty=True,
        )
        logger.info(
            f"Copied: {stats['copied']}, skipped: {stats['skipped']}, "
            f"skipped_empty: {stats['skipped_empty']}, failed: {stats['failed']}"
        )
        # The REPLACE set was already deleted from prod above, so an incomplete
        # copy leaves prod missing those images. Fail loudly instead of reporting
        # success — every add/replace image has receipts, so all must copy.
        if stats["copied"] != len(to_copy) or stats["failed"]:
            raise RuntimeError(
                f"Incomplete copy: expected {len(to_copy)} copied, got "
                f"{stats['copied']} (failed={stats['failed']}, "
                f"skipped={stats['skipped']}, skipped_empty={stats['skipped_empty']}). "
                f"REPLACE images were already deleted from prod — re-run to repair. "
                f"Errors: {stats['errors'][:5]}"
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
    ap.add_argument(
        "--protect",
        default="",
        help="Comma-separated image_ids to NEVER delete (kept in prod even if "
        "absent from dev). Use to hold prod-only images back for review.",
    )
    args = ap.parse_args()
    protected = {x.strip() for x in args.protect.split(",") if x.strip()}

    logger.info("Fingerprinting dev...")
    dev_client = DynamoClient(load_env("dev")["dynamodb_table_name"])
    dev_fp = _fingerprint_env(dev_client)
    logger.info("Fingerprinting prod...")
    prod_client = DynamoClient(load_env("prod")["dynamodb_table_name"])
    prod_fp = _fingerprint_env(prod_client)

    p = plan(dev_fp, prod_fp)

    # Safety guard: never delete a prod image whose partition holds entity types
    # the copy path cannot restore. Such images are left untouched and reported.
    prod_table = load_env("prod")["dynamodb_table_name"]
    safe_replace, guarded = guard_replaces(prod_table, p["replace"])
    p["replace"] = safe_replace

    # Hold protected images back from deletion (kept in prod for later review).
    if protected:
        held = [i for i in p["delete"] if i in protected]
        p["delete"] = [i for i in p["delete"] if i not in protected]
        if held:
            logger.warning(f"  PROTECTED from delete ({len(held)}): {held}")

    logger.info("=" * 60)
    logger.info("RECONCILE PLAN (dev → prod mirror)")
    logger.info("=" * 60)
    logger.info(f"  ADD     (new dev images):        {len(p['add'])}")
    logger.info(f"  REPLACE (content changed):       {len(p['replace'])}")
    logger.info(f"  DELETE  (gone/empty on dev):     {len(p['delete'])}")
    logger.info(f"  skipped (empty dev images):      {len(p['empty_skipped'])}")
    if guarded:
        logger.warning(
            f"  GUARDED (unrestorable types, left as-is): {len(guarded)}"
        )
        for iid, bad in guarded[: args.limit_list]:
            logger.warning(f"    {iid}: would lose {bad}")
    for bucket in ("add", "replace", "delete"):
        if p[bucket]:
            shown = p[bucket][: args.limit_list]
            logger.info(f"  {bucket}: {shown}{' ...' if len(p[bucket]) > len(shown) else ''}")

    if not (p["add"] or p["replace"] or p["delete"] or guarded):
        logger.info("\n✅ prod already matches dev. Nothing to do.")
        return

    if not (p["add"] or p["replace"] or p["delete"]) and guarded:
        # Only guarded diffs remain — nothing can be safely applied, but prod is
        # NOT in sync. Report it as incomplete rather than "matches".
        logger.error(
            f"\n❌ Nothing safely actionable, but {len(guarded)} image(s) are "
            f"guarded and remain stale in prod. Resolve their unrestorable types."
        )
        sys.exit(2)

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
    if guarded:
        logger.error(
            f"\n⚠️  INCOMPLETE: {len(guarded)} image(s) were guarded and left "
            f"stale in prod (unrestorable entity types). Exiting nonzero so the "
            f"promotion is not reported as fully successful."
        )
        sys.exit(2)


if __name__ == "__main__":
    main()
