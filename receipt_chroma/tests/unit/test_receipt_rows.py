"""Tests for receipt-row materialization and price-column pairing."""

from dataclasses import dataclass
from datetime import datetime, timezone

import pytest
from receipt_chroma.embedding.formatting import (
    build_receipt_rows,
    detect_price_column,
    is_amount_text,
)

_IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"


@dataclass
class FakeLine:
    line_id: int
    text: str
    bounding_box: dict[str, float]
    image_id: str = _IMAGE_ID
    receipt_id: int = 1

    def calculate_centroid(self) -> tuple[float, float]:
        box = self.bounding_box
        return (
            box["x"] + box["width"] / 2,
            box["y"] + box["height"] / 2,
        )


@dataclass
class FakeWord:
    line_id: int
    word_id: int
    text: str
    bounding_box: dict[str, float]


@pytest.mark.parametrize(
    ("text", "expected"),
    [("$12.99", True), ("1,234.50", True), ("(2.00)", True), ("12/99", False)],
)
def test_is_amount_text_requires_cents(text: str, expected: bool) -> None:
    assert is_amount_text(text) is expected


def test_detect_price_column_uses_dominant_right_edge() -> None:
    words = [
        FakeWord(1, 1, "$1.00", {"x": 0.80, "width": 0.15}),
        FakeWord(2, 1, "$2.00", {"x": 0.81, "width": 0.14}),
        FakeWord(3, 1, "$3.00", {"x": 0.80, "width": 0.15}),
        FakeWord(4, 1, "$0.50", {"x": 0.40, "width": 0.15}),
    ]

    column = detect_price_column(words)

    assert column is not None
    assert column.x == pytest.approx(0.95)
    assert column.tolerance == pytest.approx(0.03)


def test_build_receipt_rows_pairs_split_label_and_amount() -> None:
    lines = [
        FakeLine(
            10,
            "ORGANIC APPLES",
            {"x": 0.05, "y": 0.80, "width": 0.45, "height": 0.04},
        ),
        FakeLine(
            11,
            "$4.99",
            {"x": 0.82, "y": 0.80, "width": 0.13, "height": 0.04},
        ),
        FakeLine(
            12,
            "TOTAL $4.99",
            {"x": 0.05, "y": 0.20, "width": 0.90, "height": 0.04},
        ),
    ]
    words = [
        FakeWord(10, 1, "ORGANIC", {"x": 0.05, "width": 0.18}),
        FakeWord(10, 2, "APPLES", {"x": 0.25, "width": 0.16}),
        FakeWord(11, 1, "$4.99", {"x": 0.82, "width": 0.13}),
        FakeWord(12, 1, "TOTAL", {"x": 0.05, "width": 0.15}),
        FakeWord(12, 2, "$4.99", {"x": 0.82, "width": 0.13}),
    ]
    timestamp = datetime(2026, 7, 14, tzinfo=timezone.utc)

    rows = build_receipt_rows(lines, words, created_at=timestamp)

    assert len(rows) == 2
    item_row = rows[0]
    assert item_row.row_id == 10
    assert item_row.line_ids == [10, 11]
    assert item_row.price_column_x == pytest.approx(0.95)
    assert item_row.label_text == "ORGANIC APPLES"
    assert item_row.amount_text == "$4.99"
    assert item_row.amount_line_id == 11
    assert item_row.amount_word_id == 1
    assert item_row.created_at == timestamp


def test_build_receipt_rows_rejects_mixed_receipts() -> None:
    lines = [
        FakeLine(1, "A", {"x": 0.0, "y": 0.8, "width": 0.1, "height": 0.1}),
        FakeLine(
            2,
            "B",
            {"x": 0.0, "y": 0.6, "width": 0.1, "height": 0.1},
            receipt_id=2,
        ),
    ]

    with pytest.raises(ValueError, match="exactly one receipt"):
        build_receipt_rows(lines, [])
