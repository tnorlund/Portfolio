"""Validate the bounded snapshot before storage."""

from dataclasses import replace

import pytest

from receipt_dynamo.data.shared_exceptions import EntityValidationError
from receipt_dynamo.entities.receipt_nutrition import (
    ReceiptNutritionSnapshot,
    item_to_receipt_nutrition_snapshot,
)

pytestmark = [pytest.mark.unit]
IMAGE = "9e2816f8-429a-4b6b-9a21-0887cd07c0b2"


def test_nutrition_document_roundtrip_and_no_indexes():
    record = ReceiptNutritionSnapshot(
        IMAGE, 1, "a" * 64, "b" * 64, "c" * 64, '{"rows":[],"summary":{}}'
    )
    item = record.to_item()
    assert item_to_receipt_nutrition_snapshot(IMAGE, 1, item) == record
    assert not any(
        key.startswith("GSI") or key == "time_to_live" for key in item
    )
    assert item["SK"]["S"] == "RECEIPT#00001#NUTRITION_SUMMARY"
    for payload in (
        "{}",
        '{"rows":[],"summary":0}',
        '{"rows":{},"summary":{}}',
    ):
        with pytest.raises(EntityValidationError):
            replace(record, payload_json=payload).to_item()
    with pytest.raises(EntityValidationError):
        item_to_receipt_nutrition_snapshot(IMAGE, 2, item)
