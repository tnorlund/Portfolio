"""pytest configuration and fixtures for Lambda function testing."""

import os
import json
import tempfile
from pathlib import Path
from typing import Generator, Dict, Any
from unittest.mock import patch, MagicMock

import pytest
import boto3
from moto import mock_dynamodb, mock_s3, mock_sqs
from localstack_client.session import Session


# --- Environment Setup ---
@pytest.fixture(autouse=True)
def setup_test_env():
    """Set up test environment variables."""
    test_env = {
        "DYNAMODB_TABLE_NAME": "test-receipts-table",
        "S3_BUCKET": "test-batch-bucket",
        "CHROMADB_BUCKET": "test-chromadb-bucket",
        "OPENAI_API_KEY": "test-api-key",
        "COMPACTION_QUEUE_URL": "https://sqs.us-east-1.amazonaws.com/123456789/test-queue",
        "HANDLER_TYPE": "test_handler",
        "CHROMA_PERSIST_DIRECTORY": "/tmp/test-chroma",
    }
    
    with patch.dict(os.environ, test_env):
        yield


# --- LocalStack Fixtures ---
@pytest.fixture(scope="session")
def localstack_session():
    """Create a LocalStack session for integration tests."""
    return Session()


@pytest.fixture
def localstack_client(localstack_session):
    """Get LocalStack boto3 clients."""
    def _get_client(service_name: str):
        return localstack_session.boto3_session.client(
            service_name,
            endpoint_url="http://localhost:4566",
            region_name="us-east-1",
            aws_access_key_id="test",
            aws_secret_access_key="test",
        )
    return _get_client


# --- AWS Service Mocks ---
@pytest.fixture
def mock_dynamodb_table():
    """Create a mock DynamoDB table."""
    with mock_dynamodb():
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
                {"AttributeName": "batch_id", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[
                {
                    "IndexName": "embedding-status-index",
                    "Keys": [
                        {"AttributeName": "embedding_status", "KeyType": "HASH"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
                {
                    "IndexName": "batch-index",
                    "Keys": [
                        {"AttributeName": "batch_id", "KeyType": "HASH"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                },
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        
        # Wait for table to be created
        table.wait_until_exists()
        yield table


@pytest.fixture
def mock_s3_bucket():
    """Create mock S3 buckets."""
    with mock_s3():
        s3 = boto3.client("s3", region_name="us-east-1")
        s3.create_bucket(Bucket="test-batch-bucket")
        s3.create_bucket(Bucket="test-chromadb-bucket")
        yield s3


@pytest.fixture
def mock_sqs_queue():
    """Create a mock SQS queue."""
    with mock_sqs():
        sqs = boto3.client("sqs", region_name="us-east-1")
        queue_url = sqs.create_queue(QueueName="test-compaction-queue")["QueueUrl"]
        yield sqs, queue_url


# --- OpenAI Mock ---
@pytest.fixture
def mock_openai():
    """Mock OpenAI API responses."""
    with patch("openai.OpenAI") as mock_class:
        mock_client = MagicMock()
        mock_class.return_value = mock_client
        
        # Mock batch creation
        mock_client.batches.create.return_value = MagicMock(
            id="batch_test_123",
            status="validating",
            input_file_id="file-input-456",
        )
        
        # Mock batch retrieval
        mock_client.batches.retrieve.return_value = MagicMock(
            id="batch_test_123",
            status="completed",
            output_file_id="file-output-789",
            completed_at=1234567890,
        )
        
        # Mock file operations
        mock_client.files.create.return_value = MagicMock(
            id="file-input-456"
        )
        
        mock_client.files.content.return_value = MagicMock(
            read=lambda: b'{"custom_id":"1","response":{"body":{"data":[{"embedding":[0.1,0.2,0.3]}]}}}\n'
        )
        
        yield mock_client


# --- Sample Data Fixtures ---
@pytest.fixture
def sample_receipt_lines():
    """Generate sample receipt line data."""
    return [
        {
            "PK": f"RECEIPT#receipt-{i:03d}",
            "SK": f"LINE#{i:03d}",
            "receipt_id": f"receipt-{i:03d}",
            "line_number": i,
            "text": f"Sample product {i}",
            "embedding_status": "PENDING",
        }
        for i in range(1, 11)
    ]


@pytest.fixture
def sample_batch_data():
    """Generate sample batch data."""
    return {
        "batch_id": "test-batch-001",
        "openai_batch_id": "batch_test_123",
        "status": "submitted",
        "created_at": "2024-01-01T00:00:00Z",
        "line_count": 100,
    }


@pytest.fixture
def sample_embeddings():
    """Generate sample embedding vectors."""
    import numpy as np
    
    return {
        "embeddings": np.random.rand(10, 1536).tolist(),  # 10 embeddings of dimension 1536
        "metadata": [
            {"receipt_id": f"receipt-{i:03d}", "line_number": i}
            for i in range(1, 11)
        ],
    }


# --- Lambda Context Mock ---
@pytest.fixture
def lambda_context():
    """Create a mock Lambda context."""
    context = MagicMock()
    context.function_name = "test-function"
    context.function_version = "$LATEST"
    context.invoked_function_arn = "arn:aws:lambda:us-east-1:123456789:function:test-function"
    context.memory_limit_in_mb = "128"
    context.aws_request_id = "test-request-id"
    context.log_group_name = "/aws/lambda/test-function"
    context.log_stream_name = "2024/01/01/[$LATEST]test-stream"
    context.get_remaining_time_in_millis = lambda: 30000
    return context


# --- Test Helpers ---
@pytest.fixture
def import_lambda_handler():
    """Helper to import Lambda handlers from the simple_lambdas directory."""
    def _import_handler(handler_name: str):
        import sys
        import importlib
        
        handler_path = Path(__file__).parent.parent / "embedding_step_functions" / "simple_lambdas" / handler_name
        sys.path.insert(0, str(handler_path))
        
        try:
            handler_module = importlib.import_module("handler")
            importlib.reload(handler_module)  # Ensure fresh import
            return handler_module
        finally:
            sys.path.remove(str(handler_path))
    
    return _import_handler


# --- ChromaDB Mock ---
@pytest.fixture
def mock_chromadb():
    """Mock ChromaDB client for testing."""
    with patch("chromadb.PersistentClient") as mock_client_class:
        mock_client = MagicMock()
        mock_client_class.return_value = mock_client
        
        # Mock collection
        mock_collection = MagicMock()
        mock_client.get_or_create_collection.return_value = mock_collection
        
        # Mock collection methods
        mock_collection.add.return_value = None
        mock_collection.query.return_value = {
            "ids": [["id1", "id2"]],
            "embeddings": None,
            "documents": [["doc1", "doc2"]],
            "metadatas": [[{"key": "value1"}, {"key": "value2"}]],
            "distances": [[0.1, 0.2]],
        }
        
        yield mock_client, mock_collection


# --- Temporary Directory ---
@pytest.fixture
def temp_dir():
    """Create a temporary directory for tests."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


# --- Step Functions Mock ---
@pytest.fixture
def mock_step_functions():
    """Mock AWS Step Functions for testing."""
    with patch("boto3.client") as mock_boto_client:
        mock_sf_client = MagicMock()
        
        def _get_client(service_name, **kwargs):
            if service_name == "stepfunctions":
                return mock_sf_client
            return MagicMock()
        
        mock_boto_client.side_effect = _get_client
        
        # Mock Step Functions methods
        mock_sf_client.start_execution.return_value = {
            "executionArn": "arn:aws:states:us-east-1:123456789:execution:test-sf:test-exec",
            "startDate": "2024-01-01T00:00:00Z",
        }
        
        mock_sf_client.describe_execution.return_value = {
            "status": "SUCCEEDED",
            "output": json.dumps({"result": "success"}),
        }
        
        yield mock_sf_client