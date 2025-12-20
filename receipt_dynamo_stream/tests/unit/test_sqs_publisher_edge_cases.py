"""Additional edge case tests for sqs_publisher module."""

from datetime import datetime
from typing import Any
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

from .conftest import MockMetrics


@pytest.fixture
def env_words_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set WORDS_QUEUE_URL environment variable."""
    monkeypatch.setenv("WORDS_QUEUE_URL", "https://queue.amazonaws.com/words")


@pytest.fixture
def env_lines_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set LINES_QUEUE_URL environment variable."""
    monkeypatch.setenv("LINES_QUEUE_URL", "https://queue.amazonaws.com/lines")


@pytest.fixture
def env_both_queues(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set both LINES_QUEUE_URL and WORDS_QUEUE_URL environment variables."""
    monkeypatch.setenv("LINES_QUEUE_URL", "https://queue.amazonaws.com/lines")
    monkeypatch.setenv("WORDS_QUEUE_URL", "https://queue.amazonaws.com/words")


@pytest.fixture
def env_test_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set TEST_QUEUE_URL environment variable."""
    monkeypatch.setenv("TEST_QUEUE_URL", "https://queue.amazonaws.com/test")


def _create_test_message(
    entity_type: str = "RECEIPT_PLACE",
    collections: tuple[ChromaDBCollection, ...] = (
        ChromaDBCollection.LINES,
        ChromaDBCollection.WORDS,
    ),
    **kwargs: Any,
) -> StreamMessage:
    """Helper to create test StreamMessage."""
    defaults: dict[str, Any] = {
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
    return StreamMessage(**defaults)  # type: ignore[arg-type]


# Test _message_to_dict


def test_message_to_dict_basic() -> None:
    """Test basic message to dict conversion."""
    msg = _create_test_message()
    result = _message_to_dict(msg)

    assert result["source"] == "dynamodb_stream"
    assert result["entity_type"] == "RECEIPT_PLACE"
    assert result["event_name"] == "MODIFY"
    assert "changes" in result
    assert isinstance(result["changes"], dict)


def test_message_to_dict_empty_changes() -> None:
    """Test message with no changes."""
    msg = _create_test_message(changes={})
    result = _message_to_dict(msg)

    assert not result["changes"]  # type: ignore[truthy-bool]


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
    changes = result["changes"]

    assert isinstance(changes, dict)
    assert len(changes) == 3
    assert changes["field1"]["old"] == "old1"  # type: ignore[index]
    assert changes["field1"]["new"] == "new1"  # type: ignore[index]
    assert changes["field2"]["old"] is None  # type: ignore[index]
    assert changes["field2"]["new"] == "new2"  # type: ignore[index]
    assert changes["field3"]["old"] == "old3"  # type: ignore[index]
    assert changes["field3"]["new"] is None  # type: ignore[index]


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
def test_publish_messages_single_collection(
    mock_boto_client: Mock, env_words_queue: None
) -> None:
    """Test message targeting single collection."""
    mock_sqs = Mock()
    mock_sqs.send_message_batch.return_value = {"Successful": [{"Id": "0"}]}
    mock_boto_client.return_value = mock_sqs

    msg = _create_test_message(collections=(ChromaDBCollection.WORDS,))
    sent = publish_messages([msg])

    assert sent == 1
    assert mock_sqs.send_message_batch.call_count == 1


@patch("receipt_dynamo_stream.sqs_publisher.boto3.client")
def test_publish_messages_both_collections(
    mock_boto_client: Mock, env_both_queues: None
) -> None:
    """Test message targeting both collections."""
    mock_sqs = Mock()
    mock_sqs.send_message_batch.return_value = {"Successful": [{"Id": "0"}]}
    mock_boto_client.return_value = mock_sqs

    msg = _create_test_message(
        collections=(ChromaDBCollection.LINES, ChromaDBCollection.WORDS)
    )
    sent = publish_messages([msg])

    assert sent == 2
    assert mock_sqs.send_message_batch.call_count == 2


# Test send_batch_to_queue


def test_send_batch_to_queue_missing_queue_url() -> None:
    """Test when queue URL is not in environment."""
    mock_sqs = Mock()

    sent = send_batch_to_queue(
        mock_sqs,
        [],
        "TEST_QUEUE_URL",
        ChromaDBCollection.LINES,
    )

    assert sent == 0
    mock_sqs.send_message_batch.assert_not_called()


def test_send_batch_to_queue_compaction_run_message_group(
    env_test_queue: None,
) -> None:
    """Test message group ID for COMPACTION_RUN."""
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


def test_send_batch_to_queue_receipt_metadata_message_group(
    env_test_queue: None,
) -> None:
    """Test message group ID for RECEIPT_PLACE."""
    mock_sqs = Mock()
    mock_sqs.send_message_batch.return_value = {"Successful": [{"Id": "0"}]}

    msg = _create_test_message(
        entity_type="RECEIPT_PLACE",
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


def test_send_batch_to_queue_unknown_entity_type_fallback(
    env_test_queue: None,
) -> None:
    """Test message group ID fallback for unknown entity type."""
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


def test_send_batch_to_queue_missing_entity_data_fields(
    env_test_queue: None,
) -> None:
    """Test message group ID when entity_data is missing expected fields."""
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


def test_send_batch_to_queue_batching(
    env_test_queue: None,
) -> None:
    """Test that messages are batched in groups of 10."""
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


def test_send_batch_to_queue_with_metrics(
    env_test_queue: None,
) -> None:
    """Test that metrics are recorded."""
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


def test_send_batch_to_queue_failure_with_metrics(
    env_test_queue: None,
) -> None:
    """Test that failure metrics are recorded."""
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


def test_send_batch_to_queue_message_attributes(
    env_test_queue: None,
) -> None:
    """Test that message attributes are set correctly."""
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
    assert attrs["entity_type"]["StringValue"] == "RECEIPT_PLACE"
    assert attrs["event_name"]["StringValue"] == "MODIFY"
    assert attrs["collection"]["StringValue"] == "words"
