"""Additional edge case tests for sqs_publisher module."""
import os
from datetime import datetime
from typing import Any, Mapping, Optional
from unittest.mock import Mock, patch

import pytest

from receipt_dynamo_stream.models import (
    ChromaDBCollection,
    FieldChange,
    StreamMessage,
)
from receipt_dynamo_stream.sqs_publisher import (
    _message_to_dict,
    publish_messages,
    send_batch_to_queue,
)


class MockMetrics:
    """Mock metrics recorder for testing."""

    def __init__(self) -> None:
        self.counts: list[tuple[str, int, Optional[Mapping[str, str]]]] = []

    def count(
        self,
        name: str,
        value: int,
        dimensions: Optional[Mapping[str, str]] = None,
    ) -> object:
        self.counts.append((name, value, dimensions))
        return None


def _create_test_message(
    entity_type: str = "RECEIPT_METADATA",
    collections: tuple[ChromaDBCollection, ...] = (
        ChromaDBCollection.LINES,
        ChromaDBCollection.WORDS,
    ),
    **kwargs: Any,
) -> StreamMessage:
    """Helper to create test StreamMessage."""
    defaults = {
        "entity_type": entity_type,
        "entity_data": {"image_id": "img-1", "receipt_id": 1},
        "changes": {"field": FieldChange(old="old", new="new")},
        "event_name": "MODIFY",
        "collections": collections,
        "timestamp": datetime.now().isoformat(),
        "stream_record_id": "event-1",
        "aws_region": "us-east-1",
    }
    defaults.update(kwargs)
    return StreamMessage(**defaults)


# Test _message_to_dict


def test_message_to_dict_basic() -> None:
    """Test basic message to dict conversion."""
    msg = _create_test_message()
    result = _message_to_dict(msg)

    assert result["source"] == "dynamodb_stream"
    assert result["entity_type"] == "RECEIPT_METADATA"
    assert result["event_name"] == "MODIFY"
    assert "changes" in result
    assert isinstance(result["changes"], dict)


def test_message_to_dict_empty_changes() -> None:
    """Test message with no changes."""
    msg = _create_test_message(changes={})
    result = _message_to_dict(msg)

    assert result["changes"] == {}


def test_message_to_dict_multiple_changes() -> None:
    """Test message with multiple field changes."""
    msg = _create_test_message(
        changes={
            "field1": FieldChange(old="old1", new="new1"),
            "field2": FieldChange(old=None, new="new2"),
            "field3": FieldChange(old="old3", new=None),
        }
    )
    result = _message_to_dict(msg)

    assert len(result["changes"]) == 3
    assert result["changes"]["field1"]["old"] == "old1"
    assert result["changes"]["field1"]["new"] == "new1"
    assert result["changes"]["field2"]["old"] is None
    assert result["changes"]["field2"]["new"] == "new2"
    assert result["changes"]["field3"]["old"] == "old3"
    assert result["changes"]["field3"]["new"] is None


def test_message_to_dict_none_optional_fields() -> None:
    """Test message with None optional fields."""
    msg = StreamMessage(
        entity_type="TEST",
        entity_data={},
        changes={},
        event_name="TEST",
        collections=(ChromaDBCollection.LINES,),
        timestamp=None,
        stream_record_id=None,
        aws_region=None,
    )
    result = _message_to_dict(msg)

    assert result["timestamp"] is None
    assert result["stream_record_id"] is None
    assert result["aws_region"] is None


# Test publish_messages


@patch("receipt_dynamo_stream.sqs_publisher.boto3.client")
def test_publish_messages_empty_list(mock_boto_client: Mock) -> None:
    """Test publishing empty message list."""
    sent = publish_messages([])
    assert sent == 0
    mock_boto_client.assert_called_once_with("sqs")


@patch("receipt_dynamo_stream.sqs_publisher.boto3.client")
def test_publish_messages_single_collection(mock_boto_client: Mock) -> None:
    """Test message targeting single collection."""
    os.environ["WORDS_QUEUE_URL"] = "https://queue.amazonaws.com/words"

    mock_sqs = Mock()
    mock_sqs.send_message_batch.return_value = {"Successful": [{"Id": "0"}]}
    mock_boto_client.return_value = mock_sqs

    msg = _create_test_message(
        collections=(ChromaDBCollection.WORDS,)
    )
    sent = publish_messages([msg])

    assert sent == 1
    assert mock_sqs.send_message_batch.call_count == 1

    os.environ.pop("WORDS_QUEUE_URL", None)


@patch("receipt_dynamo_stream.sqs_publisher.boto3.client")
def test_publish_messages_both_collections(mock_boto_client: Mock) -> None:
    """Test message targeting both collections."""
    os.environ["LINES_QUEUE_URL"] = "https://queue.amazonaws.com/lines"
    os.environ["WORDS_QUEUE_URL"] = "https://queue.amazonaws.com/words"

    mock_sqs = Mock()
    mock_sqs.send_message_batch.return_value = {"Successful": [{"Id": "0"}]}
    mock_boto_client.return_value = mock_sqs

    msg = _create_test_message(
        collections=(ChromaDBCollection.LINES, ChromaDBCollection.WORDS)
    )
    sent = publish_messages([msg])

    assert sent == 2
    assert mock_sqs.send_message_batch.call_count == 2

    os.environ.pop("LINES_QUEUE_URL", None)
    os.environ.pop("WORDS_QUEUE_URL", None)


# Test send_batch_to_queue


def test_send_batch_to_queue_missing_queue_url() -> None:
    """Test when queue URL is not in environment."""
    mock_sqs = Mock()
    os.environ.pop("TEST_QUEUE_URL", None)

    sent = send_batch_to_queue(
        mock_sqs,
        [],
        "TEST_QUEUE_URL",
        ChromaDBCollection.LINES,
    )

    assert sent == 0
    mock_sqs.send_message_batch.assert_not_called()


def test_send_batch_to_queue_compaction_run_message_group() -> None:
    """Test message group ID for COMPACTION_RUN."""
    os.environ["TEST_QUEUE_URL"] = "https://queue.amazonaws.com/test"

    mock_sqs = Mock()
    mock_sqs.send_message_batch.return_value = {"Successful": [{"Id": "0"}]}

    msg = _create_test_message(
        entity_type="COMPACTION_RUN",
        entity_data={
            "run_id": "run-123",
            "image_id": "img-456",
            "receipt_id": 1,
        },
    )
    msg_dict = _message_to_dict(msg)

    sent = send_batch_to_queue(
        mock_sqs,
        [(msg_dict, ChromaDBCollection.LINES)],
        "TEST_QUEUE_URL",
        ChromaDBCollection.LINES,
    )

    assert sent == 1
    call_args = mock_sqs.send_message_batch.call_args
    entries = call_args[1]["Entries"]
    assert entries[0]["MessageGroupId"] == "COMPACTION_RUN:img-456:lines"

    os.environ.pop("TEST_QUEUE_URL", None)


def test_send_batch_to_queue_receipt_metadata_message_group() -> None:
    """Test message group ID for RECEIPT_METADATA."""
    os.environ["TEST_QUEUE_URL"] = "https://queue.amazonaws.com/test"

    mock_sqs = Mock()
    mock_sqs.send_message_batch.return_value = {"Successful": [{"Id": "0"}]}

    msg = _create_test_message(
        entity_type="RECEIPT_METADATA",
        entity_data={"image_id": "img-789", "receipt_id": 1},
    )
    msg_dict = _message_to_dict(msg)

    sent = send_batch_to_queue(
        mock_sqs,
        [(msg_dict, ChromaDBCollection.WORDS)],
        "TEST_QUEUE_URL",
        ChromaDBCollection.WORDS,
    )

    assert sent == 1
    call_args = mock_sqs.send_message_batch.call_args
    entries = call_args[1]["Entries"]
    assert entries[0]["MessageGroupId"] == "COMPACTION_RUN:img-789:words"

    os.environ.pop("TEST_QUEUE_URL", None)


def test_send_batch_to_queue_unknown_entity_type_fallback() -> None:
    """Test message group ID fallback for unknown entity type."""
    os.environ["TEST_QUEUE_URL"] = "https://queue.amazonaws.com/test"

    mock_sqs = Mock()
    mock_sqs.send_message_batch.return_value = {"Successful": [{"Id": "0"}]}

    msg = _create_test_message(
        entity_type="UNKNOWN_TYPE",
        entity_data={"image_id": "img-abc"},
    )
    msg_dict = _message_to_dict(msg)

    sent = send_batch_to_queue(
        mock_sqs,
        [(msg_dict, ChromaDBCollection.LINES)],
        "TEST_QUEUE_URL",
        ChromaDBCollection.LINES,
    )

    assert sent == 1
    call_args = mock_sqs.send_message_batch.call_args
    entries = call_args[1]["Entries"]
    # Should use image_id as fallback
    assert entries[0]["MessageGroupId"] == "UNKNOWN_TYPE:img-abc:lines"

    os.environ.pop("TEST_QUEUE_URL", None)


def test_send_batch_to_queue_missing_entity_data_fields() -> None:
    """Test message group ID when entity_data is missing expected fields."""
    os.environ["TEST_QUEUE_URL"] = "https://queue.amazonaws.com/test"

    mock_sqs = Mock()
    mock_sqs.send_message_batch.return_value = {"Successful": [{"Id": "0"}]}

    msg = _create_test_message(
        entity_type="UNKNOWN_TYPE",
        entity_data={},  # No fields
    )
    msg_dict = _message_to_dict(msg)

    sent = send_batch_to_queue(
        mock_sqs,
        [(msg_dict, ChromaDBCollection.LINES)],
        "TEST_QUEUE_URL",
        ChromaDBCollection.LINES,
    )

    assert sent == 1
    call_args = mock_sqs.send_message_batch.call_args
    entries = call_args[1]["Entries"]
    # Should use 'default' as final fallback
    assert entries[0]["MessageGroupId"] == "UNKNOWN_TYPE:default:lines"

    os.environ.pop("TEST_QUEUE_URL", None)


def test_send_batch_to_queue_batching() -> None:
    """Test that messages are batched in groups of 10."""
    os.environ["TEST_QUEUE_URL"] = "https://queue.amazonaws.com/test"

    mock_sqs = Mock()
    # Return 10 successful for each call
    mock_sqs.send_message_batch.return_value = {
        "Successful": [{"Id": str(i)} for i in range(10)]
    }

    # Create 25 messages
    messages = []
    for i in range(25):
        msg = _create_test_message(
            entity_data={"image_id": f"img-{i}", "receipt_id": i}
        )
        msg_dict = _message_to_dict(msg)
        messages.append((msg_dict, ChromaDBCollection.LINES))

    sent = send_batch_to_queue(
        mock_sqs,
        messages,
        "TEST_QUEUE_URL",
        ChromaDBCollection.LINES,
    )

    # Should make 3 calls (10 + 10 + 5)
    assert mock_sqs.send_message_batch.call_count == 3
    assert sent == 30  # 10 + 10 + 10 (mocked to return 10 each time)

    os.environ.pop("TEST_QUEUE_URL", None)


def test_send_batch_to_queue_with_metrics() -> None:
    """Test that metrics are recorded."""
    os.environ["TEST_QUEUE_URL"] = "https://queue.amazonaws.com/test"
    metrics = MockMetrics()

    mock_sqs = Mock()
    mock_sqs.send_message_batch.return_value = {"Successful": [{"Id": "0"}]}

    msg = _create_test_message()
    msg_dict = _message_to_dict(msg)

    sent = send_batch_to_queue(
        mock_sqs,
        [(msg_dict, ChromaDBCollection.LINES)],
        "TEST_QUEUE_URL",
        ChromaDBCollection.LINES,
        metrics,
    )

    assert sent == 1
    metric_names = [m[0] for m in metrics.counts]
    assert "SQSMessagesSuccessful" in metric_names

    os.environ.pop("TEST_QUEUE_URL", None)


def test_send_batch_to_queue_failure_with_metrics() -> None:
    """Test that failure metrics are recorded."""
    os.environ["TEST_QUEUE_URL"] = "https://queue.amazonaws.com/test"
    metrics = MockMetrics()

    mock_sqs = Mock()
    mock_sqs.send_message_batch.side_effect = Exception("SQS Error")

    msg = _create_test_message()
    msg_dict = _message_to_dict(msg)

    sent = send_batch_to_queue(
        mock_sqs,
        [(msg_dict, ChromaDBCollection.LINES)],
        "TEST_QUEUE_URL",
        ChromaDBCollection.LINES,
        metrics,
    )

    assert sent == 0
    metric_names = [m[0] for m in metrics.counts]
    assert "SQSMessagesFailed" in metric_names

    os.environ.pop("TEST_QUEUE_URL", None)


def test_send_batch_to_queue_message_attributes() -> None:
    """Test that message attributes are set correctly."""
    os.environ["TEST_QUEUE_URL"] = "https://queue.amazonaws.com/test"

    mock_sqs = Mock()
    mock_sqs.send_message_batch.return_value = {"Successful": [{"Id": "0"}]}

    msg = _create_test_message()
    msg_dict = _message_to_dict(msg)

    send_batch_to_queue(
        mock_sqs,
        [(msg_dict, ChromaDBCollection.WORDS)],
        "TEST_QUEUE_URL",
        ChromaDBCollection.WORDS,
    )

    call_args = mock_sqs.send_message_batch.call_args
    entries = call_args[1]["Entries"]
    attrs = entries[0]["MessageAttributes"]

    assert attrs["source"]["StringValue"] == "dynamodb_stream"
    assert attrs["entity_type"]["StringValue"] == "RECEIPT_METADATA"
    assert attrs["event_name"]["StringValue"] == "MODIFY"
    assert attrs["collection"]["StringValue"] == "words"

    os.environ.pop("TEST_QUEUE_URL", None)
