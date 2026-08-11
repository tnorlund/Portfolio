"""Contract tests for the Swift worker's DIRECT DynamoDB item writes.

Tier 2 of the worker-authority migration gave the Mac worker a Dynamo
write surface (`ReceiptStructureItems` in receipt_ocr_swift). Its item
serialization must stay byte-compatible with the receipt_dynamo entity
writers, so both sides pin the shared fixture
``receipt_ocr_swift/Tests/ReceiptOCRCoreTests/Fixtures/``
``swift_dynamo_items_contract.json``:

* the Swift side (``DynamoItemContractTests``) asserts the serializer
  reproduces the fixture byte-for-byte;
* this side asserts ``item_to_receipt_section`` /
  ``item_to_receipt_line_item`` parse the fixture back into valid
  entities with the exact field values.

A schema drift on either side fails a local suite before it fails
against the real table.
"""

import json
from pathlib import Path

from receipt_dynamo.entities.receipt_line_item import (
    item_to_receipt_line_item,
)
from receipt_dynamo.entities.receipt_section import item_to_receipt_section

_REPO_ROOT = Path(__file__).resolve().parents[5]
_FIXTURE_PATH = (
    _REPO_ROOT
    / "receipt_ocr_swift"
    / "Tests"
    / "ReceiptOCRCoreTests"
    / "Fixtures"
    / "swift_dynamo_items_contract.json"
)

IMAGE_ID = "12345678-1234-4123-8123-123456789012"


def _fixture() -> dict:
    with open(_FIXTURE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def test_swift_section_item_parses_into_a_valid_entity():
    section = item_to_receipt_section(_fixture()["section"])
    assert section.image_id == IMAGE_ID
    assert section.receipt_id == 1
    assert section.section_type == "ITEMS"
    assert section.line_ids == [3, 4]
    assert section.row_ids == [3]
    assert section.confidence == 0.95
    assert section.model_source == "swift-worker-v1"
    assert section.validation_status == "PENDING"


def test_swift_line_item_parses_into_a_valid_entity():
    item = item_to_receipt_line_item(_fixture()["line_item"])
    assert item.image_id == IMAGE_ID
    assert item.receipt_id == 1
    assert item.item_index == 0
    assert item.name == "ORGANIC"
    assert item.price == "3.99"
    assert item.line_ids == [3]
    assert item.extractor_version == "swift-worker-v1+line-items-blocks-v2"
    assert item.quantity == 2.0
    assert item.unit_price == 1.99
    assert item.is_discount is False
    assert item.collapsed_banding is False
    assert item.name_quality == "ok"
    assert item.raw_text == "ORGANIC 3.99"
    assert item.source_section_status == "PENDING"
    assert item.source_model_source == "swift-worker-v1"
    assert item.reconciliation_status == "match"
    assert item.baseline_figures_agreeing == 2
    # Worker rows never carry a merchant, so the sparse GSI must be
    # absent from the round-tripped item too.
    assert item.merchant_name is None
    assert "GSI1PK" not in item.to_item()


def test_swift_line_item_round_trips_through_the_python_writer():
    """The Swift item and the Python writer's item agree key-for-key.

    Parsing the fixture into an entity and re-serializing it through
    ``ReceiptLineItem.to_item()`` must reproduce the fixture exactly —
    proving the Swift serializer matches the Python writer, not merely
    that Python tolerates it.

    Timestamps are compared semantically rather than byte-for-byte:
    Swift always writes millisecond precision (".000") while Python's
    ``isoformat()`` omits zero microseconds. ``fromisoformat`` parses
    both, so the difference is representational only.
    """
    _TIMESTAMP_KEYS = {"created_at", "extracted_at"}

    def _without_timestamps(item: dict) -> dict:
        return {k: v for k, v in item.items() if k not in _TIMESTAMP_KEYS}

    fixture_item = _fixture()["line_item"]
    entity = item_to_receipt_line_item(fixture_item)
    assert _without_timestamps(entity.to_item()) == _without_timestamps(
        fixture_item
    )
    assert entity._extracted_at_iso().startswith("2026-08-11T00:00:00")

    fixture_section = _fixture()["section"]
    section = item_to_receipt_section(fixture_section)
    assert _without_timestamps(section.to_item()) == _without_timestamps(
        fixture_section
    )
    assert section.created_at.isoformat().startswith("2026-08-11T00:00:00")
