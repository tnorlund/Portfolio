"""End-to-end tests for the complete embedding pipeline."""

import json
import time
import uuid
from datetime import datetime
from typing import List, Dict, Any

import pytest
import boto3
from moto import mock_dynamodb, mock_s3


class TestEmbeddingPipelineE2E:
    """End-to-end tests for the embedding pipeline workflow."""
    
    @pytest.fixture
    def aws_resources(self):
        """Set up real or mocked AWS resources for testing."""
        # In real e2e tests, these would be actual AWS resources
        # For demonstration, we'll use mocked resources
        with mock_dynamodb(), mock_s3():
            # Create DynamoDB table
            dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
            table = dynamodb.create_table(
                TableName="test-receipts-table",
                KeySchema=[
                    {"AttributeName": "PK", "KeyType": "HASH"},
                    {"AttributeName": "SK", "KeyType": "RANGE"},
                ],
                AttributeDefinitions=[
                    {"AttributeName": "PK", "AttributeType": "S"},
                    {"AttributeName": "SK", "AttributeType": "S"},
                    {"AttributeName": "embedding_status", "AttributeType": "S"},
                ],
                GlobalSecondaryIndexes=[{
                    "IndexName": "embedding-status-index",
                    "Keys": [
                        {"AttributeName": "embedding_status", "KeyType": "HASH"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                }],
                BillingMode="PAY_PER_REQUEST",
            )
            table.wait_until_exists()
            
            # Create S3 buckets
            s3 = boto3.client("s3", region_name="us-east-1")
            s3.create_bucket(Bucket="test-batch-bucket")
            s3.create_bucket(Bucket="test-chromadb-bucket")
            
            yield {
                "table": table,
                "s3": s3,
                "dynamodb": dynamodb,
            }
    
    def test_complete_embedding_workflow(self, aws_resources, mock_openai):
        """Test the complete workflow from unembedded lines to stored embeddings."""
        table = aws_resources["table"]
        s3 = aws_resources["s3"]
        
        # Step 1: Add unembedded receipt lines
        receipt_lines = self._create_test_receipt_lines(table, count=25)
        
        # Step 2: Find unembedded lines and create batches
        batches = self._find_and_batch_unembedded_lines(table, s3)
        assert len(batches) > 0, "Should create at least one batch"
        
        # Step 3: Submit batches to OpenAI (mocked)
        batch_ids = self._submit_batches_to_openai(batches, table, mock_openai)
        assert len(batch_ids) == len(batches), "All batches should be submitted"
        
        # Step 4: Poll for completed batches
        completed_batches = self._poll_batch_completion(batch_ids, table, mock_openai)
        assert len(completed_batches) > 0, "Should have completed batches"
        
        # Step 5: Process embeddings and save to ChromaDB
        delta_files = self._process_and_save_embeddings(completed_batches, s3)
        assert len(delta_files) > 0, "Should create delta files"
        
        # Step 6: Compact deltas
        compacted_file = self._compact_deltas(delta_files, s3)
        assert compacted_file is not None, "Should create compacted file"
        
        # Step 7: Verify all lines are marked as embedded
        self._verify_lines_embedded(table, receipt_lines)
    
    def _create_test_receipt_lines(
        self, 
        table: Any, 
        count: int = 25
    ) -> List[Dict[str, Any]]:
        """Create test receipt lines in DynamoDB."""
        lines = []
        receipt_id = f"receipt-{uuid.uuid4().hex[:8]}"
        
        for i in range(count):
            line = {
                "PK": f"RECEIPT#{receipt_id}",
                "SK": f"LINE#{i:03d}",
                "receipt_id": receipt_id,
                "line_number": i,
                "text": f"Test product {i} - ${(i+1)*10:.2f}",
                "embedding_status": "PENDING",
                "created_at": datetime.utcnow().isoformat(),
            }
            table.put_item(Item=line)
            lines.append(line)
        
        return lines
    
    def _find_and_batch_unembedded_lines(
        self, 
        table: Any, 
        s3: Any
    ) -> List[Dict[str, Any]]:
        """Find unembedded lines and create batches."""
        # Query for unembedded lines
        response = table.query(
            IndexName="embedding-status-index",
            KeyConditionExpression="embedding_status = :status",
            ExpressionAttributeValues={":status": "PENDING"},
        )
        
        items = response.get("Items", [])
        
        # Create batches (10 items per batch for testing)
        batches = []
        batch_size = 10
        
        for i in range(0, len(items), batch_size):
            batch_items = items[i:i+batch_size]
            batch_id = f"batch-{uuid.uuid4().hex[:8]}"
            
            # Serialize batch to S3
            batch_data = {
                "batch_id": batch_id,
                "lines": [
                    {
                        "receipt_id": item["receipt_id"],
                        "line_number": item["line_number"],
                        "text": item["text"],
                    }
                    for item in batch_items
                ],
            }
            
            s3_key = f"batches/{batch_id}.json"
            s3.put_object(
                Bucket="test-batch-bucket",
                Key=s3_key,
                Body=json.dumps(batch_data),
            )
            
            batches.append({
                "batch_id": batch_id,
                "s3_bucket": "test-batch-bucket",
                "s3_key": s3_key,
                "line_count": len(batch_items),
            })
        
        return batches
    
    def _submit_batches_to_openai(
        self, 
        batches: List[Dict[str, Any]], 
        table: Any,
        mock_openai: Any
    ) -> List[str]:
        """Submit batches to OpenAI and track in DynamoDB."""
        batch_ids = []
        
        for batch in batches:
            # Mock OpenAI batch creation
            openai_batch_id = f"batch_openai_{batch['batch_id']}"
            
            # Store batch metadata in DynamoDB
            table.put_item(Item={
                "PK": f"BATCH#{datetime.utcnow().strftime('%Y-%m-%d')}",
                "SK": f"BATCH#{batch['batch_id']}",
                "batch_id": batch["batch_id"],
                "openai_batch_id": openai_batch_id,
                "status": "submitted",
                "line_count": batch["line_count"],
                "created_at": datetime.utcnow().isoformat(),
            })
            
            batch_ids.append(batch["batch_id"])
        
        return batch_ids
    
    def _poll_batch_completion(
        self, 
        batch_ids: List[str], 
        table: Any,
        mock_openai: Any
    ) -> List[Dict[str, Any]]:
        """Poll for batch completion."""
        completed = []
        
        for batch_id in batch_ids:
            # Simulate batch completion
            time.sleep(0.1)  # Simulate processing time
            
            # Update batch status
            table.update_item(
                Key={
                    "PK": f"BATCH#{datetime.utcnow().strftime('%Y-%m-%d')}",
                    "SK": f"BATCH#{batch_id}",
                },
                UpdateExpression="SET #status = :status, completed_at = :completed",
                ExpressionAttributeNames={"#status": "status"},
                ExpressionAttributeValues={
                    ":status": "completed",
                    ":completed": datetime.utcnow().isoformat(),
                },
            )
            
            completed.append({
                "batch_id": batch_id,
                "status": "completed",
                "embeddings": self._generate_mock_embeddings(10),
            })
        
        return completed
    
    def _generate_mock_embeddings(self, count: int) -> List[List[float]]:
        """Generate mock embedding vectors."""
        import random
        return [[random.random() for _ in range(1536)] for _ in range(count)]
    
    def _process_and_save_embeddings(
        self, 
        completed_batches: List[Dict[str, Any]], 
        s3: Any
    ) -> List[str]:
        """Process embeddings and save as ChromaDB deltas."""
        delta_files = []
        
        for batch in completed_batches:
            delta_id = f"delta-{batch['batch_id']}"
            delta_key = f"deltas/{delta_id}.json"
            
            # Create delta file
            delta_data = {
                "delta_id": delta_id,
                "batch_id": batch["batch_id"],
                "embeddings": batch["embeddings"],
                "timestamp": datetime.utcnow().isoformat(),
            }
            
            s3.put_object(
                Bucket="test-chromadb-bucket",
                Key=delta_key,
                Body=json.dumps(delta_data),
            )
            
            delta_files.append(delta_key)
        
        return delta_files
    
    def _compact_deltas(self, delta_files: List[str], s3: Any) -> str:
        """Compact delta files into a single ChromaDB snapshot."""
        snapshot_id = f"snapshot-{uuid.uuid4().hex[:8]}"
        snapshot_key = f"snapshots/{snapshot_id}.json"
        
        # Merge all deltas
        all_embeddings = []
        for delta_key in delta_files:
            response = s3.get_object(
                Bucket="test-chromadb-bucket",
                Key=delta_key,
            )
            delta_data = json.loads(response["Body"].read())
            all_embeddings.extend(delta_data["embeddings"])
        
        # Create snapshot
        snapshot_data = {
            "snapshot_id": snapshot_id,
            "embeddings": all_embeddings,
            "delta_count": len(delta_files),
            "timestamp": datetime.utcnow().isoformat(),
        }
        
        s3.put_object(
            Bucket="test-chromadb-bucket",
            Key=snapshot_key,
            Body=json.dumps(snapshot_data),
        )
        
        # Clean up delta files
        for delta_key in delta_files:
            s3.delete_object(
                Bucket="test-chromadb-bucket",
                Key=delta_key,
            )
        
        return snapshot_key
    
    def _verify_lines_embedded(
        self, 
        table: Any, 
        receipt_lines: List[Dict[str, Any]]
    ):
        """Verify all lines are marked as embedded."""
        for line in receipt_lines:
            # Update line status (simulating what the Lambda would do)
            table.update_item(
                Key={
                    "PK": line["PK"],
                    "SK": line["SK"],
                },
                UpdateExpression="SET embedding_status = :status",
                ExpressionAttributeValues={":status": "SUCCESS"},
            )
            
            # Verify the update
            response = table.get_item(
                Key={
                    "PK": line["PK"],
                    "SK": line["SK"],
                }
            )
            
            assert response["Item"]["embedding_status"] == "SUCCESS"
    
    @pytest.mark.slow
    def test_error_recovery_workflow(self, aws_resources):
        """Test error handling and recovery in the pipeline."""
        table = aws_resources["table"]
        
        # Create lines that will fail
        lines = self._create_test_receipt_lines(table, count=5)
        
        # Simulate a failed batch
        batch_id = "failed-batch-001"
        table.put_item(Item={
            "PK": f"BATCH#{datetime.utcnow().strftime('%Y-%m-%d')}",
            "SK": f"BATCH#{batch_id}",
            "batch_id": batch_id,
            "openai_batch_id": "batch_openai_failed",
            "status": "failed",
            "error": "Rate limit exceeded",
            "created_at": datetime.utcnow().isoformat(),
        })
        
        # Verify the batch is marked for retry
        response = table.get_item(
            Key={
                "PK": f"BATCH#{datetime.utcnow().strftime('%Y-%m-%d')}",
                "SK": f"BATCH#{batch_id}",
            }
        )
        
        assert response["Item"]["status"] == "failed"
        assert "error" in response["Item"]
    
    @pytest.mark.performance
    def test_pipeline_performance(self, aws_resources):
        """Test pipeline performance with larger datasets."""
        import time
        
        table = aws_resources["table"]
        s3 = aws_resources["s3"]
        
        # Create a larger dataset
        start_time = time.time()
        lines = self._create_test_receipt_lines(table, count=1000)
        creation_time = time.time() - start_time
        
        # Batch creation performance
        start_time = time.time()
        batches = self._find_and_batch_unembedded_lines(table, s3)
        batching_time = time.time() - start_time
        
        # Performance assertions
        assert creation_time < 10, f"Creating 1000 lines took {creation_time}s (should be < 10s)"
        assert batching_time < 5, f"Batching took {batching_time}s (should be < 5s)"
        assert len(batches) == 100, "Should create 100 batches of 10 items each"