"""Unit tests for RECEIPT_SECTION stream support.

Mirrors the RECEIPT_WORD_LABEL tests: SK detection, watched-field change
detection, and message building for INSERT / MODIFY / REMOVE events.
"""

from datetime import datetime
from typing import Any, Optional

from receipt_dynamo.entities.receipt_section import ReceiptSection
from receipt_dynamo_stream.change_detection import (
    get_chromadb_relevant_changes,
)
from receipt_dynamo_stream.message_builder import (
    _extract_entity_data,
    build_entity_change_message,
    build_messages_from_records,
)
from receipt_dynamo_stream.models import ChromaDBCollection
from receipt_dynamo_stream.parsing import detect_entity_type

IMAGE_ID = "550e8400-e29b-41d4-a716-446655440000"


def _make_section(
    section_type: str = "HEADER",
    line_ids: Optional[list[int]] = None,
    confidence: float = 0.9,
    validation_status: str = "PENDING",
) -> ReceiptSection:
    return ReceiptSection(
        image_id=IMAGE_ID,
        receipt_id=1,
        section_type=section_type,
        line_ids=line_ids or [1, 2, 3],
        created_at=datetime.fromisoformat("2024-01-01T00:00:00"),
        confidence=confidence,
        model_source="section-seed-v0",
        validation_status=validation_status,
    )


def _record(
    event_name: str,
    old: Optional[ReceiptSection],
    new: Optional[ReceiptSection],
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
        "eventID": "event-section-1",
        "awsRegion": "us-east-1",
        "dynamodb": dynamodb,
    }


# SK pattern detection


def test_detect_entity_type_section_sk() -> None:
    assert (
        detect_entity_type("RECEIPT#00001#SECTION#HEADER") == "RECEIPT_SECTION"
    )


def test_detect_entity_type_section_does_not_shadow_others() -> None:
    """Existing SK patterns must be unaffected by the SECTION matcher."""
    assert (
        detect_entity_type("RECEIPT#00001#LINE#00001#WORD#00001#LABEL#TOTAL")
        == "RECEIPT_WORD_LABEL"
    )
    assert detect_entity_type("RECEIPT#00001#LINE#00001") == "RECEIPT_LINE"
    assert (
        detect_entity_type("RECEIPT#00001#COMPACTION_RUN#abc")
        == "COMPACTION_RUN"
    )


# Watched-field change detection


def test_section_validation_status_change_is_relevant() -> None:
    old = _make_section(validation_status="PENDING")
    new = _make_section(validation_status="VALID")
    changes = get_chromadb_relevant_changes("RECEIPT_SECTION", old, new)
    assert set(changes) == {"validation_status"}
    assert changes["validation_status"].old == "PENDING"
    assert changes["validation_status"].new == "VALID"


def test_section_line_ids_and_confidence_changes_are_relevant() -> None:
    old = _make_section(line_ids=[1, 2], confidence=0.5)
    new = _make_section(line_ids=[1, 2, 3], confidence=0.9)
    changes = get_chromadb_relevant_changes("RECEIPT_SECTION", old, new)
    assert set(changes) == {"line_ids", "confidence"}


def test_section_irrelevant_field_change_is_ignored() -> None:
    old = _make_section()
    new = _make_section()
    new.model_source = "section-seed-v1"
    changes = get_chromadb_relevant_changes("RECEIPT_SECTION", old, new)
    assert changes == {}


# Entity data extraction


def test_extract_entity_data_receipt_section_targets_lines_only() -> None:
    section = _make_section()
    entity_data, collections = _extract_entity_data("RECEIPT_SECTION", section)
    assert entity_data == {
        "entity_type": "RECEIPT_SECTION",
        "image_id": IMAGE_ID,
        "receipt_id": 1,
        "section_type": "HEADER",
    }
    assert collections == [ChromaDBCollection.LINES]


# Message building


def test_section_modify_builds_message() -> None:
    record = _record(
        "MODIFY",
        _make_section(validation_status="PENDING"),
        _make_section(validation_status="VALID"),
    )
    message = build_entity_change_message(record)
    assert message is not None
    assert message.entity_type == "RECEIPT_SECTION"
    assert message.event_name == "MODIFY"
    assert message.collections == (ChromaDBCollection.LINES,)
    assert message.entity_data["image_id"] == IMAGE_ID
    assert message.entity_data["receipt_id"] == 1


def test_section_remove_builds_message_without_changes() -> None:
    """REMOVE of a section must trigger recompute even with no field diff."""
    record = _record("REMOVE", _make_section(), None)
    message = build_entity_change_message(record)
    assert message is not None
    assert message.event_name == "REMOVE"
    assert message.entity_type == "RECEIPT_SECTION"
    assert message.collections == (ChromaDBCollection.LINES,)


def test_section_insert_builds_message_via_records_pipeline() -> None:
    """INSERT of a section (QA creating one) must produce a message."""
    record = _record("INSERT", None, _make_section())
    messages = build_messages_from_records([record])
    assert len(messages) == 1
    assert messages[0].entity_type == "RECEIPT_SECTION"
    assert messages[0].event_name == "INSERT"


def test_non_section_insert_still_ignored() -> None:
    """INSERTs of other entity types must keep their existing behavior
    (dropped — the embedding pipeline covers new entities)."""
    from .test_message_builder import _make_word_label

    label = _make_word_label()
    record = {
        "eventName": "INSERT",
        "eventID": "event-label-insert",
        "awsRegion": "us-east-1",
        "dynamodb": {
            "Keys": label.key,
            "NewImage": label.to_item(),
        },
    }
    assert build_messages_from_records([record]) == []


def test_section_modify_without_relevant_changes_is_dropped() -> None:
    old = _make_section()
    new = _make_section()
    new.model_source = "section-seed-v1"
    record = _record("MODIFY", old, new)
    assert build_entity_change_message(record) is None
