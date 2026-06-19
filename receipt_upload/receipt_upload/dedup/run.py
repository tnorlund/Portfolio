"""Load receipts from DynamoDB and report Tier 0 + Tier 1 duplicates.

Usage: python -m receipt_upload.dedup.run --env dev [--json report.json]
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from receipt_dynamo import DynamoClient

from receipt_upload.dedup.detector import detect_duplicates, normalize_merchant_key

ENV_TABLE = {"dev": "ReceiptsTable-dc5be22", "prod": "ReceiptsTable-d7ff76a"}


def build_report(table: str) -> dict:
    dc = DynamoClient(table)
    receipts = dc.list_receipts()[0]

    # Merchant identity: prefer the canonical Google place_id (most stable).
    merchant = {}
    for m in dc.list_receipt_metadatas()[0]:
        merchant[(m.image_id, m.receipt_id)] = normalize_merchant_key(
            getattr(m, "canonical_place_id", None) or getattr(m, "place_id", None),
            getattr(m, "canonical_merchant_name", None)
            or getattr(m, "merchant_name", None),
        )

    # Signature = merchant | grand_total | date | item_count (all required).
    signature_lookup = {}
    for s in dc.list_receipt_summaries()[0]:
        su = getattr(s, "summary", s)
        key = (su.image_id, su.receipt_id)
        grand_total = getattr(su, "grand_total", None)
        date = getattr(su, "date", None)
        item_count = getattr(su, "item_count", None)
        mkey = merchant.get(key) or normalize_merchant_key(
            None, getattr(su, "merchant_name", None)
        )
        # Require a real (>0) total — a $0.00 total is an unparsed value, not a
        # reliable key, and would falsely group same-merchant same-day receipts.
        if mkey and grand_total and float(grand_total) > 0 and date is not None:
            dstr = date.date().isoformat() if hasattr(date, "date") else str(date)[:10]
            signature_lookup[key] = (
                f"{mkey}|{round(float(grand_total), 2)}|{dstr}|{item_count}"
            )

    return detect_duplicates(receipts, signature_lookup)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", choices=list(ENV_TABLE), default="dev")
    ap.add_argument("--json", help="write the full report to this JSON path")
    ap.add_argument("--samples", type=int, default=5)
    args = ap.parse_args()

    rep = build_report(ENV_TABLE[args.env])
    s = rep["summary"]
    print(f"[{args.env}] {rep['total_receipts']} receipts")
    print(
        f"  Tier 0 (exact, AUTO-MERGE):   {s['exact_groups']} groups, "
        f"{s['exact_redundant_receipts']} redundant receipts"
    )
    print(
        f"  Tier 1 (signature, REVIEW):   {s['signature_candidate_groups']} groups, "
        f"{s['signature_candidate_receipts']} candidate receipts"
    )

    print(f"\n  sample Tier-0 exact groups:")
    for g in rep["exact_groups"][: args.samples]:
        print(f"    keep {g.keeper} <- dup {g.duplicates}  ({g.signature})")
    print(f"\n  sample Tier-1 signature candidates (REVIEW):")
    for g in rep["signature_candidate_groups"][: args.samples]:
        print(f"    keep {g.keeper} <- {g.duplicates}  sig={g.signature}")

    if args.json:
        out = {
            "summary": rep["summary"],
            "total_receipts": rep["total_receipts"],
            "exact_groups": [asdict(g) for g in rep["exact_groups"]],
            "signature_candidate_groups": [
                asdict(g) for g in rep["signature_candidate_groups"]
            ],
        }
        with open(args.json, "w") as f:
            json.dump(out, f, indent=2, default=list)
        print(f"\n  wrote {args.json}")


if __name__ == "__main__":
    main()
