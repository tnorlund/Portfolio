#!/usr/bin/env python3
"""Recon + PROVEN census — the per-re-OCR-wave canary.

For every receipt in the table: extract items from its ITEMS section
(same decoder the pipeline uses), reconcile against the stored summary,
and fold in the bank hop (``is_proven``). Prints the recon distribution
and the PROVEN count.

WHY THIS EXISTS: #1356 made re-OCR write measured tilt, so overlays mix
rotated quads into receipts whose remaining words are axis-aligned from
the old code. The mixed-geometry safety evidence is thin (n=1,
``492f9ae1`` improved). Until it isn't, run this after each re-OCR wave
and compare against the previous run — a DROP in ``match`` is the signal
that mixed geometry (or any other regression) is degrading decoding, and
the write-tilt-only-on-whole-receipt-re-reads policy becomes the fallback.

Reference points (2026-08-04, post smart-re-OCR landing):
    prod (d7ff76a): match 489 · PROVEN 302/823
    dev  (dc5be22): match 512 · PROVEN 302/825

Usage:
    python scripts/recon_census.py --table ReceiptsTable-dc5be22
    python scripts/recon_census.py --table ReceiptsTable-d7ff76a
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

import boto3

_REPO_ROOT = Path(__file__).resolve().parent.parent
for _pkg in ("receipt_dynamo", "receipt_upload"):
    _p = _REPO_ROOT / _pkg
    if _p.is_dir():
        sys.path.insert(0, str(_p))
sys.path.insert(0, str(_REPO_ROOT / "scripts"))

from extract_line_items import (  # noqa: E402
    _deser,
    _query_all,
    fetch_receipt_records,
)

from receipt_upload.line_items.geometry import (  # noqa: E402
    extract_items,
    is_proven,
    reconcile,
)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", default="ReceiptsTable-dc5be22")
    args = ap.parse_args()

    client = boto3.client("dynamodb", region_name="us-east-1")

    receipts = set()
    for raw in _query_all(
        client,
        TableName=args.table,
        IndexName="GSITYPE",
        KeyConditionExpression="#t = :t",
        ExpressionAttributeNames={"#t": "TYPE"},
        ExpressionAttributeValues={":t": {"S": "RECEIPT_SUMMARY"}},
    ):
        item = _deser(raw)
        receipts.add((item["PK"].split("#")[1], int(item["SK"].split("#")[1])))

    recon_counts: Counter = Counter()
    proven = 0
    total = 0
    for img, rid in sorted(receipts):
        total += 1
        try:
            words, sections, summary = fetch_receipt_records(
                client, args.table, img, rid
            )
            sec = next(
                (s for s in sections if s.get("section_type") == "ITEMS"),
                None,
            )
            if sec is None or not words:
                recon_counts["none"] += 1
                continue
            line_ids = {int(x) for x in sec.get("line_ids", [])}
            items, _collapsed = extract_items(words, line_ids, summary)
            status, _, _ = reconcile(
                [x for x in items if not x["is_discount"]], summary
            )
            recon_counts[status or "none"] += 1
            gt = summary.get("grand_total") if summary else None
            bank = summary.get("bank_amount") if summary else None
            if is_proven(
                status,
                float(gt) if gt is not None else None,
                float(bank) if bank is not None else None,
            ):
                proven += 1
        except Exception as exc:  # noqa: BLE001 - census must not die mid-scan
            recon_counts[f"error:{type(exc).__name__}"] += 1

    print(f"table={args.table} receipts={total}")
    print("recon:", dict(recon_counts.most_common()))
    print(f"PROVEN: {proven}/{total}")


if __name__ == "__main__":
    main()
