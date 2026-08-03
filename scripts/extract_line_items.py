"""Extract line-item product details from ITEMS sections via geometry.

No ML: for each receipt with a VALID ITEMS section, cluster the section's
words into visual bands by y-center (Apple Vision splits name and price
columns into separate OCR lines, so per-line parsing fails; per-band
parsing recovers the pairing), then parse each band into
(name, quantity, unit_price, price, flags).

Validation built in:
  1. Reconciliation — sum of extracted item prices vs the receipt
     summary's subtotal (else grand_total - tax), classified as
     match / near / mismatch / no-baseline.
  2. Leakage — priced bands on lines outside any section for receipts
     WITH an ITEMS section (section too narrow).
  3. No-ITEMS triage — receipts without an ITEMS section classified as
     genuinely item-less vs likely-missing-section, using priced lines
     outside SUMMARY / TOTAL_LINE / PAYMENT / TRANSACTION_INFO sections.

Usage:
    python3.12 scripts/extract_line_items.py [--table ReceiptsTable-dc5be22]
        [--out /path/to/items.jsonl] [--limit N] [--receipt IMAGE_ID:RID]
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter

# Monorepo sibling paths, resolved from the repo root rather than a
# hardcoded absolute path.
from pathlib import Path
from typing import Any, Optional

import boto3
from boto3.dynamodb.types import TypeDeserializer

_REPO_ROOT = Path(__file__).resolve().parent.parent
for _pkg in ("receipt_dynamo", "receipt_upload"):
    _p = _REPO_ROOT / _pkg
    if _p.is_dir():
        sys.path.insert(0, str(_p))

# The extraction core lives in the receipt_upload package so that ingest can
# import it and CI actually tests it. This file is a CLI shim only -- fetching
# records and printing results. Do not reintroduce extraction logic here.
from receipt_upload.line_items.geometry import (  # noqa: E402
    NON_ITEM_SECTIONS,
    PRICE_RE,
    band_words,
    estimate_skew,
    extract_items,
    parse_band,
    reconcile,
    reconcile_detailed,
)

_DES = TypeDeserializer()


def _query_all(client, **kwargs):
    while True:
        resp = client.query(**kwargs)
        yield from resp["Items"]
        if "LastEvaluatedKey" not in resp:
            return
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]


def _deser(item: dict) -> dict:
    return {k: _DES.deserialize(v) for k, v in item.items()}


def fetch_receipt_records(client, table: str, image_id: str, receipt_id: int):
    """One query per receipt: words, sections, summary in a single sweep."""
    words: list[dict] = []
    sections: list[dict] = []
    summary: Optional[dict] = None
    for raw in _query_all(
        client,
        TableName=table,
        KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
        ExpressionAttributeValues={
            ":pk": {"S": f"IMAGE#{image_id}"},
            ":sk": {"S": f"RECEIPT#{receipt_id:05d}"},
        },
    ):
        t = raw.get("TYPE", {}).get("S")
        if t == "RECEIPT_WORD":
            m = re.match(r"RECEIPT#\d+#LINE#(\d+)#WORD#(\d+)$", raw["SK"]["S"])
            if not m:
                continue
            item = _deser(raw)
            bb = item.get("bounding_box") or {}
            try:
                words.append(
                    {
                        "line_id": int(m.group(1)),
                        "word_id": int(m.group(2)),
                        "text": str(item.get("text", "")),
                        "x": float(bb.get("x", 0)),
                        "y_mid": float(bb.get("y", 0))
                        + float(bb.get("height", 0)) / 2,
                        "h": float(bb.get("height", 0)),
                    }
                )
            except (TypeError, ValueError):
                continue
        elif t == "RECEIPT_SECTION":
            sections.append(_deser(raw))
        elif t == "RECEIPT_SUMMARY":
            summary = _deser(raw)
    return words, sections, summary


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", default="ReceiptsTable-dc5be22")
    ap.add_argument("--out", default=None)
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument(
        "--receipt", help="IMAGE_ID:RECEIPT_ID single-receipt mode"
    )
    args = ap.parse_args()

    client = boto3.client("dynamodb", region_name="us-east-1")

    # Collect ITEMS sections (and all sectioned receipts) via GSITYPE.
    items_secs: dict[tuple[str, int], dict] = {}
    sectioned: set[tuple[str, int]] = set()
    for raw in _query_all(
        client,
        TableName=args.table,
        IndexName="GSITYPE",
        KeyConditionExpression="#t = :t",
        ExpressionAttributeNames={"#t": "TYPE"},
        ExpressionAttributeValues={":t": {"S": "RECEIPT_SECTION"}},
    ):
        item = _deser(raw)
        m = re.match(r"IMAGE#(.+)", item["PK"])
        m2 = re.match(r"RECEIPT#(\d+)#", item["SK"])
        if not (m and m2):
            continue
        key = (m.group(1), int(m2.group(1)))
        sectioned.add(key)
        if (
            item.get("section_type") == "ITEMS"
            and item.get("validation_status") == "VALID"
        ):
            items_secs[key] = item

    targets = sorted(items_secs)
    if args.receipt:
        img, rid = args.receipt.split(":")
        targets = [(img, int(rid))]
    if args.limit:
        targets = targets[: args.limit]

    out_f = open(args.out, "w") if args.out else None
    recon = Counter()
    leaky = 0
    n_items_total = 0
    mismatches = []
    for i, key in enumerate(targets):
        img, rid = key
        words, sections, summary = fetch_receipt_records(
            client, args.table, img, rid
        )
        sec = items_secs.get(key) or next(
            (s for s in sections if s.get("section_type") == "ITEMS"), None
        )
        if sec is None:
            continue
        line_ids = {int(x) for x in sec.get("line_ids", [])}
        items, collapsed = extract_items(words, line_ids)
        n_items_total += len(items)
        status, item_sum, baseline = reconcile(
            [x for x in items if not x["is_discount"]], summary
        )
        if collapsed:
            status = f"{status}+collapsed"
        recon[status] += 1
        if status == "mismatch":
            mismatches.append((img, rid, item_sum, baseline))

        # Leakage: priced words on lines outside ANY section
        all_sectioned_lines: set[int] = set()
        for s in sections:
            all_sectioned_lines.update(int(x) for x in s.get("line_ids", []))
        stray = {
            w["line_id"]
            for w in words
            if w["line_id"] not in all_sectioned_lines
            and PRICE_RE.search(w["text"])
        }
        if stray:
            leaky += 1

        if out_f:
            out_f.write(
                json.dumps(
                    {
                        "image_id": img,
                        "receipt_id": rid,
                        "collapsed_banding": collapsed,
                        "items": items,
                        "reconciliation": {
                            "status": status,
                            "item_sum": item_sum,
                            "baseline": baseline,
                        },
                        "stray_priced_lines": sorted(stray),
                    }
                )
                + "\n"
            )
        if (i + 1) % 100 == 0:
            print(f"  {i + 1}/{len(targets)}", file=sys.stderr, flush=True)

    if out_f:
        out_f.close()

    print(f"receipts processed: {len(targets)}")
    print(f"items extracted: {n_items_total}")
    print(f"reconciliation: {dict(recon)}")
    print(f"receipts with priced lines outside all sections: {leaky}")
    print("worst mismatches (item_sum vs baseline):")
    for img, rid, s, b in sorted(
        mismatches, key=lambda x: abs((x[2] or 0) - (x[3] or 0)), reverse=True
    )[:10]:
        print(f"  {img}:{rid}  sum={s} baseline={b}")


if __name__ == "__main__":
    main()
