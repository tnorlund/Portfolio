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


def test_item_size_check_includes_primary_keys() -> None:
    from receipt_dynamo.entities.nutrition_support import nutrition_item

    attrs = {"payload_json": "x" * 379_960}
    assert nutrition_item(attrs)
    key = {"PK": {"S": "IMAGE#" + "a" * 36}, "SK": {"S": "RECEIPT#00001"}}
    with pytest.raises(EntityValidationError, match="safe item size"):
        nutrition_item(attrs, key=key)


def test_source_observation_may_exceed_payload_cap() -> None:
    import uuid

    from receipt_dynamo.entities.nutrition_support import nutrition_json
    from receipt_dynamo.entities.receipt_nutrition import NutritionInput

    image_id = str(uuid.uuid4())
    parent = {
        "PK": {"S": f"IMAGE#{image_id}"},
        "SK": {"S": "RECEIPT#00001"},
        "timestamp_added": {"S": "2026-09-08T00:00:00+00:00"},
    }
    lines = [
        {
            "SK": {"S": f"RECEIPT#00001#LINE_ITEM#{i:05d}"},
            "raw": {"S": "y" * 800},
        }
        for i in range(400)
    ]
    source = nutrition_json({"parent": parent, "lines": lines}, limit=None)
    assert len(source.encode("utf-8")) > 300_000
    observation = NutritionInput(image_id, 1, source)
    assert len(observation.fingerprint) == 64
    with pytest.raises(EntityValidationError, match="300 KB"):
        nutrition_json({"parent": parent, "lines": lines})
