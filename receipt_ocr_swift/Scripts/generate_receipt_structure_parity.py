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
from receipt_dynamo.entities.receipt_summary import find_printed_grand_total
from receipt_upload.line_items.blocks import should_reocr_items_zone
from receipt_upload.line_items.geometry import (
    extract_items,
    propose_items_boundary_extension,
    reconcile_detailed,
)
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


_SCRIPT_DIR = Path(__file__).resolve().parent
_PACKAGE_DIR = _SCRIPT_DIR.parent
_REPO_ROOT = _PACKAGE_DIR.parent
DEFAULT_INPUT = (
    _REPO_ROOT / "receipt_upload/tests/fixtures/line_items_golden_ocr.json"
)
DEFAULT_OUTPUT = (
    _PACKAGE_DIR
    / "Tests/ReceiptOCRCoreTests/Fixtures/receipt_structure_parity_expected.json"
)


def generate(input_path: Path = DEFAULT_INPUT) -> str:
    """The expectation file's exact bytes, without writing anything.

    Split out of ``main`` so the anti-drift gate in the receipt_upload
    matrix can regenerate and diff this fixture the same way it does the
    line-item one. It carries decoded ITEMS too, so a decoder change rots
    it just as fast -- and until this split it rotted INVISIBLY: the
    Python-side gate only covered generate_line_items_parity.py, and
    swift-ci.yml does not run on receipt_upload changes.
    """
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
        items_section = next(
            (
                section
                for section in sections
                if section.section_type == "ITEMS"
            ),
            None,
        )
        items_lines = (
            set(items_section.line_ids) if items_section else set()
        )
        subtotal = _printed_subtotal(rows, lines, words)
        # Mirrors buildOnDeviceReceiptStructure: a receipt that prints no
        # SUBTOTAL still prints a TOTAL, and the worker now anchors on it
        # via PrintedTotals.grandTotal (the port of
        # find_printed_grand_total). The subtotal still wins whenever one
        # exists, so this only reaches receipts that had no baseline at
        # all. The summary dict is always built, matching the Swift side,
        # which always constructs a LineItemSummary; an all-None dict
        # reconciles to no-baseline exactly as `None` did, and leaves the
        # summary-figure filter a no-op.
        grand_total = find_printed_grand_total(words)
        summary = {
            "subtotal": subtotal,
            "tax": None,
            "grand_total": grand_total,
        }
        # Mirrors the on-device zone-gap boundary extension (#1329): the
        # proposal is accepted only on strict arithmetic improvement, so
        # an already-matching zone always keeps its boundary.
        proposal = None
        if items_lines:
            proposal = propose_items_boundary_extension(
                receipt["words"],
                summary,
                items_lines,
                sections,
                rows,
                current_row_ids=(
                    items_section.row_ids if items_section else None
                ),
            )
        if proposal:
            items_lines = set(proposal["line_ids"])
        # Decode WITH the scanned summary so the summary-figure filter
        # (#1320) is pinned exactly as the device runs it.
        items, _ = extract_items(
            receipt["words"], items_lines, summary=summary
        )
        rec = reconcile_detailed(
            [item for item in items if not item.get("is_discount")],
            summary,
        )
        status = rec.status
        expected.append(
            {
                "image_id": receipt["image_id"],
                "receipt_id": receipt["receipt_id"],
                "sections": [
                    {
                        "section_type": section.section_type,
                        "line_ids": (
                            sorted(items_lines)
                            if proposal
                            and section.section_type == "ITEMS"
                            else section.line_ids
                        ),
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
                        "raw_text": item.get("raw_text") or "",
                    }
                    for index, item in enumerate(items)
                ],
                "printed_subtotal": subtotal,
                "reconciliation_status": status,
                "reconciliation": {
                    "status": rec.status,
                    "item_sum": rec.item_sum,
                    "baseline": rec.baseline,
                    "baseline_source": rec.baseline_source,
                    "baseline_figures_agreeing": (
                        rec.baseline_figures_agreeing
                    ),
                },
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
    return json.dumps(expected, indent=2) + "\n"


def main() -> None:
    payload = generate()
    DEFAULT_OUTPUT.write_text(payload, encoding="utf-8")
    count = len(json.loads(payload))
    print(f"wrote {count} receipts to {DEFAULT_OUTPUT}")


if __name__ == "__main__":
    main()
