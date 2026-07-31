"""Backfill RECEIPT_LINE_ITEM rows from the geometric extractor.

For every receipt with a VALID ITEMS section, runs the extractor live
(words + section line_ids from the current table state), builds
ReceiptLineItem entities with provenance + trust signals, and persists
them via the idempotent delete-then-put rewrite
(delete_receipt_line_items_for_receipt + add_receipt_line_items).

Re-running is safe: same input -> same items -> byte-identical rows.

Usage:
    python3.12 scripts/backfill_receipt_line_items.py [--dry-run only by
        default; pass --apply to write] [--table ReceiptsTable-dc5be22]
        [--receipt IMAGE_ID:RID]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from collections import Counter
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, "/Users/tnorlund/Portfolio/receipt_dynamo")

import boto3  # noqa: E402
from boto3.dynamodb.types import TypeDeserializer  # noqa: E402
from extract_line_items import (  # noqa: E402
    extract_items,
    fetch_receipt_records,
    reconcile_detailed,
)
from receipt_dynamo.data.dynamo_client import DynamoClient  # noqa: E402
from receipt_dynamo.entities.receipt_line_item import (  # noqa: E402
    ReceiptLineItem,
)

DEV_TABLE = "ReceiptsTable-dc5be22"
PROD_MARKER = "d7ff76a"
EXTRACTOR_VERSION = "line-items-blocks-v2"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", default=DEV_TABLE)
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--receipt", help="IMAGE_ID:RID single-receipt mode")
    args = ap.parse_args()

    if PROD_MARKER in args.table:
        sys.exit("REFUSED: this script never writes to the prod table.")

    client = boto3.client("dynamodb", region_name="us-east-1")
    dynamo = DynamoClient(args.table)
    des = TypeDeserializer()

    # Every receipt with a VALID ITEMS section (extraction population)
    targets: dict[tuple[str, int], dict] = {}
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
            if (
                item.get("section_type") == "ITEMS"
                and item.get("validation_status") == "VALID"
            ):
                m = re.match(r"IMAGE#(.+)", item["PK"])
                m2 = re.match(r"RECEIPT#(\d+)#", item["SK"])
                if m and m2:
                    targets[(m.group(1), int(m2.group(1)))] = item
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]

    keys = sorted(targets)
    if args.receipt:
        img, rid = args.receipt.split(":")
        keys = [(img, int(rid))]

    stats: Counter = Counter()
    now = datetime.now(timezone.utc)
    for n, (img, rid) in enumerate(keys):
        sec = targets.get((img, rid))
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
        if args.apply:
            deleted = dynamo.delete_receipt_line_items_for_receipt(img, rid)
            if entities:
                dynamo.add_receipt_line_items(entities)
            stats["deleted"] += deleted
            stats["written"] += len(entities)
        if (n + 1) % 100 == 0:
            print(f"  {n + 1}/{len(keys)}", file=sys.stderr, flush=True)

    mode = "APPLY" if args.apply else "dry-run"
    print(f"[{mode}] {dict(stats)}")


if __name__ == "__main__":
    main()
