"""Comprehensive unit tests for message_builder module."""

from datetime import datetime
from typing import Any
from unittest.mock import MagicMock

import pytest
from receipt_dynamo_stream.message_builder import (
    _extract_entity_data,
    build_compaction_run_completion_messages,
    build_compaction_run_messages,
    build_entity_change_message,
    build_messages_from_records,
)
from receipt_dynamo_stream.models import ChromaDBCollection, StreamMessage

from receipt_dynamo.entities.receipt import Receipt
from receipt_dynamo.entities.receipt_line import ReceiptLine
from receipt_dynamo.entities.receipt_place import ReceiptPlace
from receipt_dynamo.entities.receipt_word import ReceiptWord
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel

from .conftest import MockMetrics


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


def _make_receipt() -> Receipt:
    return Receipt(
        image_id="550e8400-e29b-41d4-a716-446655440000",
        receipt_id=1,
        width=100,
        height=200,
        timestamp_added="2024-01-01T00:00:00",
        raw_s3_bucket="test-bucket",
        raw_s3_key="test-key",
        top_left={"x": 0.0, "y": 0.0},
        top_right={"x": 1.0, "y": 0.0},
        bottom_left={"x": 0.0, "y": 1.0},
        bottom_right={"x": 1.0, "y": 1.0},
    )


def _make_receipt_word() -> ReceiptWord:
    return ReceiptWord(
        image_id="550e8400-e29b-41d4-a716-446655440000",
        receipt_id=1,
        line_id=2,
        word_id=3,
        text="TOTAL",
        bounding_box={"x": 0.1, "y": 0.1, "width": 0.2, "height": 0.05},
        top_left={"x": 0.1, "y": 0.1},
        top_right={"x": 0.3, "y": 0.1},
        bottom_left={"x": 0.1, "y": 0.15},
        bottom_right={"x": 0.3, "y": 0.15},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=0.95,
    )


def _make_receipt_line() -> ReceiptLine:
    return ReceiptLine(
        image_id="550e8400-e29b-41d4-a716-446655440000",
        receipt_id=1,
        line_id=2,
        text="TOTAL $10.00",
        bounding_box={"x": 0.1, "y": 0.1, "width": 0.5, "height": 0.05},
        top_left={"x": 0.1, "y": 0.1},
        top_right={"x": 0.6, "y": 0.1},
        bottom_left={"x": 0.1, "y": 0.15},
        bottom_right={"x": 0.6, "y": 0.15},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=0.95,
    )


def _create_compaction_run_insert_record() -> dict[str, Any]:
    """Create a mock DynamoDB stream record for COMPACTION_RUN INSERT."""
    return {
        "eventName": "INSERT",
        "eventID": "event-123",
        "awsRegion": "us-east-1",
        "dynamodb": {
            "Keys": {
                "PK": {"S": "IMAGE#550e8400-e29b-41d4-a716-446655440000"},
                "SK": {"S": "RECEIPT#00001#COMPACTION_RUN#run-abc"},
            },
            "NewImage": {
                "PK": {"S": "IMAGE#550e8400-e29b-41d4-a716-446655440000"},
                "SK": {"S": "RECEIPT#00001#COMPACTION_RUN#run-abc"},
                "run_id": {"S": "run-abc"},
                "receipt_id": {"N": "1"},
                "lines_delta_prefix": {"S": "s3://bucket/lines/"},
                "words_delta_prefix": {"S": "s3://bucket/words/"},
            },
        },
    }


def _create_compaction_run_completion_record() -> dict[str, Any]:
    """Create a mock DynamoDB stream record for COMPACTION_RUN completion."""
    return {
        "eventName": "MODIFY",
        "eventID": "event-456",
        "awsRegion": "us-east-1",
        "dynamodb": {
            "Keys": {
                "PK": {"S": "IMAGE#550e8400-e29b-41d4-a716-446655440000"},
                "SK": {"S": "RECEIPT#00001#COMPACTION_RUN#run-abc"},
            },
            "NewImage": {
                "PK": {"S": "IMAGE#550e8400-e29b-41d4-a716-446655440000"},
                "SK": {"S": "RECEIPT#00001#COMPACTION_RUN#run-abc"},
                "run_id": {"S": "run-abc"},
                "receipt_id": {"N": "1"},
                "lines_state": {"S": "COMPLETED"},
                "words_state": {"S": "COMPLETED"},
                "lines_delta_prefix": {"S": "s3://bucket/lines/"},
                "words_delta_prefix": {"S": "s3://bucket/words/"},
            },
        },
    }


def _create_place_modify_record() -> dict[str, Any]:
    """Create a mock DynamoDB stream record for RECEIPT_PLACE MODIFY."""
    old_place = _make_place("Old Merchant")
    new_place = _make_place("New Merchant")

    return {
        "eventName": "MODIFY",
        "eventID": "event-789",
        "awsRegion": "us-east-1",
        "dynamodb": {
            "Keys": old_place.key,
            "OldImage": old_place.to_item(),
            "NewImage": new_place.to_item(),
        },
    }


def _create_word_label_remove_record() -> dict[str, Any]:
    """Create a mock DynamoDB stream record for RECEIPT_WORD_LABEL REMOVE."""
    word_label = _make_word_label()

    return {
        "eventName": "REMOVE",
        "eventID": "event-101",
        "awsRegion": "us-east-1",
        "dynamodb": {
            "Keys": word_label.key,
            "OldImage": word_label.to_item(),
        },
    }


# Test build_messages_from_records


def test_build_messages_from_records_with_insert() -> None:
    """Test building messages from INSERT event."""
    record = _create_compaction_run_insert_record()
    messages = build_messages_from_records([record])

    assert len(messages) == 2  # One per collection
    assert all(msg.entity_type == "COMPACTION_RUN" for msg in messages)
    assert all(msg.event_name == "INSERT" for msg in messages)


def test_build_messages_from_records_with_modify() -> None:
    """Test building messages from MODIFY event."""
    record = _create_place_modify_record()
    messages = build_messages_from_records([record])

    assert len(messages) == 1
    assert messages[0].entity_type == "RECEIPT_PLACE"
    assert messages[0].event_name == "MODIFY"
    assert "merchant_name" in messages[0].changes


def test_build_messages_from_records_with_remove() -> None:
    """Test building messages from REMOVE event."""
    record = _create_word_label_remove_record()
    messages = build_messages_from_records([record])

    assert len(messages) == 1
    assert messages[0].entity_type == "RECEIPT_WORD_LABEL"
    assert messages[0].event_name == "REMOVE"


def test_build_messages_from_records_with_completion() -> None:
    """Test building messages for compaction run completion."""
    record = _create_compaction_run_completion_record()
    messages = build_messages_from_records([record])

    assert len(messages) == 2  # One per collection
    assert all(msg.entity_type == "COMPACTION_RUN" for msg in messages)
    assert all(msg.event_name == "MODIFY" for msg in messages)


def test_build_messages_from_records_with_metrics() -> None:
    """Test that metrics are recorded correctly."""
    metrics = MockMetrics()
    record = _create_place_modify_record()
    messages = build_messages_from_records([record], metrics)

    assert len(messages) == 1
    # Check that metrics were recorded
    metric_names = [m[0] for m in metrics.counts]
    assert "ChromaDBRelevantChanges" in metric_names
    assert "StreamMessageCreated" in metric_names


def test_build_messages_from_records_empty_list() -> None:
    """Test with empty records list."""
    messages = build_messages_from_records([])
    assert messages == []


# Test build_compaction_run_messages


def test_build_compaction_run_messages_success() -> None:
    """Test successful building of compaction run messages."""
    record = _create_compaction_run_insert_record()
    messages = build_compaction_run_messages(record)

    assert len(messages) == 2
    assert messages[0].collections == (ChromaDBCollection.LINES,)
    assert messages[1].collections == (ChromaDBCollection.WORDS,)

    # Verify entity data
    for msg in messages:
        assert msg.entity_data["run_id"] == "run-abc"
        assert (
            msg.entity_data["image_id"]
            == "550e8400-e29b-41d4-a716-446655440000"
        )
        assert msg.entity_data["receipt_id"] == 1


def test_build_compaction_run_messages_missing_new_image() -> None:
    """Test with missing NewImage."""
    record = {
        "eventName": "INSERT",
        "dynamodb": {
            "Keys": {
                "PK": {"S": "IMAGE#550e8400-e29b-41d4-a716-446655440000"},
                "SK": {"S": "RECEIPT#00001#COMPACTION_RUN#run-abc"},
            },
        },
    }
    messages = build_compaction_run_messages(record)
    assert messages == []


def test_build_compaction_run_messages_not_compaction_run() -> None:
    """Test with non-compaction-run SK."""
    record = {
        "eventName": "INSERT",
        "dynamodb": {
            "Keys": {
                "PK": {"S": "IMAGE#550e8400-e29b-41d4-a716-446655440000"},
                "SK": {"S": "RECEIPT#00001#PLACE"},
            },
            "NewImage": {},
        },
    }
    messages = build_compaction_run_messages(record)
    assert messages == []


def test_build_compaction_run_messages_with_metrics() -> None:
    """Test that metrics are recorded on errors."""
    metrics = MockMetrics()
    record = {
        "eventName": "INSERT",
        "dynamodb": {
            "Keys": {
                "PK": {"S": "IMAGE#550e8400-e29b-41d4-a716-446655440000"},
                "SK": {"S": "RECEIPT#INVALID#COMPACTION_RUN#run-abc"},
            },
            "NewImage": {
                "PK": {"S": "IMAGE#550e8400-e29b-41d4-a716-446655440000"},
                "SK": {"S": "RECEIPT#INVALID#COMPACTION_RUN#run-abc"},
                # Missing required fields to cause an error
            },
        },
    }
    messages = build_compaction_run_messages(record, metrics)
    assert messages == []
    # Should have recorded error metric
    metric_names = [m[0] for m in metrics.counts]
    assert "CompactionRunMessageBuildError" in metric_names


# Test build_compaction_run_completion_messages


def test_build_compaction_run_completion_messages_success() -> None:
    """Test successful building of completion messages."""
    record = _create_compaction_run_completion_record()
    messages = build_compaction_run_completion_messages(record)

    assert len(messages) == 2
    assert all(msg.entity_type == "COMPACTION_RUN" for msg in messages)
    assert ChromaDBCollection.LINES in messages[0].collections
    assert ChromaDBCollection.WORDS in messages[1].collections


def test_build_compaction_run_completion_messages_not_completed() -> None:
    """Test when embeddings are not completed."""
    record = {
        "eventName": "MODIFY",
        "dynamodb": {
            "Keys": {
                "PK": {"S": "IMAGE#550e8400-e29b-41d4-a716-446655440000"},
                "SK": {"S": "RECEIPT#00001#COMPACTION_RUN#run-abc"},
            },
            "NewImage": {
                "run_id": {"S": "run-abc"},
                "lines_state": {"S": "PROCESSING"},  # Not completed
                "words_state": {"S": "PROCESSING"},
            },
        },
    }
    messages = build_compaction_run_completion_messages(record)
    assert messages == []


def test_build_compaction_run_completion_messages_not_compaction_run() -> None:
    """Test with non-compaction-run SK."""
    record = {
        "eventName": "MODIFY",
        "dynamodb": {
            "Keys": {
                "PK": {"S": "IMAGE#550e8400-e29b-41d4-a716-446655440000"},
                "SK": {"S": "RECEIPT#00001#PLACE"},
            },
            "NewImage": {},
        },
    }
    messages = build_compaction_run_completion_messages(record)
    assert messages == []


def test_build_compaction_run_completion_messages_missing_new_image() -> None:
    """Test with missing NewImage."""
    record = {
        "eventName": "MODIFY",
        "dynamodb": {
            "Keys": {
                "PK": {"S": "IMAGE#550e8400-e29b-41d4-a716-446655440000"},
                "SK": {"S": "RECEIPT#00001#COMPACTION_RUN#run-abc"},
            },
        },
    }
    messages = build_compaction_run_completion_messages(record)
    assert messages == []


def test_build_compaction_run_completion_messages_with_metrics() -> None:
    """Test that completion detection metric is recorded."""
    metrics = MockMetrics()
    record = _create_compaction_run_completion_record()
    messages = build_compaction_run_completion_messages(record, metrics)

    assert len(messages) == 2
    metric_names = [m[0] for m in metrics.counts]
    assert "CompactionRunCompletionDetected" in metric_names


# Test build_entity_change_message


def test_build_entity_change_message_place_modify() -> None:
    """Test building message for place modification."""
    record = _create_place_modify_record()
    message = build_entity_change_message(record)

    assert message is not None
    assert message.entity_type == "RECEIPT_PLACE"
    assert message.event_name == "MODIFY"
    assert "merchant_name" in message.changes
    assert message.collections == (
        ChromaDBCollection.LINES,
        ChromaDBCollection.WORDS,
    )


def test_build_entity_change_message_word_label_remove() -> None:
    """Test building message for word label removal."""
    record = _create_word_label_remove_record()
    message = build_entity_change_message(record)

    assert message is not None
    assert message.entity_type == "RECEIPT_WORD_LABEL"
    assert message.event_name == "REMOVE"
    assert message.collections == (ChromaDBCollection.WORDS,)


def test_build_entity_change_message_no_relevant_changes() -> None:
    """Test when there are no ChromaDB-relevant changes."""
    # Create a MODIFY record with no relevant field changes
    place = _make_place()
    record = {
        "eventName": "MODIFY",
        "dynamodb": {
            "Keys": place.key,
            "OldImage": place.to_item(),
            "NewImage": place.to_item(),  # Same as old
        },
    }
    message = build_entity_change_message(record)
    # Should return None because no relevant changes
    assert message is None


def test_build_entity_change_message_invalid_record() -> None:
    """Test with invalid record structure."""
    record: dict[str, Any] = {"eventName": "MODIFY", "dynamodb": {}}
    message = build_entity_change_message(record)
    assert message is None


def test_build_entity_change_message_with_metrics() -> None:
    """Test that metrics are recorded."""
    metrics = MockMetrics()
    record = _create_place_modify_record()
    message = build_entity_change_message(record, metrics)

    assert message is not None
    metric_names = [m[0] for m in metrics.counts]
    assert "ChromaDBRelevantChanges" in metric_names
    assert "StreamMessageCreated" in metric_names


# Test _extract_entity_data


def test_extract_entity_data_receipt_place() -> None:
    """Test extracting data from ReceiptPlace."""
    entity = _make_place()
    data, collections = _extract_entity_data("RECEIPT_PLACE", entity)

    assert data["entity_type"] == "RECEIPT_PLACE"
    assert data["image_id"] == "550e8400-e29b-41d4-a716-446655440000"
    assert data["receipt_id"] == 1
    assert collections == [ChromaDBCollection.LINES, ChromaDBCollection.WORDS]


def test_extract_entity_data_receipt_word_label() -> None:
    """Test extracting data from ReceiptWordLabel."""
    entity = _make_word_label()
    data, collections = _extract_entity_data("RECEIPT_WORD_LABEL", entity)

    assert data["entity_type"] == "RECEIPT_WORD_LABEL"
    assert data["image_id"] == "550e8400-e29b-41d4-a716-446655440000"
    assert data["receipt_id"] == 1
    assert data["line_id"] == 1
    assert data["word_id"] == 1
    assert data["label"] == "TOTAL"
    assert collections == [ChromaDBCollection.WORDS]


def test_extract_entity_data_none_entity() -> None:
    """Test with None entity."""
    data, collections = _extract_entity_data("RECEIPT_PLACE", None)
    assert data == {}
    assert collections == []


def test_extract_entity_data_mismatched_type() -> None:
    """Test with mismatched entity type."""
    entity = _make_place()
    # Pass ReceiptPlace with RECEIPT_WORD_LABEL type
    data, collections = _extract_entity_data("RECEIPT_WORD_LABEL", entity)
    assert data == {}
    assert collections == []


def test_extract_entity_data_unknown_type() -> None:
    """Test with unknown entity type."""
    entity = _make_place()
    data, collections = _extract_entity_data("UNKNOWN_TYPE", entity)
    assert data == {}
    assert collections == []


def test_extract_entity_data_receipt() -> None:
    """Test extracting data from Receipt for deletion."""
    entity = _make_receipt()
    data, collections = _extract_entity_data("RECEIPT", entity)

    assert data["entity_type"] == "RECEIPT"
    assert data["image_id"] == "550e8400-e29b-41d4-a716-446655440000"
    assert data["receipt_id"] == 1
    # RECEIPT deletion affects both collections
    assert collections == [ChromaDBCollection.LINES, ChromaDBCollection.WORDS]


def test_extract_entity_data_receipt_word() -> None:
    """Test extracting data from ReceiptWord for deletion."""
    entity = _make_receipt_word()
    data, collections = _extract_entity_data("RECEIPT_WORD", entity)

    assert data["entity_type"] == "RECEIPT_WORD"
    assert data["image_id"] == "550e8400-e29b-41d4-a716-446655440000"
    assert data["receipt_id"] == 1
    assert data["line_id"] == 2
    assert data["word_id"] == 3
    # RECEIPT_WORD only affects WORDS collection
    assert collections == [ChromaDBCollection.WORDS]


def test_extract_entity_data_receipt_line() -> None:
    """Test extracting data from ReceiptLine for deletion."""
    entity = _make_receipt_line()
    data, collections = _extract_entity_data("RECEIPT_LINE", entity)

    assert data["entity_type"] == "RECEIPT_LINE"
    assert data["image_id"] == "550e8400-e29b-41d4-a716-446655440000"
    assert data["receipt_id"] == 1
    assert data["line_id"] == 2
    # RECEIPT_LINE only affects LINES collection
    assert collections == [ChromaDBCollection.LINES]
