"""Unit tests for RECEIPT_SUMMARY stream support (line-item trigger).

A receipt's summary is written when its words, sections, labels and
merchant all exist, so summary INSERT/MODIFY is the trigger for
RECEIPT_LINE_ITEM recomputation. These tests pin: SK detection, the
LINE_ITEMS queue routing, and INSERT firing (a receipt's FIRST summary
must produce line items, not just later edits).
"""

from typing import Any, Optional

from receipt_dynamo.entities.receipt_summary import (
    MonetaryTotals,
    ReceiptSummary,
)
from receipt_dynamo.entities.receipt_summary_record import (
    ReceiptSummaryRecord,
)
from receipt_dynamo_stream.message_builder import (
    _extract_entity_data,
    build_messages_from_records,
)
from receipt_dynamo_stream.models import TargetQueue
from receipt_dynamo_stream.parsing import detect_entity_type

IMAGE_ID = "550e8400-e29b-41d4-a716-446655440000"


def _make_record(subtotal: float = 10.55) -> ReceiptSummaryRecord:
    summary = ReceiptSummary(
        image_id=IMAGE_ID,
        receipt_id=1,
        totals=MonetaryTotals(
            subtotal=subtotal,
            grand_total=subtotal + 0.88,
            tax=0.88,
        ),
    )
    return ReceiptSummaryRecord(summary=summary)


def _stream_record(
    event_name: str,
    old: Optional[ReceiptSummaryRecord],
    new: Optional[ReceiptSummaryRecord],
) -> dict[str, Any]:
    entity = old or new
    assert entity is not None
    dynamodb: dict[str, Any] = {"Keys": entity.key}
    if old is not None:
        dynamodb["OldImage"] = old.to_item()
    if new is not None:
        dynamodb["NewImage"] = new.to_item()
    return {
        "eventName": event_name,
        "eventID": "event-summary-1",
        "awsRegion": "us-east-1",
        "dynamodb": dynamodb,
    }


def test_detect_entity_type_summary_sk() -> None:
    assert detect_entity_type("RECEIPT#00001#SUMMARY") == "RECEIPT_SUMMARY"


def test_summary_sk_does_not_shadow_others() -> None:
    assert detect_entity_type("RECEIPT#00001") == "RECEIPT"
    assert (
        detect_entity_type("RECEIPT#00001#SECTION#ITEMS") == "RECEIPT_SECTION"
    )


def test_summary_routes_to_line_items_queue() -> None:
    data, targets = _extract_entity_data("RECEIPT_SUMMARY", _make_record())
    assert data["image_id"] == IMAGE_ID
    assert data["receipt_id"] == 1
    assert targets == [TargetQueue.LINE_ITEMS]


def test_summary_insert_produces_message() -> None:
    """A receipt's FIRST summary must trigger line-item extraction."""
    record = _stream_record("INSERT", None, _make_record())
    messages = build_messages_from_records([record])
    assert len(messages) == 1
    assert TargetQueue.LINE_ITEMS in messages[0].collections


def test_summary_modify_produces_message() -> None:
    record = _stream_record("MODIFY", _make_record(10.55), _make_record(12.00))
    messages = build_messages_from_records([record])
    assert len(messages) == 1
    assert TargetQueue.LINE_ITEMS in messages[0].collections
