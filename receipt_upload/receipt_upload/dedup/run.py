"""Report exact (raw-pixel ``sha256``) duplicate receipts.

Usage: python -m receipt_upload.dedup.run --env dev [--json report.json]

Cross-image near-duplicates (different pixels, same transaction) are detected
separately by :mod:`receipt_upload.dedup.near_dup` + ``stage5_plan``.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict

from receipt_upload.dedup.detector import find_exact_duplicates
from receipt_upload.dedup.dossiers import ENV_TABLE

from receipt_dynamo import DynamoClient


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", choices=list(ENV_TABLE), default="dev")
    ap.add_argument("--json", help="write the full report to this JSON path")
    ap.add_argument("--samples", type=int, default=5)
    args = ap.parse_args()

    dc = DynamoClient(ENV_TABLE[args.env])
    receipts = dc.list_receipts()[0]
    groups = find_exact_duplicates(receipts)
    redundant = sum(len(g.duplicates) for g in groups)

    print(f"[{args.env}] {len(receipts)} receipts")
    print(
        f"  Tier 0 (exact, AUTO-MERGE): {len(groups)} groups, "
        f"{redundant} redundant receipts"
    )
    print("\n  sample exact groups:")
    for g in groups[: args.samples]:
        print(f"    keep {g.keeper} <- dup {g.duplicates}  ({g.signature})")

    if args.json:
        out = {
            "total_receipts": len(receipts),
            "redundant_receipts": redundant,
            "exact_groups": [asdict(g) for g in groups],
        }
        with open(args.json, "w", encoding="utf-8") as f:
            json.dump(out, f, indent=2, default=list)
        print(f"\n  wrote {args.json}")


if __name__ == "__main__":
    main()
