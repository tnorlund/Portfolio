"""Unit tests for embedding_utils module."""

import unittest
from unittest.mock import Mock, patch

from embedding_utils import _upload_bundled_delta_to_s3


class TestUploadBundledDeltaToS3(unittest.TestCase):
    """Tests for _upload_bundled_delta_to_s3 function."""

    @patch("embedding_utils.upload_delta_tarball")
    @patch("embedding_utils.boto3.client")
    def test_successful_upload_without_sqs(self, mock_boto_client, mock_upload):
        """Test successful upload without SQS notification."""
        # Mock upload_delta_tarball return value
        mock_upload.return_value = {
            "status": "uploaded",
            "delta_key": "lines/delta/abc-123",
            "object_key": "lines/delta/abc-123/delta.tar.gz",
            "tar_size_bytes": 1024,
        }

        result = _upload_bundled_delta_to_s3(
            local_delta_dir="/tmp/delta",
            bucket="test-bucket",
            delta_prefix="lines/delta/abc-123",
            collection_name="lines",
            database_name="lines",
            sqs_queue_url=None,
            batch_id="abc-123",
            vector_count=100,
        )

        # Verify result
        self.assertEqual(result["status"], "uploaded")
        self.assertEqual(result["delta_key"], "lines/delta/abc-123")
        self.assertEqual(result["s3_key"], "lines/delta/abc-123/delta.tar.gz")
        self.assertEqual(result["tar_size_bytes"], 1024)

        # Verify upload_delta_tarball was called correctly
        mock_upload.assert_called_once_with(
            local_delta_dir="/tmp/delta",
            bucket="test-bucket",
            delta_prefix="lines/delta/abc-123",
            metadata={"delta_key": "lines/delta/abc-123"},
            region=None,
            s3_client=None,
        )

        # Verify SQS was not called
        mock_boto_client.assert_not_called()

    @patch("embedding_utils.upload_delta_tarball")
    @patch("embedding_utils.boto3.client")
    def test_successful_upload_with_sqs(self, mock_boto_client, mock_upload):
        """Test successful upload with SQS notification."""
        # Mock upload_delta_tarball return value
        mock_upload.return_value = {
            "status": "uploaded",
            "delta_key": "words/delta/xyz-456",
            "object_key": "words/delta/xyz-456/delta.tar.gz",
            "tar_size_bytes": 2048,
        }

        # Mock SQS client
        mock_sqs = Mock()
        mock_boto_client.return_value = mock_sqs

        result = _upload_bundled_delta_to_s3(
            local_delta_dir="/tmp/delta",
            bucket="test-bucket",
            delta_prefix="words/delta/xyz-456",
            collection_name="words",
            database_name="words",
            sqs_queue_url="https://sqs.us-east-1.amazonaws.com/123/test-queue",
            batch_id="xyz-456",
            vector_count=50,
        )

        # Verify result
        self.assertEqual(result["status"], "uploaded")

        # Verify SQS was called
        mock_boto_client.assert_called_once_with("sqs")
        mock_sqs.send_message.assert_called_once()

        # Verify SQS message content
        call_args = mock_sqs.send_message.call_args
        self.assertEqual(
            call_args[1]["QueueUrl"],
            "https://sqs.us-east-1.amazonaws.com/123/test-queue",
        )
        self.assertIn("words", call_args[1]["MessageGroupId"])

    @patch("embedding_utils.upload_delta_tarball")
    def test_failed_upload(self, mock_upload):
        """Test failed upload returns error status."""
        # Mock upload failure
        mock_upload.return_value = {
            "status": "failed",
            "error": "Upload failed",
            "delta_key": "lines/delta/fail-123",
        }

        result = _upload_bundled_delta_to_s3(
            local_delta_dir="/tmp/delta",
            bucket="test-bucket",
            delta_prefix="lines/delta/fail-123",
            collection_name="lines",
            database_name="lines",
            sqs_queue_url=None,
            batch_id="fail-123",
            vector_count=0,
        )

        # Verify error result
        self.assertEqual(result["status"], "failed")
        self.assertIn("error", result)
        self.assertEqual(result["delta_key"], "lines/delta/fail-123")

    @patch("embedding_utils.upload_delta_tarball")
    @patch("embedding_utils.boto3.client")
    def test_sqs_failure_does_not_fail_upload(self, mock_boto_client, mock_upload):
        """Test that SQS failure doesn't fail the whole operation."""
        # Mock successful upload
        mock_upload.return_value = {
            "status": "uploaded",
            "delta_key": "lines/delta/abc-123",
            "object_key": "lines/delta/abc-123/delta.tar.gz",
            "tar_size_bytes": 1024,
        }

        # Mock SQS failure
        mock_sqs = Mock()
        mock_sqs.send_message.side_effect = Exception("SQS error")
        mock_boto_client.return_value = mock_sqs

        result = _upload_bundled_delta_to_s3(
            local_delta_dir="/tmp/delta",
            bucket="test-bucket",
            delta_prefix="lines/delta/abc-123",
            collection_name="lines",
            database_name="lines",
            sqs_queue_url="https://sqs.us-east-1.amazonaws.com/123/test-queue",
            batch_id="abc-123",
            vector_count=100,
        )

        # Upload should still succeed despite SQS failure
        self.assertEqual(result["status"], "uploaded")


if __name__ == "__main__":
    unittest.main()
