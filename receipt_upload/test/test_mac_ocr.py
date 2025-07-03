"""Comprehensive unit tests for mac_ocr.py module."""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import MagicMock, Mock, call, patch

import pytest
from receipt_dynamo.constants import OCRStatus
from receipt_dynamo.entities import OCRJob, OCRRoutingDecision


class TestMacOCR:
    """Test cases for mac_ocr.py main function."""

    @pytest.fixture
    def mock_pulumi_outputs(self):
        """Mock Pulumi configuration outputs."""
        return {
            "ocr_job_queue_url": "https://sqs.us-east-1.amazonaws.com/123/ocr-job-queue",
            "dynamodb_table_name": "test-dynamo-table",
            "ocr_results_queue_url": "https://sqs.us-east-1.amazonaws.com/123/ocr-results-queue",
        }

    @pytest.fixture
    def mock_sqs_message(self):
        """Create a mock SQS message."""
        return {
            "MessageId": "msg-123",
            "ReceiptHandle": "receipt-123",
            "Body": json.dumps(
                {
                    "job_id": "550e8400-e29b-41d4-a716-446655440001",
                    "image_id": "550e8400-e29b-41d4-a716-446655440002",
                }
            ),
        }

    @pytest.fixture
    def mock_ocr_job(self):
        """Create a mock OCR job."""
        job = Mock(spec=OCRJob)
        job.job_id = "550e8400-e29b-41d4-a716-446655440001"
        job.image_id = "550e8400-e29b-41d4-a716-446655440002"
        job.s3_bucket = "test-bucket"
        job.s3_key = "images/test-image.jpg"
        job.status = OCRStatus.PENDING.value
        job.created_at = datetime.now(timezone.utc)
        job.updated_at = datetime.now(timezone.utc)
        return job

    @pytest.mark.unit
    @patch("receipt_upload.mac_ocr.boto3")
    @patch("receipt_upload.mac_ocr.load_env")
    @patch("receipt_upload.mac_ocr.DynamoClient")
    @patch("receipt_upload.mac_ocr.download_image_from_s3")
    @patch("receipt_upload.mac_ocr.upload_file_to_s3")
    @patch("receipt_upload.mac_ocr.apple_vision_ocr_job")
    @patch("sys.argv", ["mac_ocr.py", "--env", "dev"])
    def test_main_successful_processing(
        self,
        mock_apple_vision,
        mock_upload_s3,
        mock_download_s3,
        mock_dynamo_client_class,
        mock_load_env,
        mock_boto3,
        mock_pulumi_outputs,
        mock_sqs_message,
        mock_ocr_job,
    ):
        """Test successful OCR processing flow."""
        # Set up mocks
        mock_load_env.return_value = mock_pulumi_outputs

        # Mock SQS client
        mock_sqs_client = Mock()
        mock_boto3.client.return_value = mock_sqs_client
        mock_sqs_client.receive_message.return_value = {
            "Messages": [mock_sqs_message]
        }

        # Mock DynamoDB client
        mock_dynamo_client = Mock()
        mock_dynamo_client_class.return_value = mock_dynamo_client
        mock_dynamo_client.getOCRJob.return_value = mock_ocr_job

        # Mock file operations
        # Use generic paths for mocking - actual code uses TemporaryDirectory
        mock_download_s3.return_value = Path("test-image.jpg")  # nosec B108
        mock_ocr_result_path = Path("ocr-result.json")  # nosec B108
        mock_apple_vision.return_value = [mock_ocr_result_path]

        # Import and run main
        with patch("sys.argv", ["mac_ocr.py", "--env", "dev"]):
            from receipt_upload.mac_ocr import main

            main()

        # Verify flow
        mock_load_env.assert_called_once_with("dev")
        mock_sqs_client.receive_message.assert_called_once()
        # getOCRJob is called twice - once at start and once before update
        assert mock_dynamo_client.getOCRJob.call_count == 2
        mock_dynamo_client.getOCRJob.assert_any_call(
            image_id="550e8400-e29b-41d4-a716-446655440002",
            job_id="550e8400-e29b-41d4-a716-446655440001",
        )
        mock_download_s3.assert_called_once_with(
            "test-bucket",
            "images/test-image.jpg",
            "550e8400-e29b-41d4-a716-446655440002",
        )
        mock_apple_vision.assert_called_once()
        mock_upload_s3.assert_called_once()

        # Verify OCR routing decision was added
        mock_dynamo_client.addOCRRoutingDecision.assert_called_once()
        routing_decision = mock_dynamo_client.addOCRRoutingDecision.call_args[
            0
        ][0]
        assert (
            routing_decision.image_id == "550e8400-e29b-41d4-a716-446655440002"
        )
        assert (
            routing_decision.job_id == "550e8400-e29b-41d4-a716-446655440001"
        )
        assert routing_decision.status == OCRStatus.PENDING.value

        # Verify result was sent to queue
        mock_sqs_client.send_message.assert_called_once()

        # Verify OCR job was updated
        mock_dynamo_client.updateOCRJob.assert_called_once()
        assert mock_ocr_job.status == OCRStatus.COMPLETED.value

        # Verify message was deleted
        mock_sqs_client.delete_message_batch.assert_called_once()

    @pytest.mark.unit
    @patch("receipt_upload.mac_ocr.boto3")
    @patch("receipt_upload.mac_ocr.load_env")
    @patch("receipt_upload.mac_ocr.DynamoClient")
    @patch("sys.argv", ["mac_ocr.py"])
    def test_main_no_messages(
        self,
        mock_dynamo_client_class,
        mock_load_env,
        mock_boto3,
        mock_pulumi_outputs,
    ):
        """Test when queue has no messages."""
        mock_load_env.return_value = mock_pulumi_outputs

        mock_sqs_client = Mock()
        mock_boto3.client.return_value = mock_sqs_client
        mock_sqs_client.receive_message.return_value = {}  # No Messages key

        from receipt_upload.mac_ocr import main

        # Should return early without error
        main()

        mock_sqs_client.receive_message.assert_called_once()
        # No other operations should occur
        mock_sqs_client.send_message.assert_not_called()
        mock_sqs_client.delete_message_batch.assert_not_called()

    @pytest.mark.unit
    @patch("receipt_upload.mac_ocr.load_env")
    @patch("sys.argv", ["mac_ocr.py", "--env", "prod"])
    def test_main_empty_pulumi_outputs(self, mock_load_env):
        """Test error when Pulumi outputs are empty."""
        mock_load_env.return_value = {}

        from receipt_upload.mac_ocr import main

        with pytest.raises(ValueError, match="Pulumi outputs are empty"):
            main()

    @pytest.mark.unit
    @patch("receipt_upload.mac_ocr.boto3")
    @patch("receipt_upload.mac_ocr.load_env")
    @patch("receipt_upload.mac_ocr.DynamoClient")
    @patch("receipt_upload.mac_ocr.download_image_from_s3")
    @patch("receipt_upload.mac_ocr.upload_file_to_s3")
    @patch("receipt_upload.mac_ocr.apple_vision_ocr_job")
    @patch("sys.argv", ["mac_ocr.py"])
    def test_main_multiple_messages(
        self,
        mock_apple_vision,
        mock_upload_s3,
        mock_download_s3,
        mock_dynamo_client_class,
        mock_load_env,
        mock_boto3,
        mock_pulumi_outputs,
    ):
        """Test processing multiple messages in batch."""
        mock_load_env.return_value = mock_pulumi_outputs

        # Create multiple messages
        messages = []
        for i in range(3):
            messages.append(
                {
                    "MessageId": f"msg-{i}",
                    "ReceiptHandle": f"receipt-{i}",
                    "Body": json.dumps(
                        {
                            "job_id": f"550e8400-e29b-41d4-a716-44665544000{i}",
                            "image_id": f"650e8400-e29b-41d4-a716-44665544000{i}",
                        }
                    ),
                }
            )

        mock_sqs_client = Mock()
        mock_boto3.client.return_value = mock_sqs_client
        mock_sqs_client.receive_message.return_value = {"Messages": messages}

        # Mock DynamoDB responses
        mock_dynamo_client = Mock()
        mock_dynamo_client_class.return_value = mock_dynamo_client

        ocr_jobs = []
        for i in range(3):
            job = Mock(spec=OCRJob)
            job.job_id = f"550e8400-e29b-41d4-a716-44665544000{i}"
            job.image_id = f"650e8400-e29b-41d4-a716-44665544000{i}"
            job.s3_bucket = "test-bucket"
            job.s3_key = f"images/test-image-{i}.jpg"
            job.status = OCRStatus.PENDING.value
            job.updated_at = datetime.now(timezone.utc)
            ocr_jobs.append(job)

        # getOCRJob is called twice per message (once at start, once before update)
        mock_dynamo_client.getOCRJob.side_effect = ocr_jobs + ocr_jobs

        # Mock file operations
        # Use generic paths for mocking - actual code uses TemporaryDirectory
        image_paths = [
            Path(f"test-image-{i}.jpg") for i in range(3)
        ]  # nosec B108
        mock_download_s3.side_effect = image_paths

        # Use generic paths for mocking - actual code uses TemporaryDirectory
        ocr_results = [
            Path(f"ocr-result-{i}.json") for i in range(3)
        ]  # nosec B108
        mock_apple_vision.return_value = ocr_results

        from receipt_upload.mac_ocr import main

        main()

        # Verify batch processing
        assert mock_download_s3.call_count == 3
        assert mock_upload_s3.call_count == 3
        assert mock_dynamo_client.addOCRRoutingDecision.call_count == 3
        assert mock_sqs_client.send_message.call_count == 3
        assert mock_dynamo_client.updateOCRJob.call_count == 3

        # Verify batch delete
        mock_sqs_client.delete_message_batch.assert_called_once()
        delete_entries = mock_sqs_client.delete_message_batch.call_args[1][
            "Entries"
        ]
        assert len(delete_entries) == 3

    @pytest.mark.unit
    @patch("receipt_upload.mac_ocr.boto3")
    @patch("receipt_upload.mac_ocr.load_env")
    @patch("receipt_upload.mac_ocr.DynamoClient")
    @patch("receipt_upload.mac_ocr.download_image_from_s3")
    @patch("sys.argv", ["mac_ocr.py"])
    def test_main_ocr_failure(
        self,
        mock_download_s3,
        mock_dynamo_client_class,
        mock_load_env,
        mock_boto3,
        mock_pulumi_outputs,
        mock_sqs_message,
        mock_ocr_job,
    ):
        """Test handling of OCR processing failure."""
        mock_load_env.return_value = mock_pulumi_outputs

        mock_sqs_client = Mock()
        mock_boto3.client.return_value = mock_sqs_client
        mock_sqs_client.receive_message.return_value = {
            "Messages": [mock_sqs_message]
        }

        mock_dynamo_client = Mock()
        mock_dynamo_client_class.return_value = mock_dynamo_client
        mock_dynamo_client.getOCRJob.return_value = mock_ocr_job

        # Simulate download failure
        mock_download_s3.side_effect = Exception("S3 download failed")

        from receipt_upload.mac_ocr import main

        with pytest.raises(Exception, match="S3 download failed"):
            main()

        # Message should not be deleted on failure
        mock_sqs_client.delete_message_batch.assert_not_called()

    @pytest.mark.unit
    def test_argparse_configuration(self):
        """Test argument parser configuration."""
        from receipt_upload.mac_ocr import main

        # Test default environment
        with patch("sys.argv", ["mac_ocr.py"]):
            with patch("receipt_upload.mac_ocr.load_env") as mock_load:
                mock_load.return_value = {}
                try:
                    main()
                except ValueError:
                    pass  # Expected due to empty outputs
                mock_load.assert_called_with("dev")  # Default

        # Test custom environment
        with patch("sys.argv", ["mac_ocr.py", "--env", "staging"]):
            with patch("receipt_upload.mac_ocr.load_env") as mock_load:
                mock_load.return_value = {}
                try:
                    main()
                except ValueError:
                    pass
                mock_load.assert_called_with("staging")

    @pytest.mark.unit
    @patch("receipt_upload.mac_ocr.TemporaryDirectory")
    @patch("receipt_upload.mac_ocr.boto3")
    @patch("receipt_upload.mac_ocr.load_env")
    @patch("receipt_upload.mac_ocr.DynamoClient")
    @patch("sys.argv", ["mac_ocr.py"])
    def test_temporary_directory_cleanup(
        self,
        mock_dynamo_client_class,
        mock_load_env,
        mock_boto3,
        mock_temp_dir,
        mock_pulumi_outputs,
    ):
        """Test that temporary directory is properly cleaned up."""
        mock_load_env.return_value = mock_pulumi_outputs

        # Mock no messages to simplify test
        mock_sqs_client = Mock()
        mock_boto3.client.return_value = mock_sqs_client
        mock_sqs_client.receive_message.return_value = {}

        # Mock temporary directory context manager
        mock_temp_context = Mock()
        # Use generic path for mocking - actual code uses TemporaryDirectory
        mock_temp_context.__enter__ = Mock(
            return_value="test-dir"
        )  # nosec B108
        mock_temp_context.__exit__ = Mock(return_value=None)
        mock_temp_dir.return_value = mock_temp_context

        from receipt_upload.mac_ocr import main

        main()

        # With no messages, TemporaryDirectory should not be created
        mock_temp_dir.assert_not_called()
