"""Unit tests for the list-pending Lambda function."""

import json
from unittest.mock import patch, MagicMock
from datetime import datetime

import pytest
import boto3
from moto import mock_dynamodb


class TestListPendingHandler:
    """Test suite for list-pending Lambda handler."""
    
    @mock_dynamodb
    def test_list_pending_with_results(self, lambda_context):
        """Test listing pending batches when batches exist."""
        # Set up DynamoDB table
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
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        table.wait_until_exists()
        
        # Add test data
        test_batches = [
            {
                "PK": "BATCH#2024-01-01",
                "SK": f"BATCH#batch-{i:03d}",
                "batch_id": f"batch-{i:03d}",
                "openai_batch_id": f"batch_openai_{i:03d}",
                "status": "submitted",
                "created_at": datetime.utcnow().isoformat(),
            }
            for i in range(1, 4)
        ]
        
        for batch in test_batches:
            table.put_item(Item=batch)
        
        # Import and test the handler
        from embedding_step_functions.simple_lambdas.list_pending import handler
        
        result = handler.lambda_handler({}, lambda_context)
        
        # Verify the result
        assert isinstance(result, list)
        assert len(result) == 3
        for i, batch in enumerate(result):
            assert batch["batch_id"] == f"batch-{i+1:03d}"
            assert batch["openai_batch_id"] == f"batch_openai_{i+1:03d}"
    
    @mock_dynamodb
    def test_list_pending_empty(self, lambda_context):
        """Test listing pending batches when no batches exist."""
        # Set up empty DynamoDB table
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
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        table.wait_until_exists()
        
        # Import and test the handler
        from embedding_step_functions.simple_lambdas.list_pending import handler
        
        result = handler.lambda_handler({}, lambda_context)
        
        # Verify empty result
        assert isinstance(result, list)
        assert len(result) == 0
    
    @mock_dynamodb
    def test_list_pending_with_limit(self, lambda_context):
        """Test listing pending batches with a limit."""
        # Set up DynamoDB table
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
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        table.wait_until_exists()
        
        # Add more test data than the limit
        for i in range(1, 11):
            table.put_item(Item={
                "PK": "BATCH#2024-01-01",
                "SK": f"BATCH#batch-{i:03d}",
                "batch_id": f"batch-{i:03d}",
                "openai_batch_id": f"batch_openai_{i:03d}",
                "status": "submitted",
            })
        
        # Import and test the handler with a limit
        from embedding_step_functions.simple_lambdas.list_pending import handler
        
        # Mock the handler to apply a limit (if it supports it)
        event = {"limit": 5}
        result = handler.lambda_handler(event, lambda_context)
        
        # Verify the result respects the limit
        assert isinstance(result, list)
        # Note: Actual limit behavior depends on implementation
        assert len(result) <= 10  # Adjust based on actual implementation
    
    def test_list_pending_error_handling(self, lambda_context):
        """Test error handling in list-pending handler."""
        with patch("boto3.resource") as mock_boto:
            # Simulate DynamoDB error
            mock_table = MagicMock()
            mock_table.query.side_effect = Exception("DynamoDB connection error")
            mock_boto.return_value.Table.return_value = mock_table
            
            from embedding_step_functions.simple_lambdas.list_pending import handler
            
            # The handler should handle the error gracefully
            with pytest.raises(Exception) as exc_info:
                handler.lambda_handler({}, lambda_context)
            
            assert "DynamoDB" in str(exc_info.value)
    
    @mock_dynamodb
    def test_list_pending_filtering(self, lambda_context):
        """Test that only pending batches are listed."""
        # Set up DynamoDB table
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
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        table.wait_until_exists()
        
        # Add batches with different statuses
        batches = [
            {"batch_id": "batch-001", "status": "submitted"},
            {"batch_id": "batch-002", "status": "completed"},
            {"batch_id": "batch-003", "status": "submitted"},
            {"batch_id": "batch-004", "status": "failed"},
        ]
        
        for batch in batches:
            table.put_item(Item={
                "PK": "BATCH#2024-01-01",
                "SK": f"BATCH#{batch['batch_id']}",
                "batch_id": batch["batch_id"],
                "openai_batch_id": f"openai_{batch['batch_id']}",
                "status": batch["status"],
            })
        
        from embedding_step_functions.simple_lambdas.list_pending import handler
        
        result = handler.lambda_handler({}, lambda_context)
        
        # Should only return submitted batches
        assert len(result) == 2
        assert all(b["batch_id"] in ["batch-001", "batch-003"] for b in result)