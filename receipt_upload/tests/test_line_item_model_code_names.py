"""Product descriptions must beat identifier-only SKU/model price rows."""

import json
from pathlib import Path

import pytest

from receipt_upload.line_items.geometry import extract_items

_FIXTURE = Path(__file__).parent / "fixtures/stacked_model_code_names.json"


def test_real_stacked_descriptions_replace_model_codes() -> None:
    data = json.loads(_FIXTURE.read_text())
    items, _ = extract_items(data["words"], set(data["items_line_ids"]))
    assert [(item["name"], item["price"]) for item in items] == [
        ("50FT CAT-6 BLUE CABLE", 13.99),
        ("USB-C TO ETHERNET 1.0G ADAPTE", 14.99),
    ]
    assert {p["line_id"] for p in items[0]["name_word_ids"]} == {14}
    assert {p["line_id"] for p in items[1]["name_word_ids"]} == {9}
    assert [item["line_ids"] for item in items] == [[12, 13, 14], [6, 7, 8, 9]]


@pytest.mark.parametrize(
    "name",
    [
        "Vitamin B12 TABLETS",
        "3M PACKING TAPE",
        "50FT CAT-6 BLUE CABLE",
        "123456 WD-40 12OZ",
        "123456 50FT CAT-6",
    ],
)
def test_descriptive_price_row_keeps_its_name(name: str) -> None:
    words = [
        *[
            dict(
                line_id=1,
                word_id=index + 1,
                text=token,
                x=0.02 + index * 0.12,
                y_mid=0.8,
                h=0.02,
            )
            for index, token in enumerate(name.split())
        ],
        dict(line_id=1, word_id=20, text="9.99", x=0.8, y_mid=0.8, h=0.02),
        dict(
            line_id=2,
            word_id=1,
            text="DETAIL TEXT",
            x=0.05,
            y_mid=0.77,
            h=0.02,
        ),
    ]
    items, _ = extract_items(words, {1, 2})
    assert len(items) == 1
    assert items[0]["name"] == name
    assert items[0]["price"] == 9.99


def test_model_code_without_description_keeps_printed_name() -> None:
    data = json.loads(_FIXTURE.read_text())
    words = [w for w in data["words"] if w["line_id"] not in {9, 14}]
    items, _ = extract_items(words, set(data["items_line_ids"]))
    assert [(item["name"], item["price"]) for item in items] == [
        ("6435180 BE-PEC6ST50", 13.99),
        ("6523711 BE-PA2CEW23", 14.99),
    ]
