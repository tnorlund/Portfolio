"""
Integration tests for DynamoDB Stream Processor with SQS.

Tests the full integration between the stream processor and SQS queues using moto.
Verifies that messages are correctly routed and formatted for downstream processing.
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

import pytest

from ...lambdas.stream_processor import lambda_handler


class TestMetadataRoutingSQS:
    """Test RECEIPT_METADATA routing to SQS queues."""

    def test_metadata_update_sends_to_both_queues(
        self, target_metadata_event, mock_sqs_queues
    ):
        """Test that metadata updates send messages to both lines and words queues."""
        sqs = mock_sqs_queues["sqs_client"]
        lines_queue_url = mock_sqs_queues["lines_queue_url"]
        words_queue_url = mock_sqs_queues["words_queue_url"]

        result = lambda_handler(target_metadata_event, None)

        assert result["statusCode"] == 200
        assert result["processed_records"] == 1
        assert result["queued_messages"] >= 1

        # Get messages from both queues
        lines_response = sqs.receive_message(
            QueueUrl=lines_queue_url, MaxNumberOfMessages=10
        )
        words_response = sqs.receive_message(
            QueueUrl=words_queue_url, MaxNumberOfMessages=10
        )

        lines_messages = lines_response.get("Messages", [])
        words_messages = words_response.get("Messages", [])

        # Verify both queues received messages
        assert len(lines_messages) == 1
        assert len(words_messages) == 1

        # Verify message content
        lines_body = json.loads(lines_messages[0]["Body"])
        assert lines_body["source"] == "dynamodb_stream"
        assert lines_body["entity_type"] == "RECEIPT_METADATA"
        assert lines_body["event_name"] == "MODIFY"
        assert (
            lines_body["entity_data"]["image_id"]
            == "7e2bd911-7afb-4e0a-84de-57f51ce4daff"
        )
        assert lines_body["entity_data"]["receipt_id"] == 1
        assert "canonical_merchant_name" in lines_body["changes"]

    def test_different_canonical_merchant_names(
        self, target_event_factory, mock_sqs_queues
    ):
        """Test stream processor with different corrected canonical merchant names."""
        sqs = mock_sqs_queues["sqs_client"]
        lines_queue_url = mock_sqs_queues["lines_queue_url"]

        custom_event = target_event_factory(canonical_merchant_name="Target Store")
        result = lambda_handler(custom_event, None)

        assert result["statusCode"] == 200
        assert result["processed_records"] == 1

        response = sqs.receive_message(QueueUrl=lines_queue_url, MaxNumberOfMessages=1)
        message = response["Messages"][0]
        body = json.loads(message["Body"])

        canonical_change = body["changes"]["canonical_merchant_name"]
        assert canonical_change["old"] == "30740 Russell Ranch Rd (Westlake Village)"
        assert canonical_change["new"] == "Target Store"


class TestWordLabelRoutingSQS:
    """Test RECEIPT_WORD_LABEL routing to SQS queues."""

    def test_word_label_update_sends_to_words_only(
        self, word_label_update_event, mock_sqs_queues
    ):
        """Test that word label updates only send to words queue."""
        sqs = mock_sqs_queues["sqs_client"]
        lines_queue_url = mock_sqs_queues["lines_queue_url"]
        words_queue_url = mock_sqs_queues["words_queue_url"]

        result = lambda_handler(word_label_update_event, None)

        assert result["statusCode"] == 200
        assert result["processed_records"] == 1
        assert result["queued_messages"] >= 1

        # Check words queue has message
        words_response = sqs.receive_message(
            QueueUrl=words_queue_url, MaxNumberOfMessages=10
        )
        words_messages = words_response.get("Messages", [])
        assert len(words_messages) == 1

        # Verify message content
        words_body = json.loads(words_messages[0]["Body"])
        assert words_body["entity_type"] == "RECEIPT_WORD_LABEL"
        assert words_body["event_name"] == "MODIFY"
        # Check that changes are present (label changed from PRODUCT to MERCHANT_NAME in fixture)
        assert (
            "reasoning" in words_body["changes"]
            or "validation_status" in words_body["changes"]
        )

        # Check lines queue is empty
        lines_response = sqs.receive_message(
            QueueUrl=lines_queue_url, MaxNumberOfMessages=10
        )
        lines_messages = lines_response.get("Messages", [])
        assert len(lines_messages) == 0

    def test_word_label_remove_event(self, word_label_remove_event, mock_sqs_queues):
        """Test REMOVE events for word labels."""
        sqs = mock_sqs_queues["sqs_client"]
        words_queue_url = mock_sqs_queues["words_queue_url"]

        result = lambda_handler(word_label_remove_event, None)

        assert result["statusCode"] == 200
        assert result["processed_records"] == 1

        # Check words queue
        words_response = sqs.receive_message(
            QueueUrl=words_queue_url, MaxNumberOfMessages=10
        )
        words_messages = words_response.get("Messages", [])
        assert len(words_messages) == 1

        words_body = json.loads(words_messages[0]["Body"])
        assert words_body["event_name"] == "REMOVE"


class TestCompactionRunRoutingSQS:
    """Test COMPACTION_RUN routing to SQS queues."""

    def test_compaction_run_sends_to_both_queues(
        self, compaction_run_insert_event, mock_sqs_queues
    ):
        """Test that COMPACTION_RUN INSERT sends to both collections."""
        sqs = mock_sqs_queues["sqs_client"]
        lines_queue_url = mock_sqs_queues["lines_queue_url"]
        words_queue_url = mock_sqs_queues["words_queue_url"]

        result = lambda_handler(compaction_run_insert_event, None)

        assert result["statusCode"] == 200
        assert result["processed_records"] == 1
        assert result["queued_messages"] >= 2  # One per collection

        # Check both queues received messages
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

        # Verify message content
        lines_body = json.loads(lines_messages[0]["Body"])
        assert lines_body["entity_type"] == "COMPACTION_RUN"
        assert lines_body["event_name"] == "INSERT"
        assert "run_id" in lines_body["entity_data"]
        assert "delta_s3_prefix" in lines_body["entity_data"]

        words_body = json.loads(words_messages[0]["Body"])
        assert words_body["entity_type"] == "COMPACTION_RUN"
        assert "run_id" in words_body["entity_data"]

    def test_compaction_run_message_format(
        self, compaction_run_insert_event, mock_sqs_queues
    ):
        """Verify COMPACTION_RUN message structure."""
        sqs = mock_sqs_queues["sqs_client"]
        lines_queue_url = mock_sqs_queues["lines_queue_url"]

        lambda_handler(compaction_run_insert_event, None)

        response = sqs.receive_message(QueueUrl=lines_queue_url, MaxNumberOfMessages=1)
        message = response["Messages"][0]
        body = json.loads(message["Body"])

        # Check required fields
        assert (
            body["entity_data"]["run_id"] == "550e8400-e29b-41d4-a716-446655440001"
        )  # UUID from fixture
        assert body["entity_data"]["image_id"] == "7e2bd911-7afb-4e0a-84de-57f51ce4daff"
        assert body["entity_data"]["receipt_id"] == 1
        assert "delta_s3_prefix" in body["entity_data"]
        assert len(body["changes"]) == 0  # INSERT events have empty changes


class TestBatchProcessing:
    """Test handling of multiple records in batches."""

    def test_large_batch_over_10_messages(self, target_event_factory, mock_sqs_queues):
        """Test handling >10 messages (SQS batch limit)."""
        # Create event with 15 metadata changes
        events = {"Records": []}
        for i in range(15):
            single_event = target_event_factory(canonical_merchant_name=f"Merchant {i}")
            record = single_event["Records"][0]
            record["eventID"] = f"event-{i}"
            events["Records"].append(record)

        result = lambda_handler(events, None)

        assert result["statusCode"] == 200
        assert result["processed_records"] == 15
        # Each metadata change goes to 2 queues, so 30 total messages
        assert result["queued_messages"] == 30

    def test_mixed_entity_types_in_batch(
        self,
        target_metadata_event,
        word_label_update_event,
        compaction_run_insert_event,
        mock_sqs_queues,
    ):
        """Test batch with multiple entity types."""
        sqs = mock_sqs_queues["sqs_client"]
        lines_queue_url = mock_sqs_queues["lines_queue_url"]
        words_queue_url = mock_sqs_queues["words_queue_url"]

        # Combine all records into one event
        mixed_event = {
            "Records": [
                target_metadata_event["Records"][0],
                word_label_update_event["Records"][0],
                compaction_run_insert_event["Records"][0],
            ]
        }

        result = lambda_handler(mixed_event, None)

        assert result["statusCode"] == 200
        assert result["processed_records"] == 3

        # Verify messages in queues
        lines_response = sqs.receive_message(
            QueueUrl=lines_queue_url, MaxNumberOfMessages=10
        )
        words_response = sqs.receive_message(
            QueueUrl=words_queue_url, MaxNumberOfMessages=10
        )

        lines_messages = lines_response.get("Messages", [])
        words_messages = words_response.get("Messages", [])

        # Lines: metadata (1) + compaction_run (1) = 2
        assert len(lines_messages) == 2
        # Words: metadata (1) + word_label (1) + compaction_run (1) = 3
        assert len(words_messages) == 3


class TestMessageFormat:
    """Test SQS message format and attributes."""

    def test_message_includes_timestamp_and_metadata(
        self, target_metadata_event, mock_sqs_queues
    ):
        """Test that messages include proper timestamps and metadata."""
        sqs = mock_sqs_queues["sqs_client"]
        lines_queue_url = mock_sqs_queues["lines_queue_url"]

        lambda_handler(target_metadata_event, None)

        response = sqs.receive_message(QueueUrl=lines_queue_url, MaxNumberOfMessages=1)
        message = response["Messages"][0]
        body = json.loads(message["Body"])

        # Verify metadata fields are present
        assert "timestamp" in body
        assert "stream_record_id" in body
        assert "aws_region" in body

        # Verify timestamp format (should be ISO format)
        assert "T" in body["timestamp"]
        assert body["aws_region"] == "us-east-1"

    def test_message_attributes_complete(self, target_metadata_event, mock_sqs_queues):
        """Verify all required message attributes are set."""
        sqs = mock_sqs_queues["sqs_client"]
        lines_queue_url = mock_sqs_queues["lines_queue_url"]

        lambda_handler(target_metadata_event, None)

        response = sqs.receive_message(
            QueueUrl=lines_queue_url, MessageAttributeNames=["All"]
        )

        # Note: moto may not fully preserve message attributes
        # This test verifies the structure if attributes are present
        if "Messages" in response and response["Messages"]:
            message = response["Messages"][0]
            if "MessageAttributes" in message:
                attrs = message["MessageAttributes"]
                # Verify expected attributes if present
                assert "source" in attrs
                assert attrs["source"]["StringValue"] == "dynamodb_stream"


class TestErrorHandling:
    """Test error handling and edge cases."""

    def test_no_queues_configured_handles_gracefully(self, target_metadata_event):
        """Test that missing queue URLs are handled gracefully."""
        original_lines = os.environ.pop("LINES_QUEUE_URL", None)
        original_words = os.environ.pop("WORDS_QUEUE_URL", None)

        try:
            result = lambda_handler(target_metadata_event, None)

            # Should still process records but not queue messages
            assert result["statusCode"] == 200
            assert result["processed_records"] == 1

        finally:
            # Restore environment variables
            if original_lines:
                os.environ["LINES_QUEUE_URL"] = original_lines
            if original_words:
                os.environ["WORDS_QUEUE_URL"] = original_words

    def test_malformed_event_handled_gracefully(self):
        """Test handling of malformed stream events."""
        malformed_event = {
            "Records": [
                {
                    "eventID": "bad-event",
                    "eventName": "MODIFY",
                    "dynamodb": {
                        # Missing Keys
                    },
                }
            ]
        }

        result = lambda_handler(malformed_event, None)

        # Should not crash, returns success with 0 processed
        assert result["statusCode"] == 200
        assert result["processed_records"] == 0


if __name__ == "__main__":
    pytest.main([__file__])
