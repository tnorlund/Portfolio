#!/usr/bin/env python3
"""Generate the Swift section-assigner parity expectations.

The 33-receipt golden OCR fixture intentionally stores only the line-item
decoder's compact word schema (x, y_mid, h), not full ReceiptLine geometry.
For this parity gate, both Python and Swift reconstruct the same zero-width
word facades and line extents before running the production row builder.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path

from receipt_chroma.embedding.formatting import build_receipt_rows
from receipt_upload.section_assignment import (
    assign_row_sections,
    load_prior_model,
    sections_from_assignments,
)


@dataclass(frozen=True)
class FixtureWord:
    line_id: int
    word_id: int
    text: str
    bounding_box: dict[str, float]


@dataclass(frozen=True)
class FixtureLine:
    image_id: str
    receipt_id: int
    line_id: int
    text: str
    bounding_box: dict[str, float]

    def calculate_centroid(self) -> tuple[float, float]:
        box = self.bounding_box
        return (
            box["x"] + box["width"] / 2,
            box["y"] + box["height"] / 2,
        )


def reconstruct(
    receipt: dict,
) -> tuple[list[FixtureLine], list[FixtureWord]]:
    words = [
        FixtureWord(
            line_id=int(raw["line_id"]),
            word_id=int(raw["word_id"]),
            text=str(raw["text"]),
            bounding_box={
                "x": float(raw["x"]),
                "y": float(raw["y_mid"]) - float(raw["h"]) / 2,
                "width": 0.0,
                "height": float(raw["h"]),
            },
        )
        for raw in receipt["words"]
    ]
    by_line: dict[int, list[FixtureWord]] = {}
    for word in words:
        by_line.setdefault(word.line_id, []).append(word)

    lines = []
    for line_id, members in sorted(by_line.items()):
        ordered = sorted(
            members, key=lambda word: (word.bounding_box["x"], word.word_id)
        )
        x_min = min(word.bounding_box["x"] for word in members)
        x_max = max(word.bounding_box["x"] for word in members)
        y_min = min(word.bounding_box["y"] for word in members)
        y_max = max(
            word.bounding_box["y"] + word.bounding_box["height"]
            for word in members
        )
        lines.append(
            FixtureLine(
                image_id=receipt["image_id"],
                receipt_id=int(receipt["receipt_id"]),
                line_id=line_id,
                text=" ".join(word.text for word in ordered),
                bounding_box={
                    "x": x_min,
                    "y": y_min,
                    "width": x_max - x_min,
                    "height": y_max - y_min,
                },
            )
        )
    return lines, words


_SCRIPT_DIR = Path(__file__).resolve().parent
_PACKAGE_DIR = _SCRIPT_DIR.parent
_REPO_ROOT = _PACKAGE_DIR.parent
DEFAULT_INPUT = (
    _REPO_ROOT / "receipt_upload/tests/fixtures/line_items_golden_ocr.json"
)
DEFAULT_OUTPUT = (
    _PACKAGE_DIR
    / "Tests/ReceiptOCRCoreTests/Fixtures/section_assignment_parity_expected.json"
)


def generate(input_path: Path = DEFAULT_INPUT) -> str:
    """The expectation file's exact bytes, without writing anything.

    Split out of ``main`` for the same reason
    ``generate_receipt_structure_parity.generate`` was: the anti-drift
    gate has to be able to regenerate and diff. Until this split, this
    was the ONE parity fixture with no gate at all -- nothing in CI ran
    this script, and ``swift-ci.yml``'s path filter does not name
    ``section_assignment.py``, so a change to the assigner rotted the
    snapshot silently in both directions.
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
            }
        )

    # The golden set grows (33 -> 35 at the 2026-08 batch); never hardcode
    # its size again, only guard against silently generating nothing.
    if len(expected) != len(fixture["receipts"]):
        raise RuntimeError(
            f"expected {len(fixture['receipts'])} receipts, "
            f"got {len(expected)}"
        )
    return json.dumps(expected, indent=2) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()

    payload = generate(args.input)
    args.output.write_text(payload, encoding="utf-8")
    print(f"wrote {len(json.loads(payload))} receipts to {args.output}")


if __name__ == "__main__":
    main()
