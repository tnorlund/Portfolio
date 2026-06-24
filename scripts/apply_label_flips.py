#!/usr/bin/env python3.12
"""Mirror audited label flips into a local grouped export.

The footer-label cleanup writes VALID->INVALID to the live DynamoDB table via the
receipt-tools MCP, but the local synthesis pipeline reads the grouped export
snapshot. This helper marks the same labels INVALID in the export so synthesis
sees the cleaned data (the loader drops INVALID) without a full re-export.

By default it mirrors only the `auto_safe` tier from an audit_footer_labels.py
output. Pass --include-review-payment to also mirror review_payment entries you
have confirmed and applied to the live table.

Usage:
  python3.12 scripts/apply_label_flips.py \
      --flips ./flips.json --export ./grouped/costco_wholesale.json
"""
from __future__ import annotations

import argparse
import json


def _key(rec: dict) -> tuple:
    return (
        str(rec.get("image_id")),
        int(rec.get("receipt_id")),
        int(rec.get("line_id")),
        int(rec.get("word_id")),
        str(rec.get("label")),
    )


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--flips", required=True, help="audit_footer_labels.py output JSON")
    ap.add_argument("--export", required=True, help="grouped merchant export to patch in place")
    ap.add_argument(
        "--include-review-payment",
        action="store_true",
        help="also mirror review_payment entries (use only for confirmed flips)",
    )
    args = ap.parse_args()

    flips = json.load(open(args.flips))
    targets = set(map(_key, flips.get("auto_safe") or []))
    if args.include_review_payment:
        targets |= set(map(_key, flips.get("review_payment") or []))

    payload = json.load(open(args.export))
    changed = 0
    for wl in payload.get("receipt_word_labels") or []:
        try:
            key = _key(wl)
        except (TypeError, ValueError):
            continue
        if key in targets and str(wl.get("validation_status") or "").upper() == "VALID":
            wl["validation_status"] = "INVALID"
            changed += 1
    json.dump(payload, open(args.export, "w"))
    print(f"mirrored {changed}/{len(targets)} flips into {args.export}")


if __name__ == "__main__":
    main()
