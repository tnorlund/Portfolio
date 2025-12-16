"""Unit tests for the refactored enhanced_compaction_handler."""

import os
import pytest
from unittest.mock import MagicMock, patch, call
from datetime import datetime
from uuid import uuid4


@pytest.fixture
def mock_env():
    """Set up environment variables for testing."""
    env_vars = {
        "CHROMADB_BUCKET": "test-bucket",
        "DYNAMODB_TABLE_NAME": "test-table",
        "CHROMADB_STORAGE_MODE": "s3",
        "CHROMA_ROOT": "/tmp/chroma",
        "LOG_LEVEL": "INFO",
    }
    with patch.dict(os.environ, env_vars, clear=False):
        yield env_vars


@pytest.fixture
def mock_logger():
    """Create a mock logger."""
    logger = MagicMock()
    logger.operation_timer = MagicMock()
    logger.operation_timer.return_value.__enter__ = MagicMock()
    logger.operation_timer.return_value.__exit__ = MagicMock()
    return logger


@pytest.fixture
def mock_metrics():
    """Create a mock metrics accumulator."""
    metrics = MagicMock()
    return metrics


@pytest.fixture
def sample_sqs_event():
    """Create a sample SQS event."""
    return {
        "Records": [
            {
                "messageId": "msg-1",
                "body": '{"entity_type": "RECEIPT_METADATA", "entity_data": {"image_id": "' + str(uuid4()) + '", "receipt_id": 1}, "changes": {}, "event_name": "MODIFY", "collections": ["lines"], "timestamp": "2025-01-01T00:00:00Z", "stream_record_id": "record-1", "aws_region": "us-east-1"}',
            }
        ]
    }


@pytest.fixture
def sample_stream_message():
    """Create a sample StreamMessage for testing."""
    from receipt_dynamo_stream.models import StreamMessage
    from receipt_dynamo.constants import ChromaDBCollection

    return StreamMessage(
        entity_type="RECEIPT_METADATA",
        entity_data={"image_id": str(uuid4()), "receipt_id": 1},
        changes={},
        event_name="MODIFY",
        collections=(ChromaDBCollection.LINES,),
        timestamp=datetime.now().isoformat(),
        stream_record_id="test-record-1",
        aws_region="us-east-1",
    )


class TestProcessCollection:
    """Test the process_collection function."""

    @patch("enhanced_compaction_handler.LockManager")
    @patch("enhanced_compaction_handler.DynamoClient")
    @patch("enhanced_compaction_handler.ChromaClient")
    @patch("enhanced_compaction_handler.process_collection_updates")
    @patch("enhanced_compaction_handler.download_chroma_snapshot")
    @patch("enhanced_compaction_handler.upload_chroma_snapshot")
    @patch("enhanced_compaction_handler.tempfile.mkdtemp")
    @patch("enhanced_compaction_handler.shutil.rmtree")
    def test_process_collection_success(
        self,
        mock_rmtree,
        mock_mkdtemp,
        mock_upload,
        mock_download,
        mock_process_updates,
        mock_chroma_client,
        mock_dynamo_client,
        mock_lock_manager,
        mock_env,
        mock_logger,
        mock_metrics,
        sample_stream_message,
    ):
        """Test successful collection processing."""
        from enhanced_compaction_handler import process_collection
        from receipt_dynamo.constants import ChromaDBCollection
        from receipt_chroma.compaction.models import CollectionUpdateResult

        # Setup mocks
        mock_mkdtemp.return_value = "/tmp/chroma-test"
        mock_download.return_value = {"status": "success", "version": "v1"}

        # Mock CollectionUpdateResult
        mock_result = MagicMock()
        mock_result.total_metadata_updated = 5
        mock_result.total_labels_updated = 3
        mock_result.delta_merge_count = 1
        mock_result.has_errors = False
        mock_result.metadata_updates = []
        mock_result.label_updates = []
        mock_process_updates.return_value = mock_result

        mock_upload.return_value = {"status": "success", "version": "v2"}

        # Mock ChromaClient
        mock_client = MagicMock()
        mock_chroma_client.return_value = mock_client

        # Call function
        result = process_collection(
            collection=ChromaDBCollection.LINES,
            messages=[sample_stream_message],
            logger=mock_logger,
            metrics=mock_metrics,
        )

        # Verify result
        assert result["status"] == "success"
        assert result["failed_message_ids"] == []

        # Verify download was called
        mock_download.assert_called_once()

        # Verify ChromaClient was created and closed
        mock_chroma_client.assert_called_once_with(
            persist_directory="/tmp/chroma-test",
            mode="write",
        )
        mock_client.close.assert_called_once()

        # Verify process_collection_updates was called
        mock_process_updates.assert_called_once()
        call_args = mock_process_updates.call_args
        assert call_args.kwargs["collection"] == ChromaDBCollection.LINES
        assert call_args.kwargs["chroma_client"] == mock_client

        # Verify upload was called
        mock_upload.assert_called_once()

        # Verify temp directory cleanup
        mock_rmtree.assert_called_once_with("/tmp/chroma-test", ignore_errors=True)

    @patch("enhanced_compaction_handler.LockManager")
    @patch("enhanced_compaction_handler.download_chroma_snapshot")
    @patch("enhanced_compaction_handler.tempfile.mkdtemp")
    @patch("enhanced_compaction_handler.shutil.rmtree")
    def test_process_collection_download_failure(
        self,
        mock_rmtree,
        mock_mkdtemp,
        mock_download,
        mock_lock_manager,
        mock_env,
        mock_logger,
        mock_metrics,
        sample_stream_message,
    ):
        """Test handling of snapshot download failure."""
        from enhanced_compaction_handler import process_collection
        from receipt_dynamo.constants import ChromaDBCollection

        # Setup mocks
        mock_mkdtemp.return_value = "/tmp/chroma-test"
        mock_download.return_value = {"status": "error", "error": "Download failed"}

        # Call function
        result = process_collection(
            collection=ChromaDBCollection.LINES,
            messages=[sample_stream_message],
            logger=mock_logger,
            metrics=mock_metrics,
        )

        # Verify error handling
        assert result["failed_message_ids"] == [sample_stream_message.stream_record_id]

        # Verify logger was called
        mock_logger.error.assert_called()

        # Verify metrics counter
        mock_metrics.count.assert_called_with("CompactionSnapshotDownloadError", 1)

        # Verify temp directory cleanup
        mock_rmtree.assert_called_once_with("/tmp/chroma-test", ignore_errors=True)

    @patch("enhanced_compaction_handler.LockManager")
    @patch("enhanced_compaction_handler.DynamoClient")
    @patch("enhanced_compaction_handler.ChromaClient")
    @patch("enhanced_compaction_handler.process_collection_updates")
    @patch("enhanced_compaction_handler.download_chroma_snapshot")
    @patch("enhanced_compaction_handler.upload_chroma_snapshot")
    @patch("enhanced_compaction_handler.tempfile.mkdtemp")
    @patch("enhanced_compaction_handler.shutil.rmtree")
    def test_process_collection_upload_failure(
        self,
        mock_rmtree,
        mock_mkdtemp,
        mock_upload,
        mock_download,
        mock_process_updates,
        mock_chroma_client,
        mock_dynamo_client,
        mock_lock_manager,
        mock_env,
        mock_logger,
        mock_metrics,
        sample_stream_message,
    ):
        """Test handling of snapshot upload failure."""
        from enhanced_compaction_handler import process_collection
        from receipt_dynamo.constants import ChromaDBCollection

        # Setup mocks
        mock_mkdtemp.return_value = "/tmp/chroma-test"
        mock_download.return_value = {"status": "success", "version": "v1"}

        mock_result = MagicMock()
        mock_result.has_errors = False
        mock_result.metadata_updates = []
        mock_result.label_updates = []
        mock_process_updates.return_value = mock_result

        mock_upload.return_value = {"status": "error", "error": "Upload failed"}

        mock_client = MagicMock()
        mock_chroma_client.return_value = mock_client

        # Call function
        result = process_collection(
            collection=ChromaDBCollection.LINES,
            messages=[sample_stream_message],
            logger=mock_logger,
            metrics=mock_metrics,
        )

        # Verify error handling
        assert result["failed_message_ids"] == [sample_stream_message.stream_record_id]

        # Verify logger was called
        mock_logger.error.assert_called()

        # Verify metrics counter
        mock_metrics.count.assert_called_with("CompactionSnapshotUploadError", 1)

        # Verify temp directory cleanup
        mock_rmtree.assert_called_once_with("/tmp/chroma-test", ignore_errors=True)

    @patch("enhanced_compaction_handler.LockManager")
    @patch("enhanced_compaction_handler.DynamoClient")
    @patch("enhanced_compaction_handler.ChromaClient")
    @patch("enhanced_compaction_handler.process_collection_updates")
    @patch("enhanced_compaction_handler.download_chroma_snapshot")
    @patch("enhanced_compaction_handler.upload_chroma_snapshot")
    @patch("enhanced_compaction_handler.tempfile.mkdtemp")
    @patch("enhanced_compaction_handler.shutil.rmtree")
    def test_process_collection_with_errors(
        self,
        mock_rmtree,
        mock_mkdtemp,
        mock_upload,
        mock_download,
        mock_process_updates,
        mock_chroma_client,
        mock_dynamo_client,
        mock_lock_manager,
        mock_env,
        mock_logger,
        mock_metrics,
    ):
        """Test handling of processing errors in updates."""
        from enhanced_compaction_handler import process_collection
        from receipt_dynamo.constants import ChromaDBCollection
        from receipt_dynamo_stream.models import StreamMessage

        # Create test messages
        test_image_id = str(uuid4())
        msg1 = StreamMessage(
            entity_type="RECEIPT_METADATA",
            entity_data={"image_id": test_image_id, "receipt_id": 1},
            changes={},
            event_name="MODIFY",
            collections=(ChromaDBCollection.LINES,),
            timestamp=datetime.now().isoformat(),
            stream_record_id="msg-1",
            aws_region="us-east-1",
        )

        # Setup mocks
        mock_mkdtemp.return_value = "/tmp/chroma-test"
        mock_download.return_value = {"status": "success", "version": "v1"}

        # Mock update result with errors
        mock_metadata_result = MagicMock()
        mock_metadata_result.error = "Update failed"
        mock_metadata_result.image_id = test_image_id
        mock_metadata_result.receipt_id = 1

        mock_result = MagicMock()
        mock_result.total_metadata_updated = 0
        mock_result.total_labels_updated = 0
        mock_result.delta_merge_count = 0
        mock_result.has_errors = True
        mock_result.metadata_updates = [mock_metadata_result]
        mock_result.label_updates = []
        mock_process_updates.return_value = mock_result

        mock_upload.return_value = {"status": "success", "version": "v2"}

        mock_client = MagicMock()
        mock_chroma_client.return_value = mock_client

        # Call function
        result = process_collection(
            collection=ChromaDBCollection.LINES,
            messages=[msg1],
            logger=mock_logger,
            metrics=mock_metrics,
        )

        # Verify failed messages
        assert "msg-1" in result["failed_message_ids"]

        # Verify temp directory cleanup
        mock_rmtree.assert_called_once_with("/tmp/chroma-test", ignore_errors=True)


class TestProcessSQSMessages:
    """Test the process_sqs_messages function."""

    @patch("enhanced_compaction_handler.parse_sqs_messages")
    @patch("enhanced_compaction_handler.process_collection")
    def test_process_sqs_messages_success(
        self,
        mock_process_collection,
        mock_parse_sqs_messages,
        mock_logger,
        mock_metrics,
        sample_stream_message,
    ):
        """Test successful SQS message processing."""
        from enhanced_compaction_handler import process_sqs_messages

        # Setup mocks
        mock_parse_sqs_messages.return_value = [sample_stream_message]
        mock_process_collection.return_value = {
            "status": "success",
            "failed_message_ids": [],
        }

        # Sample SQS records
        records = [
            {
                "messageId": "msg-1",
                "body": "test-body",
            }
        ]

        # Call function
        result = process_sqs_messages(
            records=records,
            logger=mock_logger,
            metrics=mock_metrics,
        )

        # Verify result
        assert result["batchItemFailures"] == []

        # Verify parse_sqs_messages was called
        mock_parse_sqs_messages.assert_called_once_with(records)

        # Verify process_collection was called
        mock_process_collection.assert_called_once()

    @patch("enhanced_compaction_handler.parse_sqs_messages")
    def test_process_sqs_messages_parse_error(
        self,
        mock_parse_sqs_messages,
        mock_logger,
        mock_metrics,
    ):
        """Test handling of message parsing errors."""
        from enhanced_compaction_handler import process_sqs_messages

        # Setup mocks to raise exception
        mock_parse_sqs_messages.side_effect = Exception("Parse error")

        # Sample SQS records
        records = [
            {"messageId": "msg-1", "body": "invalid-json"},
            {"messageId": "msg-2", "body": "invalid-json"},
        ]

        # Call function
        result = process_sqs_messages(
            records=records,
            logger=mock_logger,
            metrics=mock_metrics,
        )

        # Verify all messages marked as failures
        assert len(result["batchItemFailures"]) == 2
        assert result["batchItemFailures"][0]["itemIdentifier"] == "msg-1"
        assert result["batchItemFailures"][1]["itemIdentifier"] == "msg-2"

        # Verify error logging
        mock_logger.error.assert_called()

        # Verify metrics counter
        mock_metrics.count.assert_called_with("CompactionMessageParseError", 1)

    @patch("enhanced_compaction_handler.parse_sqs_messages")
    @patch("enhanced_compaction_handler.process_collection")
    def test_process_sqs_messages_collection_error(
        self,
        mock_process_collection,
        mock_parse_sqs_messages,
        mock_logger,
        mock_metrics,
        sample_stream_message,
    ):
        """Test handling of collection processing errors."""
        from enhanced_compaction_handler import process_sqs_messages

        # Setup mocks
        mock_parse_sqs_messages.return_value = [sample_stream_message]
        mock_process_collection.side_effect = Exception("Processing error")

        # Sample SQS records
        records = [{"messageId": "msg-1", "body": "test-body"}]

        # Call function
        result = process_sqs_messages(
            records=records,
            logger=mock_logger,
            metrics=mock_metrics,
        )

        # Verify messages marked as failures
        assert len(result["batchItemFailures"]) == 1

        # Verify error logging
        mock_logger.error.assert_called()

    @patch("enhanced_compaction_handler.parse_sqs_messages")
    @patch("enhanced_compaction_handler.process_collection")
    def test_process_sqs_messages_partial_failure(
        self,
        mock_process_collection,
        mock_parse_sqs_messages,
        mock_logger,
        mock_metrics,
    ):
        """Test handling of partial batch failures."""
        from enhanced_compaction_handler import process_sqs_messages
        from receipt_dynamo_stream.models import StreamMessage
        from receipt_dynamo.constants import ChromaDBCollection

        # Create test messages
        msg1 = StreamMessage(
            entity_type="RECEIPT_METADATA",
            entity_data={"image_id": str(uuid4()), "receipt_id": 1},
            changes={},
            event_name="MODIFY",
            collections=(ChromaDBCollection.LINES,),
            timestamp=datetime.now().isoformat(),
            stream_record_id="msg-1",
            aws_region="us-east-1",
        )
        msg2 = StreamMessage(
            entity_type="RECEIPT_METADATA",
            entity_data={"image_id": str(uuid4()), "receipt_id": 2},
            changes={},
            event_name="MODIFY",
            collections=(ChromaDBCollection.LINES,),
            timestamp=datetime.now().isoformat(),
            stream_record_id="msg-2",
            aws_region="us-east-1",
        )

        # Setup mocks
        mock_parse_sqs_messages.return_value = [msg1, msg2]
        mock_process_collection.return_value = {
            "status": "success",
            "failed_message_ids": ["msg-2"],  # Only msg-2 failed
        }

        # Sample SQS records
        records = [
            {"messageId": "msg-1", "body": "test-body-1"},
            {"messageId": "msg-2", "body": "test-body-2"},
        ]

        # Call function
        result = process_sqs_messages(
            records=records,
            logger=mock_logger,
            metrics=mock_metrics,
        )

        # Verify only failed message is returned
        assert len(result["batchItemFailures"]) == 1
        assert result["batchItemFailures"][0]["itemIdentifier"] == "msg-2"


class TestLambdaHandler:
    """Test the lambda_handler function."""

    @patch("enhanced_compaction_handler.process_sqs_messages")
    @patch("enhanced_compaction_handler.get_operation_logger")
    @patch("enhanced_compaction_handler.MetricsAccumulator")
    def test_lambda_handler_success(
        self,
        mock_metrics_class,
        mock_get_logger,
        mock_process_sqs,
        mock_env,
        sample_sqs_event,
    ):
        """Test successful Lambda handler execution."""
        from enhanced_compaction_handler import lambda_handler

        # Setup mocks
        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger

        mock_metrics = MagicMock()
        mock_metrics_class.return_value = mock_metrics

        mock_process_sqs.return_value = {"batchItemFailures": []}

        # Mock context
        mock_context = MagicMock()
        mock_context.function_name = "test-function"
        mock_context.invoked_function_arn = "arn:aws:lambda:us-east-1:123456789012:function:test-function"
        mock_context.aws_request_id = "test-request-id"

        # Call handler
        result = lambda_handler(sample_sqs_event, mock_context)

        # Verify result
        assert result["batchItemFailures"] == []

        # Verify logger was created
        mock_get_logger.assert_called_once()

        # Verify metrics were emitted
        mock_metrics.emit_metrics.assert_called_once()

        # Verify SQS processing was called
        mock_process_sqs.assert_called_once()

    @patch("enhanced_compaction_handler.get_operation_logger")
    @patch("enhanced_compaction_handler.MetricsAccumulator")
    def test_lambda_handler_invalid_event(
        self,
        mock_metrics_class,
        mock_get_logger,
        mock_env,
    ):
        """Test Lambda handler with invalid event."""
        from enhanced_compaction_handler import lambda_handler

        # Setup mocks
        mock_logger = MagicMock()
        mock_get_logger.return_value = mock_logger

        mock_metrics = MagicMock()
        mock_metrics_class.return_value = mock_metrics

        # Mock context
        mock_context = MagicMock()

        # Call handler with invalid event (no Records)
        lambda_handler({}, mock_context)

        # Verify error handling
        mock_logger.error.assert_called()

        # Verify metrics were still emitted
        mock_metrics.emit_metrics.assert_called_once()
