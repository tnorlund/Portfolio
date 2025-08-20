"""
Integration tests for Enhanced ChromaDB Compaction Handler using Moto

Tests the complete flow with mocked AWS services (DynamoDB, S3, SQS)
to validate end-to-end functionality without real infrastructure.
"""

import json
import os
import tempfile
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import boto3
import pytest
from moto import mock_aws


@pytest.fixture
def aws_environment():
    """Create mocked AWS environment with DynamoDB, S3, and SQS."""
    with mock_aws():
        # Setup environment variables
        os.environ.update({
            "DYNAMODB_TABLE_NAME": "test-table",
            "CHROMADB_BUCKET": "test-chromadb-bucket",
            "COMPACTION_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123456789012/test-queue",
            "HEARTBEAT_INTERVAL_SECONDS": "60",
            "LOCK_DURATION_MINUTES": "15"
        })
        
        # Create DynamoDB table
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        table = dynamodb.create_table(
            TableName="test-table",
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST"
        )
        table.wait_until_exists()
        
        # Create S3 bucket
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="test-chromadb-bucket")
        
        # Create SQS queue
        sqs = boto3.client("sqs", region_name="us-east-1")
        queue_response = sqs.create_queue(QueueName="test-queue")
        
        yield {
            "dynamodb": dynamodb,
            "s3": s3,
            "sqs": sqs,
            "table": table,
            "queue_url": queue_response["QueueUrl"]
        }


@pytest.fixture
def mock_chromadb_data():
    """Create mock ChromaDB data structure."""
    return {
        "lines_collection": {
            "ids": [
                "IMAGE#test-uuid#RECEIPT#00001#LINE#00001",
                "IMAGE#test-uuid#RECEIPT#00001#LINE#00002",
                "IMAGE#other-uuid#RECEIPT#00002#LINE#00001",
            ],
            "metadatas": [
                {
                    "text": "Store Name",
                    "confidence": 0.95,
                    "canonical_merchant_name": "Old Merchant",
                    "address": "123 Main St"
                },
                {
                    "text": "Total: $19.99",
                    "confidence": 0.92,
                    "canonical_merchant_name": "Old Merchant",
                    "address": "123 Main St"
                },
                {
                    "text": "Different Store",
                    "confidence": 0.88,
                    "canonical_merchant_name": "Other Merchant",
                    "address": "456 Oak Ave"
                }
            ]
        },
        "words_collection": {
            "ids": [
                "IMAGE#test-uuid#RECEIPT#00001#LINE#00001#WORD#00001",
                "IMAGE#test-uuid#RECEIPT#00001#LINE#00001#WORD#00002",
                "IMAGE#test-uuid#RECEIPT#00001#LINE#00002#WORD#00001",
            ],
            "metadatas": [
                {
                    "text": "Store",
                    "confidence": 0.95,
                    "canonical_merchant_name": "Old Merchant",
                    "label_label": "STORE_NAME",
                    "label_validation_status": "PENDING"
                },
                {
                    "text": "Name", 
                    "confidence": 0.94,
                    "canonical_merchant_name": "Old Merchant",
                    "label_label": "STORE_NAME",
                    "label_validation_status": "PENDING"
                },
                {
                    "text": "$19.99",
                    "confidence": 0.92,
                    "canonical_merchant_name": "Old Merchant",
                    "label_label": "TOTAL",
                    "label_validation_status": "VALIDATED"
                }
            ]
        }
    }


class TestEnhancedCompactionIntegration:
    """Integration tests for enhanced compaction with real AWS service mocks."""
    
    @patch('enhanced_compaction_handler.chromadb')
    @patch('enhanced_compaction_handler.tempfile.mkdtemp')
    @patch('enhanced_compaction_handler.shutil.rmtree')
    def test_metadata_update_end_to_end(self, mock_rmtree, mock_mkdtemp, mock_chromadb, 
                                       aws_environment, mock_chromadb_data):
        """Test complete metadata update flow from SQS message to ChromaDB update."""
        
        # Import after mocking to avoid initialization issues
        from enhanced_compaction_handler import handle
        
        # Setup mocks
        mock_mkdtemp.return_value = "/tmp/test"
        mock_collection = MagicMock()
        mock_client = MagicMock()
        mock_client.get_collection.return_value = mock_collection
        mock_chromadb.PersistentClient.return_value = mock_client
        
        # Setup collection data
        mock_collection.get.return_value = mock_chromadb_data["lines_collection"]
        
        # Create SQS event with metadata update
        sqs_event = {
            "Records": [
                {
                    "body": json.dumps({
                        "source": "dynamodb_stream",
                        "entity_type": "RECEIPT_METADATA",
                        "entity_data": {
                            "image_id": "test-uuid",
                            "receipt_id": 1
                        },
                        "changes": {
                            "canonical_merchant_name": {
                                "old": "Old Merchant",
                                "new": "New Merchant Name"
                            },
                            "address": {
                                "old": "123 Main St",
                                "new": "456 New Street"
                            }
                        },
                        "event_name": "MODIFY",
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }),
                    "messageAttributes": {
                        "source": {"stringValue": "dynamodb_stream"},
                        "entity_type": {"stringValue": "RECEIPT_METADATA"},
                        "event_name": {"stringValue": "MODIFY"}
                    }
                }
            ]
        }
        
        # Execute the handler
        result = handle(sqs_event, None)
        
        # Verify successful processing
        assert result["statusCode"] == 200
        assert result["stream_messages"] == 1
        assert result["delta_messages"] == 0
        
        # Verify ChromaDB collection was updated
        mock_collection.update.assert_called()
        update_call = mock_collection.update.call_args[1]
        
        # Should update 2 records (both lines for receipt 1)
        assert len(update_call["ids"]) == 2
        assert len(update_call["metadatas"]) == 2
        
        # Verify metadata changes
        for metadata in update_call["metadatas"]:
            assert metadata["canonical_merchant_name"] == "New Merchant Name"
            assert metadata["address"] == "456 New Street"
            assert "last_metadata_update" in metadata
    
    @patch('enhanced_compaction_handler.chromadb')
    @patch('enhanced_compaction_handler.tempfile.mkdtemp')
    @patch('enhanced_compaction_handler.shutil.rmtree')
    def test_label_update_end_to_end(self, mock_rmtree, mock_mkdtemp, mock_chromadb,
                                    aws_environment, mock_chromadb_data):
        """Test complete label update flow from SQS message to ChromaDB update."""
        
        from enhanced_compaction_handler import handle
        
        # Setup mocks
        mock_mkdtemp.return_value = "/tmp/test"
        mock_collection = MagicMock()
        mock_client = MagicMock()
        mock_client.get_collection.return_value = mock_collection
        mock_chromadb.PersistentClient.return_value = mock_client
        
        # Setup collection to return specific word
        target_word_id = "IMAGE#test-uuid#RECEIPT#00001#LINE#00001#WORD#00001"
        mock_collection.get.return_value = {
            "ids": [target_word_id],
            "metadatas": [mock_chromadb_data["words_collection"]["metadatas"][0]]
        }
        
        # Create SQS event with label update
        sqs_event = {
            "Records": [
                {
                    "body": json.dumps({
                        "source": "dynamodb_stream",
                        "entity_type": "RECEIPT_WORD_LABEL",
                        "entity_data": {
                            "image_id": "test-uuid",
                            "receipt_id": 1,
                            "line_id": 1,
                            "word_id": 1,
                            "label": "STORE_NAME"
                        },
                        "changes": {
                            "validation_status": {
                                "old": "PENDING",
                                "new": "VALIDATED"
                            },
                            "reasoning": {
                                "old": None,
                                "new": "Clearly a store name"
                            }
                        },
                        "event_name": "MODIFY",
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }),
                    "messageAttributes": {
                        "source": {"stringValue": "dynamodb_stream"},
                        "entity_type": {"stringValue": "RECEIPT_WORD_LABEL"},
                        "event_name": {"stringValue": "MODIFY"}
                    }
                }
            ]
        }
        
        # Execute the handler
        result = handle(sqs_event, None)
        
        # Verify successful processing
        assert result["statusCode"] == 200
        assert result["stream_messages"] == 1
        
        # Verify ChromaDB collection was updated
        mock_collection.update.assert_called()
        update_call = mock_collection.update.call_args[1]
        
        # Should update 1 record (specific word)
        assert len(update_call["ids"]) == 1
        assert update_call["ids"][0] == target_word_id
        
        # Verify label changes
        metadata = update_call["metadatas"][0]
        assert metadata["label_validation_status"] == "VALIDATED"
        assert metadata["label_reasoning"] == "Clearly a store name"
        assert "last_label_update" in metadata
    
    @patch('enhanced_compaction_handler.chromadb')
    @patch('enhanced_compaction_handler.tempfile.mkdtemp')
    @patch('enhanced_compaction_handler.shutil.rmtree')
    def test_remove_metadata_end_to_end(self, mock_rmtree, mock_mkdtemp, mock_chromadb,
                                       aws_environment, mock_chromadb_data):
        """Test complete metadata removal flow."""
        
        from enhanced_compaction_handler import handle
        
        # Setup mocks
        mock_mkdtemp.return_value = "/tmp/test"
        mock_collection = MagicMock()
        mock_client = MagicMock()
        mock_client.get_collection.return_value = mock_collection
        mock_chromadb.PersistentClient.return_value = mock_client
        
        # Setup collection data
        mock_collection.get.return_value = mock_chromadb_data["lines_collection"]
        
        # Create SQS event with metadata removal
        sqs_event = {
            "Records": [
                {
                    "body": json.dumps({
                        "source": "dynamodb_stream",
                        "entity_type": "RECEIPT_METADATA",
                        "entity_data": {
                            "image_id": "test-uuid",
                            "receipt_id": 1
                        },
                        "changes": {},  # No specific changes for REMOVE
                        "event_name": "REMOVE",
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }),
                    "messageAttributes": {
                        "source": {"stringValue": "dynamodb_stream"},
                        "entity_type": {"stringValue": "RECEIPT_METADATA"},
                        "event_name": {"stringValue": "REMOVE"}
                    }
                }
            ]
        }
        
        # Execute the handler
        result = handle(sqs_event, None)
        
        # Verify successful processing
        assert result["statusCode"] == 200
        
        # Verify ChromaDB collection was updated
        mock_collection.update.assert_called()
        update_call = mock_collection.update.call_args[1]
        
        # Should update 2 records (both lines for receipt 1)
        assert len(update_call["ids"]) == 2
        
        # Verify metadata fields were removed
        for metadata in update_call["metadatas"]:
            # Merchant fields should be removed
            assert "canonical_merchant_name" not in metadata
            assert "address" not in metadata
            # Other fields should remain
            assert "text" in metadata
            assert "confidence" in metadata
            assert "metadata_removed_at" in metadata
    
    @patch('enhanced_compaction_handler.chromadb')
    @patch('enhanced_compaction_handler.tempfile.mkdtemp')
    @patch('enhanced_compaction_handler.shutil.rmtree')
    def test_remove_labels_end_to_end(self, mock_rmtree, mock_mkdtemp, mock_chromadb,
                                     aws_environment, mock_chromadb_data):
        """Test complete label removal flow."""
        
        from enhanced_compaction_handler import handle
        
        # Setup mocks
        mock_mkdtemp.return_value = "/tmp/test"
        mock_collection = MagicMock()
        mock_client = MagicMock()
        mock_client.get_collection.return_value = mock_collection
        mock_chromadb.PersistentClient.return_value = mock_client
        
        # Setup collection to return specific word
        target_word_id = "IMAGE#test-uuid#RECEIPT#00001#LINE#00001#WORD#00001"
        mock_collection.get.return_value = {
            "ids": [target_word_id],
            "metadatas": [mock_chromadb_data["words_collection"]["metadatas"][0]]
        }
        
        # Create SQS event with label removal
        sqs_event = {
            "Records": [
                {
                    "body": json.dumps({
                        "source": "dynamodb_stream",
                        "entity_type": "RECEIPT_WORD_LABEL",
                        "entity_data": {
                            "image_id": "test-uuid",
                            "receipt_id": 1,
                            "line_id": 1,
                            "word_id": 1,
                            "label": "STORE_NAME"
                        },
                        "changes": {},  # No specific changes for REMOVE
                        "event_name": "REMOVE",
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }),
                    "messageAttributes": {
                        "source": {"stringValue": "dynamodb_stream"},
                        "entity_type": {"stringValue": "RECEIPT_WORD_LABEL"},
                        "event_name": {"stringValue": "REMOVE"}
                    }
                }
            ]
        }
        
        # Execute the handler
        result = handle(sqs_event, None)
        
        # Verify successful processing
        assert result["statusCode"] == 200
        
        # Verify ChromaDB collection was updated
        mock_collection.update.assert_called()
        update_call = mock_collection.update.call_args[1]
        
        # Should update 1 record (specific word)
        assert len(update_call["ids"]) == 1
        assert update_call["ids"][0] == target_word_id
        
        # Verify label fields were removed
        metadata = update_call["metadatas"][0]
        assert "label_label" not in metadata
        assert "label_validation_status" not in metadata
        # Other fields should remain
        assert "text" in metadata
        assert "confidence" in metadata
        assert "labels_removed_at" in metadata
    
    def test_mixed_message_processing(self, aws_environment):
        """Test processing mixed stream and delta messages."""
        
        # Import after AWS setup
        from enhanced_compaction_handler import handle
        
        # Create SQS event with mixed messages
        sqs_event = {
            "Records": [
                {
                    "body": json.dumps({
                        "source": "dynamodb_stream",
                        "entity_type": "RECEIPT_METADATA",
                        "entity_data": {"image_id": "test-uuid", "receipt_id": 1},
                        "changes": {},
                        "event_name": "MODIFY"
                    }),
                    "messageAttributes": {
                        "source": {"stringValue": "dynamodb_stream"}
                    }
                },
                {
                    "body": json.dumps({
                        "delta_key": "delta/abc123/",
                        "embedding_count": 100
                    }),
                    "messageAttributes": {}  # No source = delta message
                }
            ]
        }
        
        with patch('enhanced_compaction_handler.process_stream_messages') as mock_stream:
            with patch('enhanced_compaction_handler.process_delta_messages') as mock_delta:
                mock_stream.return_value = {"statusCode": 200}
                mock_delta.return_value = {"statusCode": 200}
                
                result = handle(sqs_event, None)
                
                # Verify both processors were called
                assert result["statusCode"] == 200
                assert result["stream_messages"] == 1
                assert result["delta_messages"] == 1
                mock_stream.assert_called_once()
                mock_delta.assert_called_once()
    
    def test_lock_contention_handling(self, aws_environment):
        """Test behavior when lock cannot be acquired."""
        
        from enhanced_compaction_handler import handle
        
        # Create SQS event
        sqs_event = {
            "Records": [
                {
                    "body": json.dumps({
                        "source": "dynamodb_stream",
                        "entity_type": "RECEIPT_METADATA",
                        "entity_data": {"image_id": "test-uuid", "receipt_id": 1},
                        "changes": {"canonical_merchant_name": {"old": "Old", "new": "New"}},
                        "event_name": "MODIFY"
                    }),
                    "messageAttributes": {
                        "source": {"stringValue": "dynamodb_stream"}
                    }
                }
            ]
        }
        
        # Mock lock manager to fail acquisition
        with patch('enhanced_compaction_handler.LockManager') as mock_lock_class:
            mock_lock_manager = MagicMock()
            mock_lock_manager.acquire.return_value = False  # Lock acquisition fails
            mock_lock_class.return_value = mock_lock_manager
            
            result = handle(sqs_event, None)
            
            # SQS processing should still return 200 (overall success)
            # but stream processing should fail internally
            assert result["statusCode"] == 200
            assert result["stream_messages"] == 1
            
            # Lock manager should still try to release
            mock_lock_manager.stop_heartbeat.assert_called_once()
            mock_lock_manager.release.assert_called_once()
    
    def test_s3_operations_integration(self, aws_environment):
        """Test S3 download/upload operations work with mocked S3."""
        
        from enhanced_compaction_handler import download_from_s3, upload_to_s3
        
        s3_client = aws_environment["s3"]
        bucket = "test-chromadb-bucket"
        
        # Create test data in S3
        test_content = b"test ChromaDB data"
        s3_client.put_object(
            Bucket=bucket,
            Key="lines/snapshot/latest/chroma.sqlite3",
            Body=test_content
        )
        
        # Test download
        with tempfile.TemporaryDirectory() as temp_dir:
            download_from_s3(bucket, "lines/snapshot/latest/", temp_dir)
            
            # Verify file was downloaded
            downloaded_file = os.path.join(temp_dir, "chroma.sqlite3")
            assert os.path.exists(downloaded_file)
            
            with open(downloaded_file, "rb") as f:
                assert f.read() == test_content
            
            # Test upload
            new_content = b"updated ChromaDB data"
            with open(downloaded_file, "wb") as f:
                f.write(new_content)
            
            upload_to_s3(temp_dir, bucket, "lines/snapshot/updated/")
            
            # Verify file was uploaded
            response = s3_client.get_object(
                Bucket=bucket,
                Key="lines/snapshot/updated/chroma.sqlite3"
            )
            assert response["Body"].read() == new_content
    
    def test_error_handling_integration(self, aws_environment):
        """Test error handling with malformed messages."""
        
        from enhanced_compaction_handler import handle
        
        # Create SQS event with malformed message
        sqs_event = {
            "Records": [
                {
                    "body": "invalid json",
                    "messageAttributes": {}
                },
                {
                    "body": json.dumps({
                        "source": "dynamodb_stream",
                        "entity_type": "UNKNOWN_TYPE",  # Invalid entity type
                        "entity_data": {},
                        "changes": {},
                        "event_name": "MODIFY"
                    }),
                    "messageAttributes": {
                        "source": {"stringValue": "dynamodb_stream"}
                    }
                }
            ]
        }
        
        with patch('enhanced_compaction_handler.LockManager') as mock_lock_class:
            mock_lock_manager = MagicMock()
            mock_lock_manager.acquire.return_value = True
            mock_lock_class.return_value = mock_lock_manager
            
            result = handle(sqs_event, None)
            
            # Should still process successfully (graceful error handling)
            assert result["statusCode"] == 200
            assert result["processed_messages"] == 1  # Only valid message counted
            assert result["stream_messages"] == 1  # Unknown type still processed as stream


if __name__ == '__main__':
    pytest.main([__file__])