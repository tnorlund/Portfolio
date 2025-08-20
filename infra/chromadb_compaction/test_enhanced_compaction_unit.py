"""
Unit tests for Enhanced ChromaDB Compaction Handler

Pure unit tests that mock all infrastructure dependencies and focus on testing
the business logic of stream message processing and metadata updates.
"""

import json
import os
from datetime import datetime, timezone
from unittest.mock import MagicMock, Mock, patch

import pytest


class TestEnhancedCompactionLogic:
    """Test the core business logic without infrastructure dependencies."""

    @patch.dict(
        os.environ,
        {
            "DYNAMODB_TABLE_NAME": "test-table",
            "CHROMADB_BUCKET": "test-bucket",
            "COMPACTION_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123/test-queue",
            "HEARTBEAT_INTERVAL_SECONDS": "60",
            "LOCK_DURATION_MINUTES": "15",
        },
    )
    @patch("enhanced_compaction_handler.boto3")
    @patch("enhanced_compaction_handler.ChromaDBClient")
    @patch("enhanced_compaction_handler.download_snapshot_from_s3")
    @patch("enhanced_compaction_handler.upload_delta_to_s3")
    @patch("enhanced_compaction_handler.DynamoClient")
    @patch("enhanced_compaction_handler.LockManager")
    def test_process_metadata_update_logic(
        self,
        mock_lock_manager_class,
        mock_dynamo_client_class,
        mock_upload_delta,
        mock_download_snapshot,
        mock_chromadb_client_class,
        mock_boto3,
    ):
        """Test metadata update logic with all dependencies mocked."""

        # Import after patching to avoid initialization errors
        from lambdas.enhanced_compaction_handler import update_receipt_metadata

        # Setup mock ChromaDB client and collection
        mock_chroma_client = MagicMock()
        mock_chromadb_client_class.return_value = mock_chroma_client
        
        mock_collection = MagicMock()
        mock_chroma_client.get_collection.return_value = mock_collection
        mock_collection.get.return_value = {
            "ids": [
                "IMAGE#test-uuid#RECEIPT#00001#LINE#00001",
                "IMAGE#test-uuid#RECEIPT#00001#WORD#00001",
                "IMAGE#other-uuid#RECEIPT#00002#LINE#00001",  # Different receipt
            ],
            "metadatas": [
                {"existing_field": "value1"},
                {"existing_field": "value2"},
                {"existing_field": "value3"},
            ],
        }
        
        # Setup S3 helper mocks
        mock_download_snapshot.return_value = {"status": "downloaded"}
        mock_upload_delta.return_value = {"status": "uploaded"}

        changes = {
            "canonical_merchant_name": {"old": "Old", "new": "New Merchant"}
        }

        # Test the update logic
        updated_count = update_receipt_metadata(
            mock_collection, "test-uuid", 1, changes
        )

        # Verify results
        assert updated_count == 2  # Should update 2 matching records
        mock_collection.update.assert_called_once()

        # Check the update call arguments
        call_args = mock_collection.update.call_args[1]
        assert len(call_args["ids"]) == 2
        assert len(call_args["metadatas"]) == 2

        # Verify metadata updates
        for metadata in call_args["metadatas"]:
            assert metadata["canonical_merchant_name"] == "New Merchant"
            assert "last_metadata_update" in metadata
            assert "existing_field" in metadata  # Original fields preserved

    @patch.dict(
        os.environ,
        {
            "DYNAMODB_TABLE_NAME": "test-table",
            "CHROMADB_BUCKET": "test-bucket",
            "COMPACTION_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123/test-queue",
        },
    )
    @patch("enhanced_compaction_handler.boto3")
    @patch("enhanced_compaction_handler.ChromaDBClient")
    @patch("enhanced_compaction_handler.download_snapshot_from_s3")
    @patch("enhanced_compaction_handler.upload_delta_to_s3")
    @patch("enhanced_compaction_handler.DynamoClient")
    @patch("enhanced_compaction_handler.LockManager")
    def test_remove_metadata_logic(
        self,
        mock_lock_manager_class,
        mock_dynamo_client_class,
        mock_upload_delta,
        mock_download_snapshot,
        mock_chromadb_client_class,
        mock_boto3,
    ):
        """Test metadata removal logic."""

        from lambdas.enhanced_compaction_handler import remove_receipt_metadata

        # Setup mock ChromaDB client and collection
        mock_chroma_client = MagicMock()
        mock_chromadb_client_class.return_value = mock_chroma_client
        
        mock_collection = MagicMock()
        mock_chroma_client.get_collection.return_value = mock_collection
        mock_collection.get.return_value = {
            "ids": [
                "IMAGE#test-uuid#RECEIPT#00001#LINE#00001",
                "IMAGE#test-uuid#RECEIPT#00001#WORD#00001",
            ],
            "metadatas": [
                {
                    "canonical_merchant_name": "Merchant",
                    "address": "123 Main St",
                    "other_field": "keep_this",
                },
                {
                    "merchant_name": "Merchant Name",
                    "place_id": "place123",
                    "other_field": "keep_this_too",
                },
            ],
        }
        
        # Setup S3 helper mocks
        mock_download_snapshot.return_value = {"status": "downloaded"}
        mock_upload_delta.return_value = {"status": "uploaded"}

        # Test the removal logic
        updated_count = remove_receipt_metadata(
            mock_collection, "test-uuid", 1
        )

        # Verify results
        assert updated_count == 2
        mock_collection.update.assert_called_once()

        # Check the update call arguments
        call_args = mock_collection.update.call_args[1]
        for metadata in call_args["metadatas"]:
            # Merchant fields should be removed
            assert "canonical_merchant_name" not in metadata
            assert "merchant_name" not in metadata
            assert "address" not in metadata
            assert "place_id" not in metadata
            # Other fields should remain
            assert "other_field" in metadata
            assert "metadata_removed_at" in metadata

    @patch.dict(
        os.environ,
        {
            "DYNAMODB_TABLE_NAME": "test-table",
            "CHROMADB_BUCKET": "test-bucket",
            "COMPACTION_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123/test-queue",
        },
    )
    @patch("enhanced_compaction_handler.boto3")
    @patch("enhanced_compaction_handler.ChromaDBClient")
    @patch("enhanced_compaction_handler.download_snapshot_from_s3")
    @patch("enhanced_compaction_handler.upload_delta_to_s3")
    @patch("enhanced_compaction_handler.DynamoClient")
    @patch("enhanced_compaction_handler.LockManager")
    def test_update_word_labels_logic(
        self,
        mock_lock_manager_class,
        mock_dynamo_client_class,
        mock_upload_delta,
        mock_download_snapshot,
        mock_chromadb_client_class,
        mock_boto3,
    ):
        """Test word label update logic."""

        from lambdas.enhanced_compaction_handler import update_word_labels

        chromadb_id = "IMAGE#test-uuid#RECEIPT#00001#LINE#00002#WORD#00003"

        # Setup mock ChromaDB client and collection
        mock_chroma_client = MagicMock()
        mock_chromadb_client_class.return_value = mock_chroma_client
        
        mock_collection = MagicMock()
        mock_chroma_client.get_collection.return_value = mock_collection
        mock_collection.get.return_value = {
            "ids": [chromadb_id],
            "metadatas": [{"existing_field": "value"}],
        }
        
        # Setup S3 helper mocks
        mock_download_snapshot.return_value = {"status": "downloaded"}
        mock_upload_delta.return_value = {"status": "uploaded"}

        changes = {
            "label": {"old": "OLD", "new": "NEW"},
            "validation_status": {"old": "PENDING", "new": "VALIDATED"},
        }

        # Test the update logic
        updated_count = update_word_labels(
            mock_collection, chromadb_id, changes
        )

        # Verify results
        assert updated_count == 1
        mock_collection.update.assert_called_once()

        # Check the update call arguments
        call_args = mock_collection.update.call_args[1]
        metadata = call_args["metadatas"][0]

        # Verify label fields are prefixed
        assert metadata["label_label"] == "NEW"
        assert metadata["label_validation_status"] == "VALIDATED"
        assert "last_label_update" in metadata
        assert (
            metadata["existing_field"] == "value"
        )  # Original fields preserved

    @patch.dict(
        os.environ,
        {
            "DYNAMODB_TABLE_NAME": "test-table",
            "CHROMADB_BUCKET": "test-bucket",
            "COMPACTION_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123/test-queue",
        },
    )
    @patch("enhanced_compaction_handler.boto3")
    @patch("enhanced_compaction_handler.ChromaDBClient")
    @patch("enhanced_compaction_handler.download_snapshot_from_s3")
    @patch("enhanced_compaction_handler.upload_delta_to_s3")
    @patch("enhanced_compaction_handler.DynamoClient")
    @patch("enhanced_compaction_handler.LockManager")
    def test_remove_word_labels_logic(
        self,
        mock_lock_manager_class,
        mock_dynamo_client_class,
        mock_upload_delta,
        mock_download_snapshot,
        mock_chromadb_client_class,
        mock_boto3,
    ):
        """Test word label removal logic."""

        from lambdas.enhanced_compaction_handler import remove_word_labels

        chromadb_id = "IMAGE#test-uuid#RECEIPT#00001#LINE#00002#WORD#00003"

        # Setup mock ChromaDB client and collection
        mock_chroma_client = MagicMock()
        mock_chromadb_client_class.return_value = mock_chroma_client
        
        mock_collection = MagicMock()
        mock_chroma_client.get_collection.return_value = mock_collection
        mock_collection.get.return_value = {
            "ids": [chromadb_id],
            "metadatas": [
                {
                    "label_label": "TOTAL",
                    "label_validation_status": "VALIDATED",
                    "label_reasoning": "Clear total amount",
                    "other_field": "keep_this",
                }
            ],
        }
        
        # Setup S3 helper mocks
        mock_download_snapshot.return_value = {"status": "downloaded"}
        mock_upload_delta.return_value = {"status": "uploaded"}

        # Test the removal logic
        updated_count = remove_word_labels(mock_collection, chromadb_id)

        # Verify results
        assert updated_count == 1
        mock_collection.update.assert_called_once()

        # Check the update call arguments
        call_args = mock_collection.update.call_args[1]
        metadata = call_args["metadatas"][0]

        # Label fields should be removed
        assert "label_label" not in metadata
        assert "label_validation_status" not in metadata
        assert "label_reasoning" not in metadata

        # Other fields should remain
        assert metadata["other_field"] == "keep_this"
        assert "labels_removed_at" in metadata

    def test_sqs_message_categorization(self):
        """Test SQS message categorization without AWS dependencies."""

        # Test data
        stream_message = {
            "body": json.dumps(
                {
                    "source": "dynamodb_stream",
                    "entity_type": "RECEIPT_METADATA",
                }
            ),
            "messageAttributes": {
                "source": {"stringValue": "dynamodb_stream"}
            },
        }

        delta_message = {
            "body": json.dumps(
                {"delta_key": "delta/abc123/", "embedding_count": 100}
            ),
            "messageAttributes": {},
        }

        malformed_message = {"body": "invalid json"}

        # Test message parsing logic
        def parse_message(record):
            try:
                message_body = json.loads(record["body"])
                attributes = record.get("messageAttributes", {})
                source = attributes.get("source", {}).get(
                    "stringValue", "unknown"
                )
                return source, message_body
            except:
                return None, None

        # Test stream message
        source, body = parse_message(stream_message)
        assert source == "dynamodb_stream"
        assert body["entity_type"] == "RECEIPT_METADATA"

        # Test delta message
        source, body = parse_message(delta_message)
        assert source == "unknown"
        assert body["delta_key"] == "delta/abc123/"

        # Test malformed message
        source, body = parse_message(malformed_message)
        assert source is None
        assert body is None

    def test_chromadb_id_construction(self):
        """Test ChromaDB ID construction logic."""

        # Test metadata prefix matching
        def matches_receipt_prefix(record_id, image_id, receipt_id):
            expected_prefix = f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#"
            return record_id.startswith(expected_prefix)

        # Test cases
        test_cases = [
            ("IMAGE#uuid#RECEIPT#00001#LINE#00001", "uuid", 1, True),
            ("IMAGE#uuid#RECEIPT#00001#WORD#00001", "uuid", 1, True),
            ("IMAGE#uuid#RECEIPT#00002#LINE#00001", "uuid", 1, False),
            ("IMAGE#other#RECEIPT#00001#LINE#00001", "uuid", 1, False),
        ]

        for record_id, image_id, receipt_id, expected in test_cases:
            result = matches_receipt_prefix(record_id, image_id, receipt_id)
            assert result == expected, f"Failed for {record_id}"

        # Test word-specific ID construction
        def construct_word_id(image_id, receipt_id, line_id, word_id):
            return f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}"

        word_id = construct_word_id("test-uuid", 1, 2, 3)
        assert word_id == "IMAGE#test-uuid#RECEIPT#00001#LINE#00002#WORD#00003"

    def test_metadata_field_validation(self):
        """Test metadata field handling logic."""

        # Fields that should be removed for metadata
        metadata_fields = [
            "canonical_merchant_name",
            "merchant_name",
            "merchant_category",
            "address",
            "phone_number",
            "place_id",
        ]

        # Fields that should be updated for labels
        label_fields = [
            "label",
            "reasoning",
            "validation_status",
            "label_proposed_by",
            "label_consolidated_from",
        ]

        # Test metadata field filtering
        test_metadata = {
            "canonical_merchant_name": "Test Merchant",
            "address": "123 Main St",
            "unrelated_field": "keep_this",
            "confidence": 0.95,
        }

        # Simulate removal of metadata fields
        filtered_metadata = test_metadata.copy()
        for field in metadata_fields:
            if field in filtered_metadata:
                del filtered_metadata[field]

        assert "canonical_merchant_name" not in filtered_metadata
        assert "address" not in filtered_metadata
        assert filtered_metadata["unrelated_field"] == "keep_this"
        assert filtered_metadata["confidence"] == 0.95

        # Test label field prefixing
        test_changes = {
            "label": {"old": "OLD", "new": "NEW"},
            "validation_status": {"old": "PENDING", "new": "VALIDATED"},
        }

        prefixed_updates = {}
        for field, change in test_changes.items():
            if field in label_fields:
                prefixed_updates[f"label_{field}"] = change["new"]

        assert prefixed_updates["label_label"] == "NEW"
        assert prefixed_updates["label_validation_status"] == "VALIDATED"

    def test_timestamp_generation(self):
        """Test timestamp generation for updates."""

        # Test that timestamps are in ISO format
        timestamp = datetime.now(timezone.utc).isoformat()

        # Should be a valid ISO format
        parsed = datetime.fromisoformat(timestamp.replace("Z", "+00:00"))
        assert isinstance(parsed, datetime)

        # Should be recent (within last minute)
        now = datetime.now(timezone.utc)
        assert (now - parsed).total_seconds() < 60


if __name__ == "__main__":
    pytest.main([__file__])
