"""
Simplified Unit tests for Enhanced ChromaDB Compaction Handler

Focuses on core business logic that's most critical to test:
- Metadata update operations
- Label update operations
- ChromaDB ID construction
- Message categorization logic
"""

import json
import os
from unittest.mock import MagicMock, patch

from receipt_dynamo.constants import ChromaDBCollection
from ..lambdas.enhanced_compaction_handler import (
    LambdaResponse,
    StreamMessage,
    update_receipt_metadata,
    update_word_labels,
)


class TestCoreCompactionLogic:
    """Test the core business logic that's most error-prone and critical."""

    @patch.dict(
        os.environ,
        {
            "DYNAMODB_TABLE_NAME": "test-table",
            "CHROMADB_BUCKET": "test-bucket",
            "COMPACTION_QUEUE_URL": (
                "https://sqs.us-east-1.amazonaws.com/123/test-queue"
            ),
            "HEARTBEAT_INTERVAL_SECONDS": "60",
            "LOCK_DURATION_MINUTES": "15",
        },
    )
    @patch('infra.chromadb_compaction.lambdas.enhanced_compaction_handler.get_dynamo_client')
    def test_update_receipt_metadata_logic(self, mock_get_dynamo_client):
        """Test the core metadata update logic."""

        # Setup mock DynamoDB client
        mock_dynamo_client = MagicMock()
        mock_get_dynamo_client.return_value = mock_dynamo_client
        
        # Create mock receipt lines 
        from receipt_dynamo.entities.receipt_line import ReceiptLine
        test_image_id = "550e8400-e29b-41d4-a716-446655440000"
        mock_lines = [
            ReceiptLine(
                image_id=test_image_id,
                receipt_id=456,
                line_id=1,
                text="Line 1",
                bounding_box={"x": 0, "y": 0, "width": 100, "height": 20},
                top_left={"x": 0, "y": 0},
                top_right={"x": 100, "y": 0},
                bottom_left={"x": 0, "y": 20},
                bottom_right={"x": 100, "y": 20},
                angle_degrees=0.0,
                angle_radians=0.0,
                confidence=0.95,
                embedding_status="PENDING"
            ),
            ReceiptLine(
                image_id=test_image_id,
                receipt_id=456,
                line_id=2,
                text="Line 2",
                bounding_box={"x": 0, "y": 25, "width": 100, "height": 20},
                top_left={"x": 0, "y": 25},
                top_right={"x": 100, "y": 25},
                bottom_left={"x": 0, "y": 45},
                bottom_right={"x": 100, "y": 45},
                angle_degrees=0.0,
                angle_radians=0.0,
                confidence=0.95,
                embedding_status="PENDING"
            ),
            ReceiptLine(
                image_id=test_image_id,
                receipt_id=456,
                line_id=3,
                text="Line 3",
                bounding_box={"x": 0, "y": 50, "width": 100, "height": 20},
                top_left={"x": 0, "y": 50},
                top_right={"x": 100, "y": 50},
                bottom_left={"x": 0, "y": 70},
                bottom_right={"x": 100, "y": 70},
                angle_degrees=0.0,
                angle_radians=0.0,
                confidence=0.95,
                embedding_status="PENDING"
            ),
        ]
        mock_dynamo_client.list_receipt_lines_from_receipt.return_value = mock_lines

        # Setup mock collection
        mock_collection = MagicMock()
        mock_collection.name = "lines_collection"  # Set proper collection name

        # Mock the collection.get() call to return existing records
        mock_collection.get.return_value = {
            "ids": [
                f"IMAGE#{test_image_id}#RECEIPT#00456#LINE#00001",
                f"IMAGE#{test_image_id}#RECEIPT#00456#LINE#00002", 
                f"IMAGE#{test_image_id}#RECEIPT#00456#LINE#00003",
            ],
            "metadatas": [
                {"existing_field": "value1", "old_merchant": "Old Store"},
                {"existing_field": "value2", "old_merchant": "Old Store"},
                {"existing_field": "value3"},
            ],
        }

        # Test metadata update
        changes = {
            "canonical_merchant_name": {
                "old": "Old Store",
                "new": "New Store",
            },
            "merchant_category": {"old": None, "new": "Restaurant"},
        }

        updated_count = update_receipt_metadata(
            mock_collection, test_image_id, 456, changes
        )

        # Verify update was called correctly
        assert updated_count == 3
        mock_collection.update.assert_called_once()

        # Check the update call arguments
        call_args = mock_collection.update.call_args
        updated_ids = call_args[1]["ids"]
        updated_metadatas = call_args[1]["metadatas"]

        assert len(updated_ids) == 3
        assert len(updated_metadatas) == 3

        # Verify metadata was updated correctly
        for metadata in updated_metadatas:
            assert metadata["canonical_merchant_name"] == "New Store"
            assert metadata["merchant_category"] == "Restaurant"
            assert "last_metadata_update" in metadata

    @patch.dict(os.environ, {"CHROMADB_BUCKET": "test-bucket"})
    def test_update_word_labels_logic(self):
        """Test the core word label update logic."""

        mock_collection = MagicMock()

        # Mock successful record retrieval
        mock_collection.get.return_value = {
            "ids": ["IMAGE#test123#RECEIPT#00456#LINE#00001#WORD#00001"],
            "metadatas": [
                {"existing_field": "value", "label_old_field": "old_value"}
            ],
        }

        changes = {
            "label": {"old": "OLD_LABEL", "new": "NEW_LABEL"},
            "reasoning": {"old": None, "new": "Updated reasoning"},
        }

        updated_count = update_word_labels(
            mock_collection,
            "IMAGE#test123#RECEIPT#00456#LINE#00001#WORD#00001",
            changes,
        )

        assert updated_count == 1
        mock_collection.update.assert_called_once()

        # Check the updated metadata
        call_args = mock_collection.update.call_args
        updated_metadata = call_args[1]["metadatas"][0]

        assert updated_metadata["label_label"] == "NEW_LABEL"
        assert updated_metadata["label_reasoning"] == "Updated reasoning"
        assert "last_label_update" in updated_metadata

    def test_chromadb_id_construction(self):
        """
        Test ChromaDB ID pattern construction - critical for data integrity.
        """

        # Test receipt metadata ID pattern
        image_id = "img_12345"
        receipt_id = 789
        expected_prefix = f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#"

        # This pattern is used throughout the code
        assert expected_prefix == "IMAGE#img_12345#RECEIPT#00789#"

        # Test full word label ID pattern
        line_id = 2
        word_id = 15
        full_id = f"{expected_prefix}LINE#{line_id:05d}#WORD#{word_id:05d}"

        assert full_id == "IMAGE#img_12345#RECEIPT#00789#LINE#00002#WORD#00015"

    def test_sqs_message_categorization(self):
        """Test message categorization logic."""

        # Mock SQS messages with different sources
        messages = [
            {
                "body": '{"entity_type": "RECEIPT_METADATA"}',
                "messageAttributes": {
                    "source": {"stringValue": "dynamodb_stream"}
                },
            },
            {
                "body": '{"entity_type": "RECEIPT_WORD_LABEL"}',
                "messageAttributes": {
                    "source": {"stringValue": "dynamodb_stream"}
                },
            },
            {
                "body": '{"delta_file": "some_delta.json"}',
                "messageAttributes": {
                    "source": {"stringValue": "delta_processor"}
                },
            },
            {"body": '{"unknown": "message"}', "messageAttributes": {}},
        ]

        # Simulate the categorization logic from process_sqs_messages
        stream_messages = []
        delta_messages = []

        for record in messages:
            message_body = json.loads(record["body"])
            attributes = record.get("messageAttributes", {})
            source = attributes.get("source", {}).get("stringValue", "unknown")

            if source == "dynamodb_stream":
                stream_messages.append(message_body)
            else:
                delta_messages.append(message_body)

        assert len(stream_messages) == 2
        assert len(delta_messages) == 2
        assert stream_messages[0]["entity_type"] == "RECEIPT_METADATA"
        assert stream_messages[1]["entity_type"] == "RECEIPT_WORD_LABEL"

    def test_metadata_field_filtering(self):
        """Test that only relevant metadata fields are processed."""

        # These are the fields that should trigger ChromaDB updates
        # pylint: disable=duplicate-code
        # Field lists are naturally duplicated in related test modules
        relevant_fields = [
            "canonical_merchant_name",
            "merchant_name",
            "merchant_category",
            "address",
            "phone_number",
            "place_id",
        ]

        # Mock entity with many fields
        mock_changes = {
            "canonical_merchant_name": {
                "old": "Old Store",
                "new": "New Store",
            },
            "merchant_category": {"old": None, "new": "Restaurant"},
            "internal_id": {"old": "123", "new": "456"},  # Should be ignored
            "created_at": {
                "old": "2023-01-01",
                "new": "2023-01-02",
            },  # Should be ignored
            "address": {"old": "123 Main St", "new": "456 Oak St"},
        }

        # Filter to only relevant fields
        # (simulating the actual filtering logic)
        filtered_changes = {
            field: change
            for field, change in mock_changes.items()
            if field in relevant_fields
        }

        assert len(filtered_changes) == 3  # Only 3 relevant fields
        assert "canonical_merchant_name" in filtered_changes
        assert "merchant_category" in filtered_changes
        assert "address" in filtered_changes
        assert "internal_id" not in filtered_changes
        assert "created_at" not in filtered_changes


class TestDataclassIntegration:
    """Test the dataclass functionality we added."""

    def test_lambda_response_creation(self):
        """Test LambdaResponse dataclass works correctly."""

        # Test with minimal fields
        response = LambdaResponse(status_code=200, message="Success")

        result = response.to_dict()
        assert result["statusCode"] == 200
        assert result["message"] == "Success"
        assert (
            "processed_messages" not in result
        )  # Optional fields not included

        # Test with optional fields
        response_with_optional = LambdaResponse(
            status_code=200,
            message="Success",
            processed_messages=5,
            stream_messages=3,
            delta_messages=2,
        )

        result = response_with_optional.to_dict()
        assert result["processed_messages"] == 5
        assert result["stream_messages"] == 3
        assert result["delta_messages"] == 2

    def test_stream_message_parsing(self):
        """Test StreamMessage dataclass parsing."""

        message_dict = {
            "entity_type": "RECEIPT_METADATA",
            "entity_data": {"image_id": "test123", "receipt_id": 456},
            "changes": {"merchant_name": {"old": "Old", "new": "New"}},
            "event_name": "MODIFY",
        }

        stream_msg = StreamMessage(
            entity_type=message_dict.get("entity_type", ""),
            entity_data=message_dict.get("entity_data", {}),
            changes=message_dict.get("changes", {}),
            event_name=message_dict.get("event_name", ""),
            collection=ChromaDBCollection.LINES,
            source=message_dict.get("source", "dynamodb_stream"),
        )

        assert stream_msg.entity_type == "RECEIPT_METADATA"
        assert stream_msg.entity_data["image_id"] == "test123"
        assert stream_msg.changes["merchant_name"]["new"] == "New"
        assert stream_msg.collection == ChromaDBCollection.LINES
        assert stream_msg.source == "dynamodb_stream"  # Default value
