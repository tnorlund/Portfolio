"""Unit tests for the ReceiptLineItem entity."""

from datetime import datetime

import pytest

from receipt_dynamo.entities.receipt_line_item import (
    ReceiptLineItem,
    item_to_receipt_line_item,
    normalize_product_text,
    slugify_merchant,
)

IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"


def make(**overrides):
    kwargs = dict(
        receipt_id=1,
        image_id=IMAGE_ID,
        item_index=0,
        name="ORG WHOLE MILK",
        price="10.99",
        line_ids=[12, 13],
        extractor_version="line-items-geom-v1",
        extracted_at=datetime(2026, 7, 29, 12, 0, 0),
        quantity=2.0,
        unit_price=5.495,
        raw_text="ORG WHOLE MILK 2 @ 5.495 10.99",
        merchant_name="Sprouts Farmers Market",
        source_section_status="VALID",
        source_model_source="section-qa-v3",
        reconciliation_status="match",
    )
    kwargs.update(overrides)
    return ReceiptLineItem(**kwargs)


def test_round_trip():
    li = make()
    assert item_to_receipt_line_item(li.to_item()) == li


def test_key_schema():
    li = make(receipt_id=3, item_index=7)
    assert li.key["PK"]["S"] == f"IMAGE#{IMAGE_ID}"
    assert li.key["SK"]["S"] == "RECEIPT#00003#LINE_ITEM#00007"
    assert li.to_item()["TYPE"]["S"] == "RECEIPT_LINE_ITEM"


def test_gsi1_present_for_named_items():
    item = make().to_item()
    assert item["GSI1PK"]["S"] == "MERCHANT#sprouts-farmers-market"
    assert item["GSI1SK"]["S"].startswith("LINE_ITEM#ORG WHOLE MILK#")


def test_gsi1_omitted_for_low_name_quality():
    item = make(name="", name_quality="low").to_item()
    assert "GSI1PK" not in item
    assert "GSI1SK" not in item


def test_gsi1_omitted_without_merchant():
    item = make(merchant_name=None).to_item()
    assert "GSI1PK" not in item


def test_low_quality_round_trip():
    li = make(name="", name_quality="low", quantity=None, unit_price=None)
    assert item_to_receipt_line_item(li.to_item()) == li


def test_negative_discount_price_ok():
    li = make(price="-3.00", is_discount=True)
    assert item_to_receipt_line_item(li.to_item()).price == "-3.00"


def test_numeric_price_normalized_to_string():
    assert make(price=7.98).price == "7.98"


@pytest.mark.parametrize(
    "overrides,msg",
    [
        ({"receipt_id": 0}, "receipt_id must be positive"),
        ({"item_index": -1}, "item_index must be non-negative"),
        ({"price": "abc"}, "price must parse"),
        ({"line_ids": []}, "line_ids must be a non-empty list"),
        ({"line_ids": [1, "2"]}, "line_ids must contain"),
        ({"extractor_version": ""}, "extractor_version"),
        ({"quantity": -1.0}, "quantity must be finite and non-negative"),
        ({"name_quality": "bad"}, "name_quality must be one of"),
        ({"name": "", "name_quality": "ok"}, "name is required"),
        (
            {"reconciliation_status": "sorta"},
            "reconciliation_status must be one of",
        ),
    ],
)
def test_validation_errors(overrides, msg):
    with pytest.raises(ValueError, match=msg):
        make(**overrides)


def test_slug_and_normalize_helpers():
    assert slugify_merchant("Trader Joe's #123") == "trader-joe-s-123"
    assert normalize_product_text("  org.  milk 2% ") == "ORG MILK 2"


def test_iso_string_extracted_at_normalizes():
    li = make(extracted_at="2026-07-29T12:00:00")
    assert isinstance(li.extracted_at, datetime)
