"""
Unit tests for DynamoDB Stream Processor Lambda

Tests the stream processor functionality for ChromaDB metadata synchronization.
"""

import json
import os
import uuid
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

from stream_processor import (
    extract_dynamodb_value,
    get_chromadb_relevant_changes,
    lambda_handler,
    parse_receipt_entity_key,
    send_messages_to_sqs,
)


class TestParseReceiptEntityKey:
    """Test entity key parsing logic."""

    def test_parse_receipt_metadata_valid(self):
        """Test parsing valid RECEIPT_METADATA keys."""
        pk = "IMAGE#550e8400-e29b-41d4-a716-446655440000"
        sk = "RECEIPT#00001#METADATA"

        result = parse_receipt_entity_key(pk, sk)

        assert result is not None
        assert result["entity_type"] == "RECEIPT_METADATA"
        assert result["image_id"] == "550e8400-e29b-41d4-a716-446655440000"
        assert result["receipt_id"] == 1

    def test_parse_receipt_word_label_valid(self):
        """Test parsing valid RECEIPT_WORD_LABEL keys."""
        pk = "IMAGE#550e8400-e29b-41d4-a716-446655440000"
        sk = "RECEIPT#00001#LINE#00002#WORD#00003#LABEL#TOTAL"

        result = parse_receipt_entity_key(pk, sk)

        assert result is not None
        assert result["entity_type"] == "RECEIPT_WORD_LABEL"
        assert result["image_id"] == "550e8400-e29b-41d4-a716-446655440000"
        assert result["receipt_id"] == 1
        assert result["line_id"] == 2
        assert result["word_id"] == 3
        assert result["label"] == "TOTAL"

    def test_parse_non_image_pk(self):
        """Test that non-IMAGE PKs are ignored."""
        pk = "BATCH#12345"
        sk = "RECEIPT#00001#METADATA"

        result = parse_receipt_entity_key(pk, sk)
        assert result is None

    def test_parse_receipt_line_ignored(self):
        """Test that RECEIPT_LINE entities are ignored."""
        pk = "IMAGE#550e8400-e29b-41d4-a716-446655440000"
        sk = "RECEIPT#00001#LINE#00002"

        result = parse_receipt_entity_key(pk, sk)
        assert result is None

    def test_parse_receipt_word_ignored(self):
        """Test that RECEIPT_WORD entities are ignored."""
        pk = "IMAGE#550e8400-e29b-41d4-a716-446655440000"
        sk = "RECEIPT#00001#LINE#00002#WORD#00003"

        result = parse_receipt_entity_key(pk, sk)
        assert result is None

    def test_parse_invalid_metadata_format(self):
        """Test parsing invalid metadata format."""
        pk = "IMAGE#550e8400-e29b-41d4-a716-446655440000"
        sk = "RECEIPT#invalid#METADATA"

        result = parse_receipt_entity_key(pk, sk)
        assert result is None

    def test_parse_invalid_label_format(self):
        """Test parsing invalid label format."""
        pk = "IMAGE#550e8400-e29b-41d4-a716-446655440000"
        sk = "RECEIPT#invalid#LINE#00002#WORD#00003#LABEL#TOTAL"

        result = parse_receipt_entity_key(pk, sk)
        assert result is None


class TestExtractDynamodbValue:
    """Test DynamoDB value extraction."""

    def test_extract_string_value(self):
        """Test extracting string value."""
        dynamo_value = {"S": "test_string"}
        result = extract_dynamodb_value(dynamo_value)
        assert result == "test_string"

    def test_extract_number_value(self):
        """Test extracting number value."""
        dynamo_value = {"N": "123.45"}
        result = extract_dynamodb_value(dynamo_value)
        assert result == 123.45

    def test_extract_boolean_value(self):
        """Test extracting boolean value."""
        dynamo_value = {"BOOL": True}
        result = extract_dynamodb_value(dynamo_value)
        assert result is True

    def test_extract_null_value(self):
        """Test extracting null value."""
        dynamo_value = {"NULL": True}
        result = extract_dynamodb_value(dynamo_value)
        assert result is None

    def test_extract_map_value(self):
        """Test extracting map value."""
        dynamo_value = {
            "M": {"x": {"N": "10"}, "y": {"N": "20"}, "name": {"S": "test"}}
        }
        result = extract_dynamodb_value(dynamo_value)
        expected = {"x": 10.0, "y": 20.0, "name": "test"}
        assert result == expected

    def test_extract_list_value(self):
        """Test extracting list value."""
        dynamo_value = {"L": [{"S": "item1"}, {"S": "item2"}, {"N": "123"}]}
        result = extract_dynamodb_value(dynamo_value)
        expected = ["item1", "item2", 123.0]
        assert result == expected

    def test_extract_empty_value(self):
        """Test extracting empty value."""
        result = extract_dynamodb_value({})
        assert result is None


class TestGetChromadbRelevantChanges:
    """Test ChromaDB-relevant change detection."""

    def test_metadata_changes_detected(self):
        """Test metadata field changes are detected."""
        entity_data = {"entity_type": "RECEIPT_METADATA"}
        old_image = {
            "canonical_merchant_name": {"S": "Old Merchant"},
            "address": {"S": "Old Address"},
        }
        new_image = {
            "canonical_merchant_name": {"S": "New Merchant"},
            "address": {"S": "Old Address"},
            "place_id": {"S": "new_place_123"},
        }

        changes = get_chromadb_relevant_changes(
            entity_data, old_image, new_image
        )

        assert "canonical_merchant_name" in changes
        assert changes["canonical_merchant_name"]["old"] == "Old Merchant"
        assert changes["canonical_merchant_name"]["new"] == "New Merchant"
        assert "place_id" in changes
        assert changes["place_id"]["old"] is None
        assert changes["place_id"]["new"] == "new_place_123"
        assert "address" not in changes  # No change

    def test_label_changes_detected(self):
        """Test label field changes are detected."""
        entity_data = {"entity_type": "RECEIPT_WORD_LABEL"}
        old_image = {
            "label": {"S": "OLD_LABEL"},
            "validation_status": {"S": "PENDING"},
            "reasoning": {"S": "Old reasoning"},
        }
        new_image = {
            "label": {"S": "NEW_LABEL"},
            "validation_status": {"S": "VALIDATED"},
            "reasoning": {"S": "Old reasoning"},
        }

        changes = get_chromadb_relevant_changes(
            entity_data, old_image, new_image
        )

        assert "label" in changes
        assert changes["label"]["old"] == "OLD_LABEL"
        assert changes["label"]["new"] == "NEW_LABEL"
        assert "validation_status" in changes
        assert changes["validation_status"]["old"] == "PENDING"
        assert changes["validation_status"]["new"] == "VALIDATED"
        assert "reasoning" not in changes  # No change

    def test_no_changes_detected(self):
        """Test when no relevant changes are detected."""
        entity_data = {"entity_type": "RECEIPT_METADATA"}
        old_image = {
            "canonical_merchant_name": {"S": "Same Merchant"},
            "address": {"S": "Same Address"},
        }
        new_image = {
            "canonical_merchant_name": {"S": "Same Merchant"},
            "address": {"S": "Same Address"},
        }

        changes = get_chromadb_relevant_changes(
            entity_data, old_image, new_image
        )
        assert changes == {}

    def test_unknown_entity_type(self):
        """Test handling of unknown entity types."""
        entity_data = {"entity_type": "UNKNOWN_TYPE"}
        old_image = {"some_field": {"S": "old_value"}}
        new_image = {"some_field": {"S": "new_value"}}

        changes = get_chromadb_relevant_changes(
            entity_data, old_image, new_image
        )
        assert changes == {}


class TestSendMessagesToSqs:
    """Test SQS message sending."""

    @patch.dict(
        os.environ,
        {
            "COMPACTION_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123/test-queue"
        },
    )
    @patch("stream_processor.boto3.client")
    def test_send_single_batch(self, mock_boto3_client):
        """Test sending a single batch of messages."""
        mock_sqs = MagicMock()
        mock_boto3_client.return_value = mock_sqs
        mock_sqs.send_message_batch.return_value = {
            "Successful": [{"Id": "0"}, {"Id": "1"}],
            "Failed": [],
        }

        messages = [
            {"entity_type": "RECEIPT_METADATA", "event_name": "MODIFY"},
            {"entity_type": "RECEIPT_WORD_LABEL", "event_name": "REMOVE"},
        ]

        sent_count = send_messages_to_sqs(messages)

        assert sent_count == 2
        mock_sqs.send_message_batch.assert_called_once()
        call_args = mock_sqs.send_message_batch.call_args[1]
        assert (
            call_args["QueueUrl"]
            == "https://sqs.us-east-1.amazonaws.com/123/test-queue"
        )
        assert len(call_args["Entries"]) == 2

    @patch.dict(
        os.environ,
        {
            "COMPACTION_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123/test-queue"
        },
    )
    @patch("stream_processor.boto3.client")
    def test_send_multiple_batches(self, mock_boto3_client):
        """Test sending multiple batches (>10 messages)."""
        mock_sqs = MagicMock()
        mock_boto3_client.return_value = mock_sqs

        def batch_response_side_effect(*args, **kwargs):
            # Return successful response based on actual batch size
            entries = kwargs.get("Entries", [])
            return {
                "Successful": [{"Id": entry["Id"]} for entry in entries],
                "Failed": [],
            }

        mock_sqs.send_message_batch.side_effect = batch_response_side_effect

        # Create 25 messages to trigger 3 batches (10, 10, 5)
        messages = [
            {
                "entity_type": "RECEIPT_METADATA",
                "event_name": "MODIFY",
                "entity_data": {"image_id": f"test-{i}", "receipt_id": 1},
            }
            for i in range(25)
        ]

        sent_count = send_messages_to_sqs(messages)

        assert sent_count == 25  # All messages sent successfully
        assert mock_sqs.send_message_batch.call_count == 3

        # Verify batch sizes match expectations
        call_args_list = mock_sqs.send_message_batch.call_args_list
        batch_sizes = [len(call[1]["Entries"]) for call in call_args_list]
        assert batch_sizes == [10, 10, 5]

    @patch.dict(
        os.environ,
        {
            "COMPACTION_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123/test-queue"
        },
    )
    @patch("stream_processor.boto3.client")
    def test_send_with_failures(self, mock_boto3_client):
        """Test handling SQS send failures."""
        mock_sqs = MagicMock()
        mock_boto3_client.return_value = mock_sqs
        mock_sqs.send_message_batch.return_value = {
            "Successful": [{"Id": "0"}],
            "Failed": [
                {
                    "Id": "1",
                    "Code": "InvalidMessageContents",
                    "Message": "Test failure",
                }
            ],
        }

        messages = [
            {"entity_type": "RECEIPT_METADATA", "event_name": "MODIFY"},
            {"entity_type": "RECEIPT_WORD_LABEL", "event_name": "REMOVE"},
        ]

        sent_count = send_messages_to_sqs(messages)

        assert sent_count == 1  # Only one successful


class TestLambdaHandler:
    """Test the main Lambda handler."""

    @patch.dict(
        os.environ,
        {
            "COMPACTION_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123/test-queue"
        },
    )
    @patch("stream_processor.send_messages_to_sqs")
    def test_handler_processes_modify_event(self, mock_send_messages):
        """Test handler processes MODIFY events correctly."""
        mock_send_messages.return_value = 1

        event = {
            "Records": [
                {
                    "eventID": "test-event-1",
                    "eventName": "MODIFY",
                    "awsRegion": "us-east-1",
                    "dynamodb": {
                        "Keys": {
                            "PK": {
                                "S": "IMAGE#550e8400-e29b-41d4-a716-446655440000"
                            },
                            "SK": {"S": "RECEIPT#00001#METADATA"},
                        },
                        "OldImage": {
                            "canonical_merchant_name": {"S": "Old Merchant"}
                        },
                        "NewImage": {
                            "canonical_merchant_name": {"S": "New Merchant"}
                        },
                    },
                }
            ]
        }

        response = lambda_handler(event, None)

        assert response["statusCode"] == 200
        assert response["processed_records"] == 1
        assert response["queued_messages"] == 1
        mock_send_messages.assert_called_once()

        # Check the message content
        sent_messages = mock_send_messages.call_args[0][0]
        assert len(sent_messages) == 1
        message = sent_messages[0]
        assert message["source"] == "dynamodb_stream"
        assert message["entity_type"] == "RECEIPT_METADATA"
        assert message["event_name"] == "MODIFY"
        assert "canonical_merchant_name" in message["changes"]

    @patch.dict(
        os.environ,
        {
            "COMPACTION_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123/test-queue"
        },
    )
    @patch("stream_processor.send_messages_to_sqs")
    def test_handler_processes_remove_event(self, mock_send_messages):
        """Test handler processes REMOVE events correctly."""
        mock_send_messages.return_value = 1

        event = {
            "Records": [
                {
                    "eventID": "test-event-2",
                    "eventName": "REMOVE",
                    "awsRegion": "us-east-1",
                    "dynamodb": {
                        "Keys": {
                            "PK": {
                                "S": "IMAGE#550e8400-e29b-41d4-a716-446655440000"
                            },
                            "SK": {
                                "S": "RECEIPT#00001#LINE#00002#WORD#00003#LABEL#TOTAL"
                            },
                        },
                        "OldImage": {
                            "label": {"S": "TOTAL"},
                            "validation_status": {"S": "VALIDATED"},
                        },
                    },
                }
            ]
        }

        response = lambda_handler(event, None)

        assert response["statusCode"] == 200
        assert response["processed_records"] == 1
        assert response["queued_messages"] == 1

        # Check the message content
        sent_messages = mock_send_messages.call_args[0][0]
        message = sent_messages[0]
        assert message["entity_type"] == "RECEIPT_WORD_LABEL"
        assert message["event_name"] == "REMOVE"

    @patch("stream_processor.send_messages_to_sqs")
    def test_handler_ignores_insert_events(self, mock_send_messages):
        """Test handler ignores INSERT events."""
        event = {
            "Records": [
                {
                    "eventID": "test-event-3",
                    "eventName": "INSERT",
                    "awsRegion": "us-east-1",
                    "dynamodb": {
                        "Keys": {
                            "PK": {
                                "S": "IMAGE#550e8400-e29b-41d4-a716-446655440000"
                            },
                            "SK": {"S": "RECEIPT#00001#METADATA"},
                        },
                        "NewImage": {
                            "canonical_merchant_name": {"S": "New Merchant"}
                        },
                    },
                }
            ]
        }

        response = lambda_handler(event, None)

        assert response["statusCode"] == 200
        assert response["processed_records"] == 0
        assert response["queued_messages"] == 0
        mock_send_messages.assert_not_called()

    @patch("stream_processor.send_messages_to_sqs")
    def test_handler_ignores_irrelevant_entities(self, mock_send_messages):
        """Test handler ignores irrelevant entity types."""
        event = {
            "Records": [
                {
                    "eventID": "test-event-4",
                    "eventName": "MODIFY",
                    "awsRegion": "us-east-1",
                    "dynamodb": {
                        "Keys": {
                            "PK": {
                                "S": "IMAGE#550e8400-e29b-41d4-a716-446655440000"
                            },
                            "SK": {
                                "S": "RECEIPT#00001#LINE#00002"
                            },  # RECEIPT_LINE - ignored
                        },
                        "OldImage": {"text": {"S": "old text"}},
                        "NewImage": {"text": {"S": "new text"}},
                    },
                }
            ]
        }

        response = lambda_handler(event, None)

        assert response["statusCode"] == 200
        assert response["processed_records"] == 0
        assert response["queued_messages"] == 0
        mock_send_messages.assert_not_called()

    @patch("stream_processor.send_messages_to_sqs")
    def test_handler_handles_errors_gracefully(self, mock_send_messages):
        """Test handler handles processing errors gracefully."""
        # Create an event with malformed data
        event = {
            "Records": [
                {
                    "eventID": "test-event-5",
                    "eventName": "MODIFY",
                    "awsRegion": "us-east-1",
                    "dynamodb": {
                        "Keys": {
                            "PK": {
                                "S": "MALFORMED"
                            },  # This will cause parsing to fail
                            "SK": {"S": "MALFORMED"},
                        }
                    },
                }
            ]
        }

        response = lambda_handler(event, None)

        # Should still return success but with 0 processed records
        assert response["statusCode"] == 200
        assert response["processed_records"] == 0
        assert response["queued_messages"] == 0
        mock_send_messages.assert_not_called()


if __name__ == "__main__":
    pytest.main([__file__])
