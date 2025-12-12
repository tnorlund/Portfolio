import pytest

from receipt_agent.agents.label_harmonizer.tools.helpers import (
    extract_pricing_table_from_words,
)


def _word(
    text: str,
    x: float,
    y: float,
    *,
    width: float = 10.0,
    height: float = 10.0,
    line_id: int = 1,
    word_id: int = 1,
    label: str | None = None,
):
    return {
        "text": text,
        "line_id": line_id,
        "word_id": word_id,
        "label": label,
        "bounding_box": {"x": x, "y": y, "width": width, "height": height},
    }


def test_pricing_table_errors_on_no_words():
    result = extract_pricing_table_from_words([])
    assert "error" in result


def test_pricing_table_errors_when_no_geometry():
    result = extract_pricing_table_from_words(
        [{"text": "Item", "line_id": 1, "word_id": 1}]
    )
    assert "error" in result


def test_pricing_table_basic_two_rows():
    words = [
        _word("ItemA", x=0, y=0, line_id=1, word_id=1, label="PRODUCT_NAME"),
        _word("10.00", x=80, y=0, line_id=1, word_id=2, label="UNIT_PRICE"),
        _word("ItemB", x=0, y=40, line_id=2, word_id=1, label="PRODUCT_NAME"),
        _word("2", x=60, y=40, line_id=2, word_id=2, label="QUANTITY"),
        _word("5.00", x=90, y=40, line_id=2, word_id=3, label="UNIT_PRICE"),
    ]

    result = extract_pricing_table_from_words(words)

    assert "rows" in result and "columns" in result
    assert result["total_rows"] == 2
    assert result["total_columns"] >= 2
    all_cells = [cell for row in result["rows"] for cell in row["cells"]]
    assert any(cell["text"] == "ItemB" for cell in all_cells)


def test_pricing_table_filters_non_line_rows():
    words = [
        _word("Store", x=0, y=0, line_id=1, word_id=1),
        _word("Info", x=30, y=0, line_id=1, word_id=2),
        _word("ItemC", x=0, y=50, line_id=2, word_id=1, label="PRODUCT_NAME"),
        _word("12.00", x=80, y=50, line_id=2, word_id=2, label="UNIT_PRICE"),
    ]

    result = extract_pricing_table_from_words(words)

    assert result["total_rows"] == 1
    assert result["rows"][0]["cells"][0]["text"] == "ItemC"
