"""Backfill (and audit) RECEIPT_LINE_ITEM rows from the geometric extractor.

For every receipt the line-item writer would extract from, runs the
extractor live (words + section line_ids from the current table state),
builds ReceiptLineItem entities with provenance + trust signals, and
persists them via the idempotent delete-then-put rewrite
(delete_receipt_line_items_for_receipt + add_receipt_line_items).

Re-running is safe: same input -> same items -> byte-identical rows.

WHY THIS SCRIPT MATTERS: nothing recomputes line items when the DECODER
changes. The stream stage (infra/receipt_line_item_updater) fires on a
summary write, so a decoder fix leaves every untouched receipt carrying
the verdict the old code produced -- indistinguishable from a fresh row,
because ``extractor_version`` names the algorithm family and does not
move with behaviour. This is the corpus recompute path, and ``--check``
is the detector that says how many stored verdicts have gone stale.

Target population: any receipt with a non-INVALID ITEMS section,
preferring VALID when a receipt somehow has more than one -- exactly
what ``line_item_processor.update_receipt_line_items`` picks. It used to
demand ``validation_status == "VALID"``, which is *nearly the whole
corpus in dev and almost nothing in prod*: measured 2026-08-05, prod
carries 729 PENDING ITEMS sections and 1 VALID, so the recompute path
could reach one prod receipt out of 730 (dev: 667 VALID, 17 PENDING,
7 INVALID). A recompute that cannot reach the rows is not a recompute
path.

WHAT ``--check`` CANNOT SEE, because two harnesses now measure this
corpus and neither is a superset of the other. This one compares STORED
against LIVE, so it is blind to any receipt with no stored rows at all
-- 28 in prod, 25 in dev (2026-08-05) have an ITEMS section and zero
RECEIPT_LINE_ITEM rows, and they are reported only as a
``no-stored-rows`` count, never as drift. A LIVE-ONLY sweep sees those
and is in turn blind to the stored column: prod 916d7955 stores
``no-baseline``, which says its rows were written before the receipt had
a usable printed baseline, and no amount of recomputing can recover that
fact. Use both, and do not read either one's silence as coverage.

Usage:
    # read-only drift report (works against prod)
    python3.13 scripts/backfill_receipt_line_items.py --check \
        --table ReceiptsTable-d7ff76a

    # dry run / write, dev only
    python3.13 scripts/backfill_receipt_line_items.py
    python3.13 scripts/backfill_receipt_line_items.py --apply
    python3.13 scripts/backfill_receipt_line_items.py \
        --receipt IMAGE_ID:RID --apply
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Optional

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import boto3  # noqa: E402
from boto3.dynamodb.types import TypeDeserializer  # noqa: E402
from extract_line_items import (  # noqa: E402
    extract_items,
    fetch_receipt_records,
    reconcile_detailed,
)

# isort: off
# The repo has no root isort config, so whether receipt_dynamo and
# receipt_upload land in one import block or two depends on which
# package's pyproject the linter happens to resolve, and the two CI lint
# jobs disagree. Fenced so neither can reorder them (same fence, same
# reason, as scripts/backfill_decoder_word_labels.py).
from receipt_dynamo.data.dynamo_client import DynamoClient  # noqa: E402
from receipt_dynamo.entities.receipt_line_item import (  # noqa: E402
    ReceiptLineItem,
)
from receipt_upload.line_items.provenance import (  # noqa: E402
    is_worker_extractor_version,
)

# isort: on

DEV_TABLE = "ReceiptsTable-dc5be22"
PROD_MARKER = "d7ff76a"
EXTRACTOR_VERSION = "line-items-blocks-v2"


def select_items_section(sections: list[dict]) -> Optional[dict]:
    """The ITEMS section the line-item writer would extract from.

    Mirrors ``line_item_processor.update_receipt_line_items``: any
    non-INVALID canonical ITEMS section, preferring VALID over PENDING so
    provenance reflects the strongest source. Legacy ITEMS_VALUE /
    ITEMS_DESCRIPTION zones are partial (prices-only or names-only) and
    are excluded by the exact ``ITEMS`` match.
    """
    picked: Optional[dict] = None
    for section in sections:
        if str(section.get("section_type") or "").upper() != "ITEMS":
            continue
        status = str(section.get("validation_status") or "").upper()
        if status == "INVALID":
            continue
        if picked is None or status == "VALID":
            picked = section
        if status == "VALID":
            break
    return picked


def _stored_line_items(client, table: str, image_id: str, receipt_id: int):
    """The RECEIPT_LINE_ITEM rows currently stored for one receipt."""
    des = TypeDeserializer()
    rows: list[dict] = []
    kwargs: dict[str, Any] = dict(
        TableName=table,
        KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
        ExpressionAttributeValues={
            ":pk": {"S": f"IMAGE#{image_id}"},
            ":sk": {"S": f"RECEIPT#{receipt_id:05d}#LINE_ITEM#"},
        },
    )
    while True:
        resp = client.query(**kwargs)
        rows.extend(
            {k: des.deserialize(v) for k, v in raw.items()}
            for raw in resp["Items"]
        )
        if "LastEvaluatedKey" not in resp:
            return rows
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def _stored_status(rows: list[dict]) -> Optional[str]:
    """The reconciliation verdict the stored rows agree on.

    Every row of a receipt is written in one pass with one verdict, so
    disagreement means the rows were written by two different runs --
    reported as-is rather than silently collapsed.
    """
    statuses = {r.get("reconciliation_status") for r in rows}
    if not statuses:
        return None
    if len(statuses) > 1:
        return "|".join(sorted(str(s) for s in statuses))
    return statuses.pop()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", default=DEV_TABLE)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument(
        "--check",
        action="store_true",
        help=(
            "read-only: report every receipt whose STORED "
            "reconciliation_status disagrees with a live recompute "
            "(decoder drift). Never writes, so prod is allowed."
        ),
    )
    ap.add_argument(
        "--replace-worker-rows",
        action="store_true",
        help=(
            "also rewrite receipts whose stored rows were decoded on "
            "device by the Mac worker. Off by default: the stream stage "
            "preserves a worker decode when it reconciles better "
            "(_reconcile_with_worker_rows), and this script's blind "
            "delete-then-put would discard it."
        ),
    )
    ap.add_argument("--receipt", help="IMAGE_ID:RID single-receipt mode")
    args = ap.parse_args()

    if PROD_MARKER in args.table and args.apply:
        sys.exit("REFUSED: this script never writes to the prod table.")

    client = boto3.client("dynamodb", region_name="us-east-1")
    dynamo = DynamoClient(args.table) if args.apply else None
    des = TypeDeserializer()

    # Every receipt the writer would extract from (extraction population).
    candidates: dict[tuple[str, int], list[dict]] = {}
    kwargs = dict(
        TableName=args.table,
        IndexName="GSITYPE",
        KeyConditionExpression="#t = :t",
        ExpressionAttributeNames={"#t": "TYPE"},
        ExpressionAttributeValues={":t": {"S": "RECEIPT_SECTION"}},
    )
    while True:
        resp = client.query(**kwargs)
        for raw in resp["Items"]:
            item = {k: des.deserialize(v) for k, v in raw.items()}
            if str(item.get("section_type") or "").upper() != "ITEMS":
                continue
            m = re.match(r"IMAGE#(.+)", item["PK"])
            m2 = re.match(r"RECEIPT#(\d+)#", item["SK"])
            if m and m2:
                candidates.setdefault(
                    (m.group(1), int(m2.group(1))), []
                ).append(item)
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    targets: dict[tuple[str, int], dict] = {}
    for key, sections in candidates.items():
        section = select_items_section(sections)
        if section is not None:
            targets[key] = section

    keys = sorted(targets)
    if args.receipt:
        img, rid = args.receipt.split(":")
        keys = [(img, int(rid))]

    stats: Counter = Counter()
    flips: Counter = Counter()
    now = datetime.now(timezone.utc)
    for n, (img, rid) in enumerate(keys):
        sec = targets.get((img, rid))
        if sec is None:
            stats["no-items-section"] += 1
            continue
        words, _, summary = fetch_receipt_records(client, args.table, img, rid)
        line_ids = {int(x) for x in (sec.get("line_ids") or [])}
        items, collapsed = extract_items(words, line_ids, summary=summary)
        recon_result = reconcile_detailed(
            [x for x in items if not x["is_discount"]], summary
        )
        status = recon_result.status
        merchant = (summary or {}).get("merchant_name")
        entities = []
        for idx, it in enumerate(items):
            name = it.get("name") or ""
            quality = (
                "low" if it.get("name_quality") == "low" or not name else "ok"
            )
            entities.append(
                ReceiptLineItem(
                    receipt_id=rid,
                    image_id=img,
                    item_index=idx,
                    name=name,
                    price=f"{it['price']:.2f}",
                    line_ids=[int(x) for x in it["line_ids"]],
                    extractor_version=EXTRACTOR_VERSION,
                    extracted_at=now,
                    quantity=it.get("quantity"),
                    unit_price=it.get("unit_price"),
                    is_discount=bool(it.get("is_discount")),
                    raw_text=it.get("raw_text") or "",
                    name_quality=quality,
                    merchant_name=merchant,
                    source_section_status=sec.get("validation_status"),
                    source_model_source=sec.get("model_source"),
                    reconciliation_status=status,
                    collapsed_banding=bool(collapsed),
                    baseline_figures_agreeing=(
                        recon_result.baseline_figures_agreeing
                    ),
                )
            )
        stats["receipts"] += 1
        stats["items"] += len(entities)
        stats[f"recon-{status}"] += 1

        stored = (
            _stored_line_items(client, args.table, img, rid)
            if (args.check or args.apply)
            else []
        )
        worker_decoded = any(
            is_worker_extractor_version(r.get("extractor_version"))
            for r in stored
        )
        if args.check:
            if not stored:
                stats["no-stored-rows"] += 1
            else:
                before = _stored_status(stored)
                stats["stored-rows"] += 1
                if before == status:
                    stats["agree"] += 1
                else:
                    stats["drift"] += 1
                    flips[(str(before), str(status))] += 1
                    stored_sum = round(
                        sum(
                            float(r["price"])
                            for r in stored
                            if not r.get("is_discount")
                        ),
                        2,
                    )
                    print(
                        f"DRIFT {img}:{rid} {before} -> {status}  "
                        f"items {len(stored)} -> {len(entities)}  "
                        f"sum {stored_sum} -> {recon_result.item_sum}  "
                        f"baseline {recon_result.baseline}  "
                        f"worker_decoded={int(worker_decoded)}"
                    )
        if args.apply and dynamo is not None:
            if worker_decoded and not args.replace_worker_rows:
                stats["skipped-worker-decoded"] += 1
            else:
                deleted = dynamo.delete_receipt_line_items_for_receipt(
                    img, rid
                )
                if entities:
                    dynamo.add_receipt_line_items(entities)
                stats["deleted"] += deleted
                stats["written"] += len(entities)
        if (n + 1) % 100 == 0:
            print(f"  {n + 1}/{len(keys)}", file=sys.stderr, flush=True)

    mode = "APPLY" if args.apply else ("check" if args.check else "dry-run")
    print(f"[{mode}] {dict(stats)}")
    if flips:
        print(f"[{mode}] transitions {dict(flips)}")


if __name__ == "__main__":
    main()
