"""
Unit tests for DynamoDB Stream Processor Lambda

Tests the stream processor functionality for ChromaDB metadata synchronization.
"""

import json
import os
import uuid
from datetime import datetime
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

# Import entity classes for test data creation
from receipt_dynamo.entities.receipt_metadata import ReceiptMetadata
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel
from receipt_dynamo.constants import ValidationStatus, ValidationMethod

from ..lambdas.stream_processor import (
    FieldChange,
    LambdaResponse,
    ParsedStreamRecord,
    get_chromadb_relevant_changes,
    lambda_handler,
    parse_stream_record,
    send_messages_to_queues,
)


# Helper functions to create test entities using the actual entity classes
def create_test_receipt_metadata(
    merchant_name: str = "Test Merchant",
    canonical_merchant_name: str = "",
    **kwargs
) -> ReceiptMetadata:
    """Create a test ReceiptMetadata entity with sensible defaults."""
    defaults = {
        "image_id": "550e8400-e29b-41d4-a716-446655440000",
        "receipt_id": 1,
        "place_id": "place123",
        "merchant_name": merchant_name,
        "canonical_merchant_name": canonical_merchant_name,
        "matched_fields": ["name"],
        "validated_by": ValidationMethod.PHONE_LOOKUP,
        "timestamp": datetime.fromisoformat("2024-01-01T00:00:00"),
    }
    defaults.update(kwargs)
    return ReceiptMetadata(**defaults)


def create_test_receipt_word_label(
    label: str = "TEST_LABEL",
    **kwargs
) -> ReceiptWordLabel:
    """Create a test ReceiptWordLabel entity with sensible defaults."""
    defaults = {
        "image_id": "550e8400-e29b-41d4-a716-446655440000",
        "receipt_id": 1,
        "line_id": 2,
        "word_id": 3,
        "label": label,
        "validation_status": ValidationStatus.VALID,
        "reasoning": "Test reasoning",
        "timestamp_added": datetime.fromisoformat("2024-01-01T00:00:00"),
    }
    defaults.update(kwargs)
    return ReceiptWordLabel(**defaults)


def create_stream_record_from_entities(
    event_name: str,
    old_entity=None,
    new_entity=None,
    event_id: str = "test-event-1",
    aws_region: str = "us-east-1"
) -> Dict[str, Any]:
    """Create a DynamoDB stream record from entity objects using their key/to_item methods."""
    # Get the key from whichever entity is available
    entity = old_entity or new_entity
    if not entity:
        raise ValueError("At least one entity (old or new) must be provided")
    
    # Use the entity's key method for Keys
    keys = entity.key
    
    record = {
        "eventID": event_id,
        "eventName": event_name,
        "awsRegion": aws_region,
        "dynamodb": {
            "Keys": keys
        }
    }
    
    # Use entity's to_item method for OldImage/NewImage
    if old_entity:
        record["dynamodb"]["OldImage"] = old_entity.to_item()
    if new_entity:
        record["dynamodb"]["NewImage"] = new_entity.to_item()
        
    return record


class TestParseStreamRecord:
    """Test DynamoDB stream record parsing logic."""
    
    def test_parse_receipt_metadata_modify_event(self):
        """Test parsing RECEIPT_METADATA MODIFY event."""
        old_entity = create_test_receipt_metadata(merchant_name="Old Merchant")
        new_entity = create_test_receipt_metadata(merchant_name="New Merchant")
        
        record = create_stream_record_from_entities(
            event_name="MODIFY",
            old_entity=old_entity,
            new_entity=new_entity
        )

        result = parse_stream_record(record)

        assert result is not None
        assert isinstance(result, ParsedStreamRecord)
        assert result.entity_type == "RECEIPT_METADATA"
        # Use the entity's key for validation instead of hardcoded strings
        expected_key = old_entity.key
        assert result.pk == expected_key["PK"]["S"]
        assert result.sk == expected_key["SK"]["S"]
        assert result.old_entity is not None
        assert result.new_entity is not None
        assert isinstance(result.old_entity, ReceiptMetadata)
        assert isinstance(result.new_entity, ReceiptMetadata)
        # Use the actual entity attributes
        assert result.old_entity.merchant_name == old_entity.merchant_name
        assert result.new_entity.merchant_name == new_entity.merchant_name

    def test_parse_receipt_word_label_remove_event(self):
        """Test parsing RECEIPT_WORD_LABEL REMOVE event."""
        old_entity = create_test_receipt_word_label(label="TOTAL")
        
        record = create_stream_record_from_entities(
            event_name="REMOVE",
            old_entity=old_entity,
            new_entity=None
        )

        result = parse_stream_record(record)

        assert result is not None
        assert isinstance(result, ParsedStreamRecord)
        assert result.entity_type == "RECEIPT_WORD_LABEL"
        # Use the entity's key for validation
        expected_key = old_entity.key
        assert result.pk == expected_key["PK"]["S"]
        assert result.sk == expected_key["SK"]["S"]
        assert result.old_entity is not None
        assert result.new_entity is None  # REMOVE event
        assert isinstance(result.old_entity, ReceiptWordLabel)
        # Use the actual entity attributes
        assert result.old_entity.label == old_entity.label
        assert result.old_entity.validation_status == old_entity.validation_status

    def test_parse_non_image_pk(self):
        """Test that non-IMAGE PKs are ignored."""
        record = {
            "eventName": "MODIFY",
            "dynamodb": {
                "Keys": {
                    "PK": {"S": "BATCH#12345"},
                    "SK": {"S": "RECEIPT#00001#METADATA"}
                }
            }
        }

        result = parse_stream_record(record)
        assert result is None

    def test_parse_receipt_line_ignored(self):
        """Test that RECEIPT_LINE entities are ignored."""
        record = {
            "eventName": "MODIFY",
            "dynamodb": {
                "Keys": {
                    "PK": {"S": "IMAGE#550e8400-e29b-41d4-a716-446655440000"},
                    "SK": {"S": "RECEIPT#00001#LINE#00002"}
                }
            }
        }

        result = parse_stream_record(record)
        assert result is None

    def test_parse_receipt_word_ignored(self):
        """Test that RECEIPT_WORD entities are ignored."""
        record = {
            "eventName": "MODIFY",
            "dynamodb": {
                "Keys": {
                    "PK": {"S": "IMAGE#550e8400-e29b-41d4-a716-446655440000"},
                    "SK": {"S": "RECEIPT#00001#LINE#00002#WORD#00003"}
                }
            }
        }

        result = parse_stream_record(record)
        assert result is None

    def test_parse_invalid_record_format(self):
        """Test parsing record with missing required fields."""
        record = {
            "eventName": "MODIFY",
            "dynamodb": {
                # Missing Keys
            }
        }

        result = parse_stream_record(record)
        assert result is None


class TestDataclasses:
    """Test dataclass functionality."""
    
    def test_lambda_response_to_dict(self):
        """Test LambdaResponse to_dict conversion."""
        response = LambdaResponse(
            status_code=200,
            processed_records=5,
            queued_messages=3
        )
        
        result = response.to_dict()
        
        assert result == {
            "statusCode": 200,
            "processed_records": 5,
            "queued_messages": 3
        }
    
    def test_field_change_creation(self):
        """Test FieldChange dataclass creation."""
        change = FieldChange(old="old_value", new="new_value")
        
        assert change.old == "old_value"
        assert change.new == "new_value"
    
    def test_parsed_stream_record_creation(self):
        """Test ParsedStreamRecord dataclass creation."""
        old_entity = create_test_receipt_metadata(merchant_name="Old Merchant")
        
        # Use the entity's key for PK/SK values
        entity_key = old_entity.key
        pk = entity_key["PK"]["S"]
        sk = entity_key["SK"]["S"]
        
        parsed = ParsedStreamRecord(
            entity_type="RECEIPT_METADATA",
            old_entity=old_entity,
            new_entity=None,
            pk=pk,
            sk=sk
        )
        
        assert parsed.entity_type == "RECEIPT_METADATA"
        assert parsed.old_entity == old_entity
        assert parsed.new_entity is None
        assert parsed.pk == pk
        assert parsed.sk == sk


class TestGetChromadbRelevantChanges:
    """Test ChromaDB-relevant change detection."""

    def test_metadata_changes_detected(self):
        """Test metadata field changes are detected."""
        old_entity = create_test_receipt_metadata(
            merchant_name="Old Merchant",
            canonical_merchant_name="Old Merchant",
            place_id="old_place_123",
            address="Old Address"
        )
        
        new_entity = create_test_receipt_metadata(
            merchant_name="New Merchant",
            canonical_merchant_name="New Merchant",
            place_id="new_place_123",
            address="Old Address"  # Same address - no change expected
        )

        changes = get_chromadb_relevant_changes(
            "RECEIPT_METADATA", old_entity, new_entity
        )

        assert "canonical_merchant_name" in changes
        assert isinstance(changes["canonical_merchant_name"], FieldChange)
        assert changes["canonical_merchant_name"].old == old_entity.canonical_merchant_name
        assert changes["canonical_merchant_name"].new == new_entity.canonical_merchant_name
        assert "place_id" in changes
        assert changes["place_id"].old == old_entity.place_id
        assert changes["place_id"].new == new_entity.place_id
        assert "merchant_name" in changes
        assert changes["merchant_name"].old == old_entity.merchant_name
        assert changes["merchant_name"].new == new_entity.merchant_name
        assert "address" not in changes  # No change (both entities have same address)

    def test_label_changes_detected(self):
        """Test label field changes are detected."""
        old_entity = create_test_receipt_word_label(
            label="OLD_LABEL",
            validation_status=ValidationStatus.PENDING,
            reasoning="Old reasoning"
        )
        
        new_entity = create_test_receipt_word_label(
            label="NEW_LABEL",
            validation_status=ValidationStatus.VALID,
            reasoning="Old reasoning"  # Same reasoning - no change expected
        )

        changes = get_chromadb_relevant_changes(
            "RECEIPT_WORD_LABEL", old_entity, new_entity
        )

        assert "label" in changes
        assert isinstance(changes["label"], FieldChange)
        assert changes["label"].old == old_entity.label
        assert changes["label"].new == new_entity.label
        assert "validation_status" in changes
        assert changes["validation_status"].old == old_entity.validation_status
        assert changes["validation_status"].new == new_entity.validation_status
        assert "reasoning" not in changes  # No change (both entities have same reasoning)

    def test_no_changes_detected(self):
        """Test when no relevant changes are detected."""
        entity = create_test_receipt_metadata(
            merchant_name="Same Merchant",
            canonical_merchant_name="Same Merchant",
            place_id="same_place_123",
            address="Same Address"
        )

        changes = get_chromadb_relevant_changes(
            "RECEIPT_METADATA", entity, entity  # Same entity
        )
        assert changes == {}

    def test_unknown_entity_type(self):
        """Test handling of unknown entity types."""
        entity = create_test_receipt_metadata(merchant_name="Test Merchant")

        changes = get_chromadb_relevant_changes(
            "UNKNOWN_TYPE", entity, entity
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

        sent_count = send_messages_to_queues(messages)

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

        sent_count = send_messages_to_queues(messages)

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

        sent_count = send_messages_to_queues(messages)

        assert sent_count == 1  # Only one successful


class TestLambdaHandler:
    """Test the main Lambda handler."""

    @patch.dict(
        os.environ,
        {
            "COMPACTION_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123/test-queue"
        },
    )
    @patch("infra.chromadb_compaction.lambdas.stream_processor.send_messages_to_queues")
    def test_handler_processes_modify_event(self, mock_send_messages):
        """Test handler processes MODIFY events correctly."""
        mock_send_messages.return_value = 1

        # Create test entities using the entity classes
        old_entity = create_test_receipt_metadata(
            merchant_name="Old Merchant",
            canonical_merchant_name="Old Merchant"
        )
        new_entity = create_test_receipt_metadata(
            merchant_name="New Merchant", 
            canonical_merchant_name="New Merchant"
        )

        # Create stream record using entity methods
        stream_record = create_stream_record_from_entities(
            event_name="MODIFY",
            old_entity=old_entity,
            new_entity=new_entity,
            event_id="test-event-1"
        )

        event = {"Records": [stream_record]}

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
    @patch("infra.chromadb_compaction.lambdas.stream_processor.send_messages_to_queues")
    def test_handler_processes_remove_event(self, mock_send_messages):
        """Test handler processes REMOVE events correctly."""
        mock_send_messages.return_value = 1

        # Create test entity using the entity class
        old_entity = create_test_receipt_word_label(label="TOTAL")

        # Create stream record using entity methods
        stream_record = create_stream_record_from_entities(
            event_name="REMOVE",
            old_entity=old_entity,
            new_entity=None,
            event_id="test-event-2"
        )

        event = {"Records": [stream_record]}

        response = lambda_handler(event, None)

        assert response["statusCode"] == 200
        assert response["processed_records"] == 1
        assert response["queued_messages"] == 1

        # Check the message content
        sent_messages = mock_send_messages.call_args[0][0]
        message = sent_messages[0]
        assert message["entity_type"] == "RECEIPT_WORD_LABEL"
        assert message["event_name"] == "REMOVE"

    @patch("infra.chromadb_compaction.lambdas.stream_processor.send_messages_to_queues")
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

    @patch("infra.chromadb_compaction.lambdas.stream_processor.send_messages_to_queues")
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

    @patch("infra.chromadb_compaction.lambdas.stream_processor.send_messages_to_queues")
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
