from datetime import datetime

import pytest
from receipt_dynamo_stream import (
    CHROMADB_RELEVANT_FIELDS,
    ChromaDBCollection,
    FieldChange,
    LambdaResponse,
    ParsedStreamRecord,
    StreamMessage,
    detect_entity_type,
    get_chromadb_relevant_changes,
)

from receipt_dynamo.entities.receipt_place import ReceiptPlace
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel


def _make_place(merchant_name: str = "Test Merchant") -> ReceiptPlace:
    return ReceiptPlace(
        image_id="550e8400-e29b-41d4-a716-446655440000",
        receipt_id=1,
        place_id="place123",
        merchant_name=merchant_name,
        formatted_address="123 Main St",
        phone_number="555-123-4567",
        matched_fields=["name"],
        validated_by="INFERENCE",
        timestamp=datetime.fromisoformat("2024-01-01T00:00:00"),
    )


def _make_word_label(label: str = "TOTAL") -> ReceiptWordLabel:
    return ReceiptWordLabel(
        image_id="550e8400-e29b-41d4-a716-446655440000",
        receipt_id=1,
        line_id=1,
        word_id=1,
        label=label,
        reasoning="initial",
        timestamp_added=datetime.fromisoformat("2024-01-01T00:00:00"),
        validation_status="NONE",
    )


def test_lambda_response_to_dict() -> None:
    response = LambdaResponse(
        status_code=200,
        processed_records=5,
        queued_messages=3,
    )
    assert response.to_dict() == {
        "statusCode": 200,
        "processed_records": 5,
        "queued_messages": 3,
    }


def test_field_change_is_frozen() -> None:
    change = FieldChange(old="a", new="b")
    with pytest.raises(Exception):
        change.__setattr__("old", "c")


def test_parsed_stream_record_round_trip() -> None:
    entity = _make_place()
    record = ParsedStreamRecord(
        entity_type="RECEIPT_PLACE",
        old_entity=entity,
        new_entity=None,
        pk=entity.key["PK"]["S"],
        sk=entity.key["SK"]["S"],
    )

    assert record.entity_type == "RECEIPT_PLACE"
    assert record.pk.startswith("IMAGE#")
    assert record.sk.endswith("#PLACE")
    assert record.old_entity == entity
    assert record.new_entity is None


def test_stream_message_supports_multiple_collections() -> None:
    message = StreamMessage(
        entity_type="RECEIPT_PLACE",
        entity_data={"image_id": "img", "receipt_id": 1},
        changes={"merchant_name": FieldChange(old="old", new="new")},
        event_name="MODIFY",
        collections=(ChromaDBCollection.LINES, ChromaDBCollection.WORDS),
        stream_record_id="abc",
        aws_region="us-east-1",
    )

    assert {col.value for col in message.collections} == {"lines", "words"}
    assert message.changes["merchant_name"].new == "new"


def test_detect_entity_type_patterns() -> None:
    assert detect_entity_type("RECEIPT#00001#METADATA") == "RECEIPT_METADATA"
    assert (
        detect_entity_type("RECEIPT#00001#LINE#00001#WORD#00001#LABEL#TOTAL")
        == "RECEIPT_WORD_LABEL"
    )
    assert (
        detect_entity_type("RECEIPT#00001#COMPACTION_RUN#abc")
        == "COMPACTION_RUN"
    )
    assert detect_entity_type("RECEIPT#00001#LINE#00001") is None


def test_get_chromadb_relevant_changes_for_place() -> None:
    old = _make_place("Old Merchant")
    new = _make_place("New Merchant")

    changes = get_chromadb_relevant_changes("RECEIPT_PLACE", old, new)

    assert "merchant_name" in changes
    assert changes["merchant_name"].old == "Old Merchant"
    assert changes["merchant_name"].new == "New Merchant"
    assert set(CHROMADB_RELEVANT_FIELDS["RECEIPT_PLACE"]) >= {
        "merchant_name",
        "formatted_address",
    }


def test_get_chromadb_relevant_changes_for_word_label() -> None:
    old = _make_word_label("TOTAL")
    new = _make_word_label("TOTAL")
    new.reasoning = "updated"

    changes = get_chromadb_relevant_changes("RECEIPT_WORD_LABEL", old, new)

    assert "reasoning" in changes
    assert changes["reasoning"].old == "initial"
    assert changes["reasoning"].new == "updated"
