"""Integration tests for enhanced_compaction_handler with moto."""

import os
import json
import pytest
import tempfile
import shutil
from uuid import uuid4
from datetime import datetime
from unittest.mock import MagicMock, patch

# Skip if moto not available - imports moved to functions
pytest.importorskip("moto")


@pytest.fixture
def aws_credentials():
    """Set up fake AWS credentials for moto."""
    os.environ["AWS_ACCESS_KEY_ID"] = "testing"
    os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"
    os.environ["AWS_SECURITY_TOKEN"] = "testing"
    os.environ["AWS_SESSION_TOKEN"] = "testing"
    os.environ["AWS_DEFAULT_REGION"] = "us-east-1"


@pytest.fixture
def mock_env(aws_credentials):
    """Set up environment variables for testing."""
    env_vars = {
        "CHROMADB_BUCKET": "test-compaction-bucket",
        "DYNAMODB_TABLE_NAME": "test-receipt-table",
        "CHROMADB_STORAGE_MODE": "s3",
        "LOG_LEVEL": "INFO",
    }
    with patch.dict(os.environ, env_vars, clear=False):
        yield env_vars


@pytest.fixture
def s3_client(aws_credentials):
    """Create a mocked S3 client."""
    from moto import mock_aws
    import boto3

    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")
        # Create bucket
        s3.create_bucket(Bucket="test-compaction-bucket")
        yield s3


@pytest.fixture
def dynamodb_client(aws_credentials):
    """Create a mocked DynamoDB client."""
    from moto import mock_aws
    import boto3

    with mock_aws():
        dynamodb = boto3.client("dynamodb", region_name="us-east-1")

        # Create table
        dynamodb.create_table(
            TableName="test-receipt-table",
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )

        yield dynamodb


@pytest.fixture
def temp_chroma_dir():
    """Create a temporary ChromaDB directory."""
    temp_dir = tempfile.mkdtemp(prefix="chroma-test-")
    yield temp_dir
    shutil.rmtree(temp_dir, ignore_errors=True)


@pytest.fixture
def setup_s3_snapshot(s3_client, temp_chroma_dir):
    """Set up a test ChromaDB snapshot in S3."""
    # Create a minimal ChromaDB snapshot
    from receipt_chroma import ChromaClient

    client = ChromaClient(persist_directory=temp_chroma_dir, mode="write")

    # Add test data
    test_id = str(uuid4())
    client.upsert(
        collection_name="lines",
        ids=[f"IMAGE#{test_id}#RECEIPT#00001#LINE#00001"],
        embeddings=[[0.1] * 1536],
        metadatas=[{"text": "Test Line", "merchant_name": "Old Merchant"}],
    )

    client.close()

    # Upload to S3
    import tarfile
    tar_path = f"{temp_chroma_dir}.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        tar.add(temp_chroma_dir, arcname="chroma_snapshot")

    s3_client.upload_file(
        tar_path,
        "test-compaction-bucket",
        "chromadb/lines/snapshot.tar.gz",
    )

    # Upload version file
    s3_client.put_object(
        Bucket="test-compaction-bucket",
        Key="chromadb/lines/version.txt",
        Body="v1",
    )

    os.remove(tar_path)

    return test_id


class TestEnhancedCompactionIntegration:
    """Integration tests for the enhanced compaction handler."""

    @patch("enhanced_compaction_handler.process_collection_updates")
    @patch("enhanced_compaction_handler.ChromaClient")
    def test_full_workflow_with_s3(
        self,
        mock_chroma_client,
        mock_process_updates,
        mock_env,
        s3_client,
        dynamodb_client,
        setup_s3_snapshot,
    ):
        """Test full workflow with S3 backend."""
        from enhanced_compaction_handler import process_collection
        from receipt_dynamo.constants import ChromaDBCollection
        from receipt_dynamo_stream.models import StreamMessage

        # Create test message
        test_image_id = setup_s3_snapshot
        msg = StreamMessage(
            entity_type="RECEIPT_METADATA",
            entity_data={"image_id": test_image_id, "receipt_id": 1},
            changes={"merchant_name": {"old": "Old Merchant", "new": "New Merchant"}},
            event_name="MODIFY",
            collections=(ChromaDBCollection.LINES,),
            timestamp=datetime.now().isoformat(),
            stream_record_id="test-record",
            aws_region="us-east-1",
        )

        # Mock ChromaClient
        mock_client = MagicMock()
        mock_chroma_client.return_value = mock_client

        # Mock process_collection_updates result
        mock_result = MagicMock()
        mock_result.total_metadata_updated = 1
        mock_result.total_labels_updated = 0
        mock_result.delta_merge_count = 0
        mock_result.has_errors = False
        mock_result.metadata_updates = []
        mock_result.label_updates = []
        mock_process_updates.return_value = mock_result

        # Mock logger and metrics
        mock_logger = MagicMock()
        mock_logger.operation_timer = MagicMock()
        mock_logger.operation_timer.return_value.__enter__ = MagicMock()
        mock_logger.operation_timer.return_value.__exit__ = MagicMock()

        mock_metrics = MagicMock()

        # Call process_collection
        result = process_collection(
            collection=ChromaDBCollection.LINES,
            messages=[msg],
            logger=mock_logger,
            metrics=mock_metrics,
        )

        # Verify success
        assert result["status"] == "success"
        assert result["failed_message_ids"] == []

        # Verify ChromaClient was used
        mock_chroma_client.assert_called_once()
        mock_client.close.assert_called_once()

        # Verify process_collection_updates was called
        mock_process_updates.assert_called_once()

    def test_sqs_message_processing(
        self,
        mock_env,
        s3_client,
        dynamodb_client,
    ):
        """Test SQS message processing with mocked AWS services."""
        from enhanced_compaction_handler import process_sqs_messages
        from receipt_dynamo_stream.models import StreamMessage
        from receipt_dynamo.constants import ChromaDBCollection

        # Create test SQS records
        test_image_id = str(uuid4())
        message_body = {
            "entity_type": "RECEIPT_METADATA",
            "entity_data": {"image_id": test_image_id, "receipt_id": 1},
            "changes": {},
            "event_name": "MODIFY",
            "collections": ["lines"],
            "timestamp": datetime.now().isoformat(),
            "stream_record_id": "test-record",
            "aws_region": "us-east-1",
        }

        records = [
            {
                "messageId": "msg-1",
                "body": json.dumps(message_body),
            }
        ]

        # Mock logger and metrics
        mock_logger = MagicMock()
        mock_metrics = MagicMock()

        # Mock process_collection to avoid actual processing
        with patch("enhanced_compaction_handler.process_collection") as mock_process:
            mock_process.return_value = {
                "status": "success",
                "failed_message_ids": [],
            }

            # Call process_sqs_messages
            result = process_sqs_messages(
                records=records,
                logger=mock_logger,
                metrics=mock_metrics,
            )

            # Verify result
            assert result["batchItemFailures"] == []

            # Verify process_collection was called
            mock_process.assert_called_once()

    @patch("enhanced_compaction_handler.download_chroma_snapshot")
    @patch("enhanced_compaction_handler.upload_chroma_snapshot")
    @patch("enhanced_compaction_handler.process_collection_updates")
    @patch("enhanced_compaction_handler.ChromaClient")
    def test_lambda_handler_end_to_end(
        self,
        mock_chroma_client,
        mock_process_updates,
        mock_upload,
        mock_download,
        mock_env,
        s3_client,
        dynamodb_client,
    ):
        """Test full Lambda handler end-to-end."""
        from enhanced_compaction_handler import lambda_handler

        # Create test SQS event
        test_image_id = str(uuid4())
        message_body = {
            "entity_type": "RECEIPT_METADATA",
            "entity_data": {"image_id": test_image_id, "receipt_id": 1},
            "changes": {"merchant_name": {"old": "Old", "new": "New"}},
            "event_name": "MODIFY",
            "collections": ["lines"],
            "timestamp": datetime.now().isoformat(),
            "stream_record_id": "test-record",
            "aws_region": "us-east-1",
        }

        event = {
            "Records": [
                {
                    "messageId": "msg-1",
                    "body": json.dumps(message_body),
                }
            ]
        }

        # Mock context
        context = MagicMock()
        context.function_name = "test-function"
        context.invoked_function_arn = "arn:aws:lambda:us-east-1:123456789012:function:test-function"
        context.aws_request_id = "test-request-id"
        context.get_remaining_time_in_millis.return_value = 900000  # 15 minutes

        # Mock download/upload
        mock_download.return_value = {"status": "success", "version": "v1"}
        mock_upload.return_value = {"status": "success", "version": "v2"}

        # Mock ChromaClient
        mock_client = MagicMock()
        mock_chroma_client.return_value = mock_client

        # Mock process_collection_updates result
        mock_result = MagicMock()
        mock_result.total_metadata_updated = 1
        mock_result.total_labels_updated = 0
        mock_result.delta_merge_count = 0
        mock_result.has_errors = False
        mock_result.metadata_updates = []
        mock_result.label_updates = []
        mock_process_updates.return_value = mock_result

        # Call Lambda handler
        result = lambda_handler(event, context)

        # Verify result
        assert "batchItemFailures" in result
        assert result["batchItemFailures"] == []

        # Verify download was called
        mock_download.assert_called_once()

        # Verify ChromaClient was used
        mock_chroma_client.assert_called()
        mock_client.close.assert_called()

        # Verify process_collection_updates was called
        mock_process_updates.assert_called_once()

        # Verify upload was called
        mock_upload.assert_called_once()

    @patch("enhanced_compaction_handler.download_chroma_snapshot")
    def test_lambda_handler_download_failure(
        self,
        mock_download,
        mock_env,
        s3_client,
        dynamodb_client,
    ):
        """Test Lambda handler with download failure."""
        from enhanced_compaction_handler import lambda_handler

        # Create test SQS event
        test_image_id = str(uuid4())
        message_body = {
            "entity_type": "RECEIPT_METADATA",
            "entity_data": {"image_id": test_image_id, "receipt_id": 1},
            "changes": {},
            "event_name": "MODIFY",
            "collections": ["lines"],
            "timestamp": datetime.now().isoformat(),
            "stream_record_id": "test-record",
            "aws_region": "us-east-1",
        }

        event = {
            "Records": [
                {
                    "messageId": "msg-1",
                    "body": json.dumps(message_body),
                }
            ]
        }

        # Mock context
        context = MagicMock()
        context.function_name = "test-function"
        context.invoked_function_arn = "arn:aws:lambda:us-east-1:123456789012:function:test-function"
        context.aws_request_id = "test-request-id"
        context.get_remaining_time_in_millis.return_value = 900000

        # Mock download failure
        mock_download.return_value = {"status": "error", "error": "Download failed"}

        # Call Lambda handler
        result = lambda_handler(event, context)

        # Verify failed message is returned
        assert "batchItemFailures" in result
        assert len(result["batchItemFailures"]) == 1
        assert result["batchItemFailures"][0]["itemIdentifier"] == "msg-1"

    def test_multiple_collections(
        self,
        mock_env,
        s3_client,
        dynamodb_client,
    ):
        """Test processing messages for multiple collections."""
        from enhanced_compaction_handler import process_sqs_messages

        # Create test messages for both lines and words
        test_image_id = str(uuid4())

        message_body_lines = {
            "entity_type": "RECEIPT_METADATA",
            "entity_data": {"image_id": test_image_id, "receipt_id": 1},
            "changes": {},
            "event_name": "MODIFY",
            "collections": ["lines"],
            "timestamp": datetime.now().isoformat(),
            "stream_record_id": "test-record-1",
            "aws_region": "us-east-1",
        }

        message_body_words = {
            "entity_type": "WORD_LABEL",
            "entity_data": {"image_id": test_image_id, "word_id": 1},
            "changes": {},
            "event_name": "INSERT",
            "collections": ["words"],
            "timestamp": datetime.now().isoformat(),
            "stream_record_id": "test-record-2",
            "aws_region": "us-east-1",
        }

        records = [
            {"messageId": "msg-1", "body": json.dumps(message_body_lines)},
            {"messageId": "msg-2", "body": json.dumps(message_body_words)},
        ]

        # Mock logger and metrics
        mock_logger = MagicMock()
        mock_metrics = MagicMock()

        # Mock process_collection for both collections
        with patch("enhanced_compaction_handler.process_collection") as mock_process:
            mock_process.return_value = {
                "status": "success",
                "failed_message_ids": [],
            }

            # Call process_sqs_messages
            result = process_sqs_messages(
                records=records,
                logger=mock_logger,
                metrics=mock_metrics,
            )

            # Verify result
            assert result["batchItemFailures"] == []

            # Verify process_collection was called for each collection
            assert mock_process.call_count == 2
