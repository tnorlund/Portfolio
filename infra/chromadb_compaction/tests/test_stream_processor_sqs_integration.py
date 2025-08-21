"""
SQS Integration Tests for DynamoDB Stream Processor Lambda

Tests the full integration between the stream processor and SQS queues using moto
to mock AWS services. These tests verify that:
1. Stream processor correctly sends messages to SQS queues
2. Messages contain the expected content and metadata
3. Different entity types target appropriate collections
"""

# pylint: disable=wrong-import-position,duplicate-code
# wrong-import-position: Required to mock modules before importing lambda_handler
# duplicate-code: Test files need identical mocking setup

import json
import os
import sys
from unittest.mock import MagicMock

# Mock problematic imports before any other imports
sys.modules["lambda_layer"] = MagicMock()
sys.modules["lambda_layer"].dynamo_layer = MagicMock()
sys.modules["pulumi_docker_build"] = MagicMock()
sys.modules["pulumi"] = MagicMock()
sys.modules["pulumi_aws"] = MagicMock()
sys.modules["pulumi_aws.ecr"] = MagicMock()

# Import the stream processor after mocking
from ..lambdas.stream_processor import lambda_handler


class TestStreamProcessorSQSIntegration:
    """Integration tests for stream processor with real SQS integration."""

    def test_target_metadata_update_sends_to_both_queues(
        self, target_metadata_event, mock_sqs_queues
    ):
        """Test that Target metadata update sends messages to both lines and words queues."""
        # Get SQS client and queue URLs from fixture
        sqs = mock_sqs_queues["sqs_client"]
        lines_queue_url = mock_sqs_queues["lines_queue_url"]
        words_queue_url = mock_sqs_queues["words_queue_url"]

        # Call the lambda handler with the Target metadata event
        result = lambda_handler(target_metadata_event, None)

        # Assert successful processing
        assert result["statusCode"] == 200
        assert result["processed_records"] == 1
        assert result["queued_messages"] == 1

        # Get messages from both queues
        lines_messages, words_messages = self._get_messages_from_queues(
            sqs, lines_queue_url, words_queue_url
        )

        # Verify both queues received exactly one message
        assert len(lines_messages) == 1
        assert len(words_messages) == 1

        # Verify message content
        self._verify_target_message_content(
            lines_messages[0], words_messages[0]
        )

    def _get_messages_from_queues(self, sqs, lines_queue_url, words_queue_url):
        """Helper to get messages from both queues."""
        lines_response = sqs.receive_message(
            QueueUrl=lines_queue_url, MaxNumberOfMessages=10
        )
        words_response = sqs.receive_message(
            QueueUrl=words_queue_url, MaxNumberOfMessages=10
        )
        return (
            lines_response.get("Messages", []),
            words_response.get("Messages", []),
        )

    def _verify_target_message_content(self, lines_message, words_message):
        """Helper to verify the content of Target metadata messages."""
        lines_body = json.loads(lines_message["Body"])

        # Verify core message content
        assert lines_body["source"] == "dynamodb_stream"
        assert lines_body["entity_type"] == "RECEIPT_METADATA"
        assert lines_body["event_name"] == "MODIFY"
        expected_image_id = "7e2bd911-7afb-4e0a-84de-57f51ce4daff"
        assert lines_body["entity_data"]["image_id"] == expected_image_id
        assert lines_body["entity_data"]["receipt_id"] == 1

        # Check the field change that triggered this message
        assert "canonical_merchant_name" in lines_body["changes"]
        canonical_change = lines_body["changes"]["canonical_merchant_name"]
        expected_old = "30740 Russell Ranch Rd (Westlake Village)"
        assert canonical_change["old"] == expected_old
        assert canonical_change["new"] == "Target"

        # Verify message attributes
        lines_attrs = lines_message["MessageAttributes"]
        assert lines_attrs["source"]["StringValue"] == "dynamodb_stream"
        assert lines_attrs["entity_type"]["StringValue"] == "RECEIPT_METADATA"
        assert lines_attrs["event_name"]["StringValue"] == "MODIFY"
        assert lines_attrs["collection"]["StringValue"] == "lines"

        # Verify words queue message content matches lines (except collection)
        words_body = json.loads(words_message["Body"])
        assert words_body["source"] == lines_body["source"]
        assert words_body["entity_type"] == lines_body["entity_type"]
        assert words_body["entity_data"] == lines_body["entity_data"]
        assert words_body["changes"] == lines_body["changes"]

        words_attrs = words_message["MessageAttributes"]
        assert words_attrs["collection"]["StringValue"] == "words"

    def test_different_canonical_merchant_names(
        self, target_event_factory, mock_sqs_queues
    ):
        """Test stream processor with different corrected canonical merchant names."""
        sqs = mock_sqs_queues["sqs_client"]
        lines_queue_url = mock_sqs_queues["lines_queue_url"]

        # Test with custom merchant name
        custom_event = target_event_factory(
            canonical_merchant_name="Target Store"
        )
        result = lambda_handler(custom_event, None)

        assert result["statusCode"] == 200
        assert result["processed_records"] == 1

        # Check the message content
        response = sqs.receive_message(
            QueueUrl=lines_queue_url, MaxNumberOfMessages=1
        )
        message = response["Messages"][0]
        body = json.loads(message["Body"])

        # Verify the custom merchant name appears in the change
        canonical_change = body["changes"]["canonical_merchant_name"]
        assert (
            canonical_change["old"]
            == "30740 Russell Ranch Rd (Westlake Village)"
        )
        assert canonical_change["new"] == "Target Store"

    def test_no_queues_configured_handles_gracefully(
        self, target_metadata_event
    ):
        """Test that missing queue URLs are handled gracefully."""
        # Temporarily remove queue environment variables
        original_lines = os.environ.pop("LINES_QUEUE_URL", None)
        original_words = os.environ.pop("WORDS_QUEUE_URL", None)

        try:
            # Should handle missing queues gracefully
            result = lambda_handler(target_metadata_event, None)

            # Should still process records but not queue any messages
            assert result["statusCode"] == 200
            assert result["processed_records"] == 1
            assert (
                result["queued_messages"] == 1
            )  # Messages created but not sent

        finally:
            # Restore environment variables
            if original_lines:
                os.environ["LINES_QUEUE_URL"] = original_lines
            if original_words:
                os.environ["WORDS_QUEUE_URL"] = original_words

    def test_message_includes_timestamp_and_metadata(
        self, target_metadata_event, mock_sqs_queues
    ):
        """Test that messages include proper timestamps and metadata."""
        sqs = mock_sqs_queues["sqs_client"]
        lines_queue_url = mock_sqs_queues["lines_queue_url"]

        result = lambda_handler(target_metadata_event, None)
        assert result["statusCode"] == 200

        # Get the message
        response = sqs.receive_message(
            QueueUrl=lines_queue_url, MaxNumberOfMessages=1
        )
        message = response["Messages"][0]
        body = json.loads(message["Body"])

        # Verify metadata fields are present
        assert "timestamp" in body
        assert "stream_record_id" in body
        assert "aws_region" in body

        # Verify timestamp format (should be ISO format)
        assert "T" in body["timestamp"]
        assert body["aws_region"] == "us-east-1"

    def test_receipt_metadata_affects_both_collections(
        self, target_metadata_event, mock_sqs_queues
    ):
        """Test that RECEIPT_METADATA changes affect both lines and words collections."""
        sqs = mock_sqs_queues["sqs_client"]
        lines_queue_url = mock_sqs_queues["lines_queue_url"]
        words_queue_url = mock_sqs_queues["words_queue_url"]

        result = lambda_handler(target_metadata_event, None)
        assert result["statusCode"] == 200

        # Both queues should have exactly one message
        lines_response = sqs.receive_message(
            QueueUrl=lines_queue_url, MaxNumberOfMessages=10
        )
        words_response = sqs.receive_message(
            QueueUrl=words_queue_url, MaxNumberOfMessages=10
        )

        lines_messages = lines_response.get("Messages", [])
        words_messages = words_response.get("Messages", [])

        assert len(lines_messages) == 1
        assert len(words_messages) == 1

        # Verify collection-specific attributes
        lines_attrs = lines_messages[0]["MessageAttributes"]
        words_attrs = words_messages[0]["MessageAttributes"]

        assert lines_attrs["collection"]["StringValue"] == "lines"
        assert words_attrs["collection"]["StringValue"] == "words"
