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


def _strip_target_labels(
    word: dict,
    targets: set[tuple],
    *,
    image_id=None,
    receipt_id=None,
    line_id=None,
) -> int:
    labels = word.get("labels")
    if not isinstance(labels, list) or not labels:
        return 0

    next_labels = []
    removed = 0
    for label in labels:
        try:
            word_image_id = word.get("image_id")
            word_receipt_id = word.get("receipt_id")
            word_line_id = word.get("line_id")
            key = (
                str(word_image_id if word_image_id is not None else image_id),
                int(word_receipt_id if word_receipt_id is not None else receipt_id),
                int(word_line_id if word_line_id is not None else line_id),
                int(word.get("word_id")),
                str(label),
            )
        except (TypeError, ValueError):
            next_labels.append(label)
            continue
        if key in targets:
            removed += 1
            continue
        next_labels.append(label)

    if removed:
        word["labels"] = next_labels
    return removed


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
    embedded_changed = 0
    for wl in payload.get("receipt_word_labels") or []:
        try:
            key = _key(wl)
        except (TypeError, ValueError):
            continue
        if key in targets and str(wl.get("validation_status") or "").upper() == "VALID":
            wl["validation_status"] = "INVALID"
            changed += 1

    for word in payload.get("receipt_words") or []:
        if isinstance(word, dict):
            embedded_changed += _strip_target_labels(word, targets)

    for receipt in payload.get("receipts") or []:
        if not isinstance(receipt, dict):
            continue
        image_id = receipt.get("image_id")
        receipt_id = receipt.get("receipt_id")
        for line in receipt.get("lines") or []:
            if not isinstance(line, dict):
                continue
            for word in line.get("words") or []:
                if isinstance(word, dict):
                    embedded_changed += _strip_target_labels(
                        word,
                        targets,
                        image_id=image_id,
                        receipt_id=receipt_id,
                        line_id=line.get("line_id"),
                    )
    json.dump(payload, open(args.export, "w"))
    print(
        f"mirrored {changed}/{len(targets)} flips into {args.export}; "
        f"removed {embedded_changed} embedded labels"
    )


if __name__ == "__main__":
    main()
