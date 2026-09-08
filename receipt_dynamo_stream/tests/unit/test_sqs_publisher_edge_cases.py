"""Additional edge case tests for sqs_publisher module."""

from datetime import datetime
from typing import Any
from unittest.mock import Mock, patch

import pytest
from botocore.exceptions import ClientError

from receipt_dynamo_stream.exceptions import (
    QueueBatchFailureError,
    QueueConfigurationError,
    QueueServiceError,
)
from receipt_dynamo_stream.models import (
    FieldChange,
    StreamMessage,
    StreamRecordContext,
    TargetQueue,
)
from receipt_dynamo_stream.sqs_publisher import (
    _message_to_dict,
    publish_messages,
    send_batch_to_queue,
)

from .conftest import MockMetrics


@pytest.fixture
def env_line_item_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set LINE_ITEM_QUEUE_URL environment variable."""
    monkeypatch.setenv(
        "LINE_ITEM_QUEUE_URL", "https://queue.amazonaws.com/line-items"
    )


@pytest.fixture
def env_summary_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set RECEIPT_SUMMARY_QUEUE_URL environment variable."""
    monkeypatch.setenv(
        "RECEIPT_SUMMARY_QUEUE_URL", "https://queue.amazonaws.com/summary"
    )


@pytest.fixture
def env_all_queues(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set both surviving queue URLs."""
    monkeypatch.setenv(
        "LINE_ITEM_QUEUE_URL", "https://queue.amazonaws.com/line-items"
    )
    monkeypatch.setenv(
        "RECEIPT_SUMMARY_QUEUE_URL", "https://queue.amazonaws.com/summary"
    )


@pytest.fixture
def env_test_queue(monkeypatch: pytest.MonkeyPatch) -> None:
    """Set TEST_QUEUE_URL environment variable."""
    monkeypatch.setenv("TEST_QUEUE_URL", "https://queue.amazonaws.com/test")


def _create_test_message(
    entity_type: str = "RECEIPT_PLACE",
    collections: tuple[TargetQueue, ...] = (
        TargetQueue.RECEIPT_SUMMARY,
        TargetQueue.LINE_ITEMS,
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
        "context": StreamRecordContext(
            timestamp=datetime.now().isoformat(),
            record_id="event-1",
            aws_region="us-east-1",
        ),
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
    """Test message with None optional fields in context."""
    msg = StreamMessage(
        entity_type="TEST",
        entity_data={},
        changes={},
        event_name="TEST",
        collections=(TargetQueue.RECEIPT_SUMMARY,),
        context=StreamRecordContext(
            timestamp=None,
            record_id=None,
            aws_region=None,
        ),
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
def test_publish_messages_single_queue(
    mock_boto_client: Mock, env_line_item_queue: None
) -> None:
    """Test message targeting a single queue."""
    mock_sqs = Mock()
    mock_sqs.send_message_batch.return_value = {"Successful": [{"Id": "0"}]}
    mock_boto_client.return_value = mock_sqs

    msg = _create_test_message(collections=(TargetQueue.LINE_ITEMS,))
    sent = publish_messages([msg])

    assert sent == 1
    assert mock_sqs.send_message_batch.call_count == 1


@patch("receipt_dynamo_stream.sqs_publisher.boto3.client")
def test_publish_messages_both_queues(
    mock_boto_client: Mock, env_all_queues: None
) -> None:
    """Test message targeting both surviving queues."""
    mock_sqs = Mock()
    mock_sqs.send_message_batch.return_value = {"Successful": [{"Id": "0"}]}
    mock_boto_client.return_value = mock_sqs

    msg = _create_test_message(
        collections=(TargetQueue.RECEIPT_SUMMARY, TargetQueue.LINE_ITEMS)
    )
    sent = publish_messages([msg])

    assert sent == 2
    assert mock_sqs.send_message_batch.call_count == 2


@patch("receipt_dynamo_stream.sqs_publisher.boto3.client")
def test_publish_messages_summary_queue_only(
    mock_boto_client: Mock, env_summary_queue: None
) -> None:
    """Test message targeting only summary queue."""
    mock_sqs = Mock()
    mock_sqs.send_message_batch.return_value = {"Successful": [{"Id": "0"}]}
    mock_boto_client.return_value = mock_sqs

    msg = _create_test_message(collections=(TargetQueue.RECEIPT_SUMMARY,))
    sent = publish_messages([msg])

    assert sent == 1
    assert mock_sqs.send_message_batch.call_count == 1


def test_send_batch_to_queue_summary_queue(
    env_summary_queue: None,
) -> None:
    """Test sending to summary queue with TargetQueue type."""
    mock_sqs = Mock()
    mock_sqs.send_message_batch.return_value = {"Successful": [{"Id": "0"}]}

    msg = _create_test_message(
        entity_type="RECEIPT_WORD_LABEL",
        entity_data={"image_id": "img-123", "receipt_id": 1},
    )
    msg_dict = _message_to_dict(msg)

    sent = send_batch_to_queue(
        mock_sqs,
        [(msg_dict, TargetQueue.RECEIPT_SUMMARY)],
        "RECEIPT_SUMMARY_QUEUE_URL",
        TargetQueue.RECEIPT_SUMMARY,
    )

    assert sent == 1
    call_args = mock_sqs.send_message_batch.call_args
    entries = call_args[1]["Entries"]
    attrs = entries[0]["MessageAttributes"]
    # Should use TargetQueue.RECEIPT_SUMMARY.value for collection attribute
    assert attrs["collection"]["StringValue"] == "receipt_summary"


# Test send_batch_to_queue


def test_send_batch_to_queue_missing_queue_url() -> None:
    """Test when queue URL is not in environment."""
    mock_sqs = Mock()

    with pytest.raises(QueueConfigurationError) as caught:
        send_batch_to_queue(
            mock_sqs,
            [],
            "TEST_QUEUE_URL",
            TargetQueue.RECEIPT_SUMMARY,
        )

    assert type(caught.value) is QueueConfigurationError
    assert str(caught.value) == (
        "Queue URL for 'receipt_summary' is not configured; "
        "set TEST_QUEUE_URL"
    )
    assert caught.value.environment_variable == "TEST_QUEUE_URL"
    assert caught.value.queue_name == "receipt_summary"
    assert caught.value.__cause__ is None
    mock_sqs.send_message_batch.assert_not_called()


def test_send_batch_to_queue_receipt_place_no_message_group_id(
    env_test_queue: None,
) -> None:
    """Test that Standard queues don't use MessageGroupId for RECEIPT_PLACE."""
    mock_sqs = Mock()
    mock_sqs.send_message_batch.return_value = {"Successful": [{"Id": "0"}]}

    msg = _create_test_message(
        entity_type="RECEIPT_PLACE",
        entity_data={"image_id": "img-789", "receipt_id": 1},
    )
    msg_dict = _message_to_dict(msg)

    sent = send_batch_to_queue(
        mock_sqs,
        [(msg_dict, TargetQueue.LINE_ITEMS)],
        "TEST_QUEUE_URL",
        TargetQueue.LINE_ITEMS,
    )

    assert sent == 1
    call_args = mock_sqs.send_message_batch.call_args
    entries = call_args[1]["Entries"]
    # Standard queues don't use MessageGroupId - Lambda handles ordering
    assert "MessageGroupId" not in entries[0]


def test_send_batch_to_queue_unknown_entity_type_no_message_group_id(
    env_test_queue: None,
) -> None:
    """Test that Standard queues don't use MessageGroupId for unknown types."""
    mock_sqs = Mock()
    mock_sqs.send_message_batch.return_value = {"Successful": [{"Id": "0"}]}

    msg = _create_test_message(
        entity_type="UNKNOWN_TYPE",
        entity_data={"image_id": "img-abc"},
    )
    msg_dict = _message_to_dict(msg)

    sent = send_batch_to_queue(
        mock_sqs,
        [(msg_dict, TargetQueue.RECEIPT_SUMMARY)],
        "TEST_QUEUE_URL",
        TargetQueue.RECEIPT_SUMMARY,
    )

    assert sent == 1
    call_args = mock_sqs.send_message_batch.call_args
    entries = call_args[1]["Entries"]
    # Standard queues don't use MessageGroupId - Lambda handles ordering
    assert "MessageGroupId" not in entries[0]


def test_send_batch_to_queue_missing_entity_data_no_message_group_id(
    env_test_queue: None,
) -> None:
    """Test that Standard queues don't use MessageGroupId even with empty data."""
    mock_sqs = Mock()
    mock_sqs.send_message_batch.return_value = {"Successful": [{"Id": "0"}]}

    msg = _create_test_message(
        entity_type="UNKNOWN_TYPE",
        entity_data={},  # No fields
    )
    msg_dict = _message_to_dict(msg)

    sent = send_batch_to_queue(
        mock_sqs,
        [(msg_dict, TargetQueue.RECEIPT_SUMMARY)],
        "TEST_QUEUE_URL",
        TargetQueue.RECEIPT_SUMMARY,
    )

    assert sent == 1
    call_args = mock_sqs.send_message_batch.call_args
    entries = call_args[1]["Entries"]
    # Standard queues don't use MessageGroupId - Lambda handles ordering
    assert "MessageGroupId" not in entries[0]


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
        messages.append((msg_dict, TargetQueue.RECEIPT_SUMMARY))

    sent = send_batch_to_queue(
        mock_sqs,
        messages,
        "TEST_QUEUE_URL",
        TargetQueue.RECEIPT_SUMMARY,
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
        [(msg_dict, TargetQueue.RECEIPT_SUMMARY)],
        "TEST_QUEUE_URL",
        TargetQueue.RECEIPT_SUMMARY,
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
    mock_sqs.send_message_batch.side_effect = ClientError(
        {"Error": {"Code": "ServiceUnavailable", "Message": "SQS Error"}},
        "SendMessageBatch",
    )

    msg = _create_test_message()
    msg_dict = _message_to_dict(msg)

    with pytest.raises(QueueServiceError) as caught:
        send_batch_to_queue(
            mock_sqs,
            [(msg_dict, TargetQueue.RECEIPT_SUMMARY)],
            "TEST_QUEUE_URL",
            TargetQueue.RECEIPT_SUMMARY,
            metrics,
        )

    assert type(caught.value) is QueueServiceError
    assert str(caught.value) == (
        "Failed to send batch of 1 message(s) to 'receipt_summary' queue"
    )
    assert isinstance(caught.value.__cause__, ClientError)
    metric_names = [m[0] for m in metrics.counts]
    assert "SQSMessagesFailed" in metric_names


def test_send_batch_to_queue_surfaces_partial_failure(
    env_test_queue: None,
) -> None:
    """SQS batch-level failures must not look like partial success."""
    mock_sqs = Mock()
    failed = [{"Id": "0", "Code": "Throttled", "SenderFault": False}]
    mock_sqs.send_message_batch.return_value = {
        "Successful": [],
        "Failed": failed,
    }
    msg_dict = _message_to_dict(_create_test_message())

    with pytest.raises(QueueBatchFailureError) as caught:
        send_batch_to_queue(
            mock_sqs,
            [(msg_dict, TargetQueue.RECEIPT_SUMMARY)],
            "TEST_QUEUE_URL",
            TargetQueue.RECEIPT_SUMMARY,
        )

    assert type(caught.value) is QueueBatchFailureError
    assert str(caught.value) == (
        "SQS rejected 1 message(s) for 'receipt_summary' queue"
    )
    assert caught.value.failed_entries == failed
    assert caught.value.__cause__ is None


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
        [(msg_dict, TargetQueue.LINE_ITEMS)],
        "TEST_QUEUE_URL",
        TargetQueue.LINE_ITEMS,
    )

    call_args = mock_sqs.send_message_batch.call_args
    entries = call_args[1]["Entries"]
    attrs = entries[0]["MessageAttributes"]

    assert attrs["source"]["StringValue"] == "dynamodb_stream"
    assert attrs["entity_type"]["StringValue"] == "RECEIPT_PLACE"
    assert attrs["event_name"]["StringValue"] == "MODIFY"
    assert attrs["collection"]["StringValue"] == "line_items"
