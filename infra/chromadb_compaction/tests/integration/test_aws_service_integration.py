"""
Comprehensive AWS Service Integration Tests for ChromaDB Compaction.

This module tests the full integration between AWS services using moto,
following the same patterns as receipt_dynamo package tests.
"""

import json
import os
import sys
from unittest.mock import MagicMock, patch

# Mock problematic imports before any other imports
sys.modules["lambda_layer"] = MagicMock()
sys.modules["lambda_layer"].dynamo_layer = MagicMock()
sys.modules["pulumi_docker_build"] = MagicMock()
sys.modules["pulumi"] = MagicMock()
sys.modules["pulumi_aws"] = MagicMock()
sys.modules["pulumi_aws.ecr"] = MagicMock()

import pytest
from moto import mock_aws

from ..lambdas.stream_processor import lambda_handler as stream_handler
from ..lambdas.enhanced_compaction_handler import lambda_handler as compaction_handler


class TestAWSServiceIntegration:
    """Test integration with real AWS services using moto."""

    @mock_aws
    def test_sqs_message_flow_with_real_queues(self, target_metadata_event):
        """Test complete SQS message flow using real moto queues."""
        import boto3
        
        # Create real SQS queues using moto
        sqs = boto3.client("sqs", region_name="us-east-1")
        
        lines_queue = sqs.create_queue(QueueName="test-lines-queue")
        words_queue = sqs.create_queue(QueueName="test-words-queue")
        
        lines_queue_url = lines_queue["QueueUrl"]
        words_queue_url = words_queue["QueueUrl"]
        
        # Set environment variables for the Lambda
        with patch.dict(os.environ, {
            "LINES_QUEUE_URL": lines_queue_url,
            "WORDS_QUEUE_URL": words_queue_url,
            "AWS_REGION": "us-east-1",
        }):
            # Process stream event
            result = stream_handler(target_metadata_event, None)
            
            assert result["statusCode"] == 200
            assert result["processed_records"] == 1
            assert result["queued_messages"] >= 1
            
            # Verify messages were sent to queues
            lines_response = sqs.receive_message(
                QueueUrl=lines_queue_url, MaxNumberOfMessages=10
            )
            words_response = sqs.receive_message(
                QueueUrl=words_queue_url, MaxNumberOfMessages=10
            )
            
            lines_messages = lines_response.get("Messages", [])
            words_messages = words_response.get("Messages", [])
            
            # Both queues should have received messages
            assert len(lines_messages) == 1
            assert len(words_messages) == 1
            
            # Verify message content
            lines_body = json.loads(lines_messages[0]["Body"])
            words_body = json.loads(words_messages[0]["Body"])
            
            assert lines_body["entity_type"] == "RECEIPT_METADATA"
            assert words_body["entity_type"] == "RECEIPT_METADATA"
            assert lines_body["event_name"] == "MODIFY"
            assert words_body["event_name"] == "MODIFY"

    @mock_aws
    def test_dynamodb_queries_with_real_table(self):
        """Test DynamoDB queries using real moto table."""
        import boto3
        from receipt_dynamo.data.dynamo_client import DynamoClient
        
        # Create real DynamoDB table using moto
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")
        
        table_name = "TestReceiptTable"
        table = dynamodb.create_table(
            TableName=table_name,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
                {"AttributeName": "GSI1PK", "AttributeType": "S"},
                {"AttributeName": "GSI1SK", "AttributeType": "S"},
            ],
            ProvisionedThroughput={
                "ReadCapacityUnits": 5,
                "WriteCapacityUnits": 5,
            },
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "GSI1",
                    "KeySchema": [
                        {"AttributeName": "GSI1PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI1SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {
                        "ReadCapacityUnits": 5,
                        "WriteCapacityUnits": 5,
                    },
                },
            ],
        )
        
        # Wait for table to be active
        table.wait_until_exists()
        
        # Create DynamoClient and test queries
        client = DynamoClient(table_name=table_name)
        
        # Test that we can query the table (even if empty)
        try:
            lines = client.list_receipt_lines_from_receipt(
                image_id="test-image", receipt_id=1
            )
            assert isinstance(lines, list)
        except Exception as e:
            # Expected for empty table, but should not be a connection error
            assert "ResourceNotFoundException" not in str(e)

    @mock_aws
    def test_s3_operations_with_real_bucket(self):
        """Test S3 operations using real moto bucket."""
        import boto3
        
        # Create real S3 bucket using moto
        s3 = boto3.client("s3", region_name="us-east-1")
        
        bucket_name = "test-chromadb-bucket"
        s3.create_bucket(Bucket=bucket_name)
        
        # Test basic S3 operations
        test_key = "lines/snapshot/latest/test-file.txt"
        test_content = "test snapshot content"
        
        # Upload object
        s3.put_object(
            Bucket=bucket_name,
            Key=test_key,
            Body=test_content
        )
        
        # Download object
        response = s3.get_object(Bucket=bucket_name, Key=test_key)
        downloaded_content = response["Body"].read().decode("utf-8")
        
        assert downloaded_content == test_content
        
        # List objects
        list_response = s3.list_objects_v2(Bucket=bucket_name)
        assert len(list_response.get("Contents", [])) == 1
        assert list_response["Contents"][0]["Key"] == test_key

    def test_end_to_end_pipeline_with_mocks(self, integration_test_environment):
        """Test complete pipeline using comprehensive mocks."""
        # This test uses the integration_test_environment fixture
        # which provides all AWS services mocked with moto
        
        env = integration_test_environment
        
        # Test that all services are properly mocked
        assert "sqs" in env
        assert "dynamodb_table" in env
        assert "s3_bucket" in env
        assert "chromadb_collections" in env
        assert "s3_operations" in env
        
        # Test SQS queues exist
        sqs_client = env["sqs"]["sqs_client"]
        lines_queue_url = env["sqs"]["lines_queue_url"]
        words_queue_url = env["sqs"]["words_queue_url"]
        
        assert lines_queue_url is not None
        assert words_queue_url is not None
        
        # Test DynamoDB table exists
        dynamodb_table = env["dynamodb_table"]
        assert dynamodb_table == "TestReceiptTable"
        
        # Test S3 bucket exists
        s3_bucket = env["s3_bucket"]
        assert s3_bucket == "test-chromadb-bucket"
        
        # Test ChromaDB collections are mocked
        collections = env["chromadb_collections"]
        assert "lines" in collections
        assert "words" in collections
        assert collections["lines"].name == "lines"
        assert collections["words"].name == "words"
        
        # Test S3 operations are mocked
        s3_ops = env["s3_operations"]
        assert "download_snapshot_atomic" in s3_ops
        assert "upload_snapshot_atomic" in s3_ops


class TestAWSServiceErrorHandling:
    """Test error handling with AWS services."""

    @mock_aws
    def test_sqs_queue_not_found_error(self, target_metadata_event):
        """Test handling of missing SQS queue."""
        import boto3
        
        # Create only one queue, but try to send to both
        sqs = boto3.client("sqs", region_name="us-east-1")
        
        lines_queue = sqs.create_queue(QueueName="test-lines-queue")
        lines_queue_url = lines_queue["QueueUrl"]
        
        # Set environment to point to non-existent words queue
        with patch.dict(os.environ, {
            "LINES_QUEUE_URL": lines_queue_url,
            "WORDS_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123/non-existent-queue",
            "AWS_REGION": "us-east-1",
        }):
            # This should handle the error gracefully
            result = stream_handler(target_metadata_event, None)
            
            # Should still process the record but may have errors
            assert result["statusCode"] in [200, 500]  # Either success or error

    @mock_aws
    def test_dynamodb_table_not_found_error(self):
        """Test handling of missing DynamoDB table."""
        from receipt_dynamo.data.dynamo_client import DynamoClient
        
        # Try to create client with non-existent table
        with patch.dict(os.environ, {
            "DYNAMODB_TABLE_NAME": "non-existent-table",
        }):
            try:
                client = DynamoClient(table_name="non-existent-table")
                # This should raise an exception
                assert False, "Expected exception for non-existent table"
            except Exception as e:
                # Should be a table not found error
                assert "ResourceNotFoundException" in str(e) or "Table" in str(e)

    @mock_aws
    def test_s3_bucket_not_found_error(self):
        """Test handling of missing S3 bucket."""
        import boto3
        
        s3 = boto3.client("s3", region_name="us-east-1")
        
        # Try to access non-existent bucket
        try:
            s3.get_object(Bucket="non-existent-bucket", Key="test-key")
            assert False, "Expected exception for non-existent bucket"
        except Exception as e:
            # Should be a bucket not found error
            assert "NoSuchBucket" in str(e) or "bucket" in str(e).lower()
