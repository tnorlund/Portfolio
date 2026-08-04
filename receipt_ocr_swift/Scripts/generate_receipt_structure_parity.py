#!/usr/bin/env python3
"""Generate end-to-end rows -> sections -> line-items expectations."""

from __future__ import annotations

import json
import re
from pathlib import Path

from generate_section_parity import reconstruct

from receipt_chroma.embedding.formatting import build_receipt_rows
from receipt_dynamo.amounts import (
    looks_like_receipt_amount,
    parse_receipt_amount,
)
from receipt_upload.line_items.blocks import should_reocr_items_zone
from receipt_upload.line_items.geometry import extract_items, reconcile
from receipt_upload.section_assignment import (
    assign_row_sections,
    load_prior_model,
    sections_from_assignments,
)

_SUBTOTAL_RE = re.compile(r"\bSUB\s*TOTAL\b", re.IGNORECASE)


def _printed_subtotal(rows, lines, words) -> float | None:
    lines_by_id = {line.line_id: line for line in lines}
    for row in rows:
        text = " ".join(lines_by_id[line_id].text for line_id in row.line_ids)
        if not _SUBTOTAL_RE.search(text):
            continue
        amount = parse_receipt_amount(row.amount_text)
        if amount is not None and amount > 0:
            return amount
        row_ids = set(row.line_ids)
        candidates = sorted(
            (
                word
                for word in words
                if word.line_id in row_ids
                and looks_like_receipt_amount(word.text)
            ),
            key=lambda word: (
                word.bounding_box["x"],
                word.line_id,
                word.word_id,
            ),
        )
        for word in reversed(candidates):
            amount = parse_receipt_amount(word.text)
            if amount is not None and amount > 0:
                return amount
    return None


def main() -> None:
    script_dir = Path(__file__).resolve().parent
    package_dir = script_dir.parent
    repo_root = package_dir.parent
    input_path = (
        repo_root / "receipt_upload/tests/fixtures/line_items_golden_ocr.json"
    )
    output_path = (
        package_dir
        / "Tests/ReceiptOCRCoreTests/Fixtures/receipt_structure_parity_expected.json"
    )
    fixture = json.loads(input_path.read_text(encoding="utf-8"))
    model = load_prior_model()
    expected = []
    for receipt in fixture["receipts"]:
        lines, words = reconstruct(receipt)
        rows = build_receipt_rows(lines, words)
        assignments = assign_row_sections(
            rows, lines, model, receipt.get("merchant")
        )
        sections = sections_from_assignments(assignments)
        items_lines = next(
            (
                set(section.line_ids)
                for section in sections
                if section.section_type == "ITEMS"
            ),
            set(),
        )
        items, _ = extract_items(receipt["words"], items_lines)
        subtotal = _printed_subtotal(rows, lines, words)
        status, _, _ = reconcile(
            [item for item in items if not item.get("is_discount")],
            {"subtotal": subtotal} if subtotal is not None else None,
        )
        expected.append(
            {
                "image_id": receipt["image_id"],
                "receipt_id": receipt["receipt_id"],
                "sections": [
                    {
                        "section_type": section.section_type,
                        "line_ids": section.line_ids,
                    }
                    for section in sections
                ],
                "line_items": [
                    {
                        "item_index": index,
                        "name": item["name"],
                        "price": item["price"],
                        "quantity": item.get("quantity"),
                        "unit_price": item.get("unit_price"),
                        "is_discount": bool(item.get("is_discount")),
                        "name_quality": item.get("name_quality") or "ok",
                        "line_ids": item["line_ids"],
                        "reconciliation_status": status,
                    }
                    for index, item in enumerate(items)
                ],
                "printed_subtotal": subtotal,
                "reconciliation_status": status,
                "should_reocr_items_zone": should_reocr_items_zone(
                    items, subtotal
                ),
            }
        )

    # The golden set grows; derive the count instead of pinning it.
    if len(expected) != len(fixture["receipts"]):
        raise RuntimeError(
            f"expected {len(fixture['receipts'])} receipts, "
            f"got {len(expected)}"
        )
    output_path.write_text(
        json.dumps(expected, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(expected)} receipts to {output_path}")


if __name__ == "__main__":
    main()
