#!/usr/bin/env python3
"""Regenerate the hermetic OCR fixture for the line-item golden set.

Reads each golden receipt's ITEMS-section word geometry from DynamoDB and
writes ``receipt_upload/tests/fixtures/line_items_golden_ocr.json``, which
lets ``test_line_item_golden_regression.py`` run in CI with no AWS access.

Re-run only when a golden receipt's sections or OCR legitimately change
(e.g. resegmentation); the labeled truth in ``line_items_golden.json`` is
hand-verified and does NOT regenerate.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
for _pkg in ("receipt_dynamo",):
    _p = _REPO_ROOT / _pkg
    if _p.is_dir():
        sys.path.insert(0, str(_p))

from receipt_dynamo import DynamoClient  # noqa: E402

FIXTURES = _REPO_ROOT / "receipt_upload" / "tests" / "fixtures"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--table", default="ReceiptsTable-dc5be22")
    args = ap.parse_args()

    client = DynamoClient(args.table)
    golden = json.load(open(FIXTURES / "line_items_golden.json"))

    out = []
    for d in golden["receipts"]:
        key = (d["image_id"], d["receipt_id"])
        words = client.list_receipt_words_from_receipt(*key)
        sections = client.get_receipt_sections_from_receipt(*key)
        item_lines: set[int] = set()
        for s in sections:
            if "ITEM" in str(getattr(s, "section_type", "") or "").upper():
                item_lines.update(s.line_ids or [])
        out.append(
            {
                "image_id": d["image_id"],
                "receipt_id": d["receipt_id"],
                "merchant": d.get("merchant"),
                "items_line_ids": sorted(item_lines),
                "words": [
                    {
                        "line_id": w.line_id,
                        "word_id": w.word_id,
                        "text": w.text,
                        "x": round(w.bounding_box.get("x", 0.0), 6),
                        "y_mid": round(
                            w.bounding_box.get("y", 0.0)
                            + w.bounding_box.get("height", 0.0) / 2,
                            6,
                        ),
                        "h": round(w.bounding_box.get("height", 0.0), 6),
                    }
                    for w in words
                    if w.line_id in item_lines
                ],
            }
        )
        print(
            f"  {d.get('merchant', '?'):<40} "
            f"{d['image_id'][:8]}#{d['receipt_id']} words={len(out[-1]['words'])}"
        )

    doc = {
        "_README": (
            "OCR word geometry for the golden receipts' ITEMS sections, "
            f"captured {date.today().isoformat()} from {args.table} so the "
            "golden regression test runs hermetically in CI. Regenerate with "
            "scripts/export_line_item_golden_ocr.py if sections change."
        ),
        "receipts": out,
    }
    path = FIXTURES / "line_items_golden_ocr.json"
    json.dump(doc, open(path, "w"), indent=1)
    print(f"\nwrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
