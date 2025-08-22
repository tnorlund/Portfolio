"""Test configuration and fixtures for ChromaDB compaction tests."""

import os
import sys
from unittest.mock import MagicMock, patch, Mock
import pytest
import boto3
from moto import mock_aws
from .test_data import (
    TARGET_METADATA_UPDATE_EVENT,
    get_target_event_variation,
)

# Set environment variables to indicate we're in test mode
os.environ["PYTEST_RUNNING"] = "1"

# Set required environment variables for Lambda handlers
os.environ["DYNAMODB_TABLE_NAME"] = "test-table"
os.environ["CHROMADB_BUCKET"] = "test-bucket"
os.environ["COMPACTION_QUEUE_URL"] = "https://sqs.us-east-1.amazonaws.com/123456789012/test-queue"

# Prevent lambda layer building during tests
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '../..'))
import lambda_layer
lambda_layer.SKIP_LAYER_BUILDING = True

# Mock the lambda_layer and pulumi modules to avoid import errors during testing
sys.modules["lambda_layer"] = MagicMock()
sys.modules["lambda_layer"].dynamo_layer = MagicMock()
sys.modules["pulumi_docker_build"] = MagicMock()
sys.modules["pulumi"] = MagicMock()
sys.modules["pulumi_aws"] = MagicMock()
sys.modules["pulumi_aws.ecr"] = MagicMock()

# Mock DynamoClient globally for all tests
from unittest.mock import patch
dynamo_client_patcher = patch('receipt_dynamo.data.dynamo_client.DynamoClient')
mock_dynamo_client = dynamo_client_patcher.start()
mock_dynamo_client.return_value = MagicMock()


@pytest.fixture
def target_metadata_event():
    """Fixture providing the specific Target metadata update event."""
    return TARGET_METADATA_UPDATE_EVENT


@pytest.fixture
def target_event_factory():
    """Fixture providing a factory for Target event variations."""
    return get_target_event_variation


@pytest.fixture
def mock_sqs_queues():
    """Fixture that creates mocked SQS queues for integration testing."""
    with mock_aws():
        # Create SQS client
        sqs = boto3.client("sqs", region_name="us-east-1")

        # Create the queues
        lines_response = sqs.create_queue(
            QueueName="chromadb-lines-queue",
            Attributes={
                "VisibilityTimeoutSeconds": "300",
                "MessageRetentionPeriod": "1209600",  # 14 days
            },
        )
        words_response = sqs.create_queue(
            QueueName="chromadb-words-queue",
            Attributes={
                "VisibilityTimeoutSeconds": "300",
                "MessageRetentionPeriod": "1209600",  # 14 days
            },
        )

        # Set environment variables for the Lambda function
        lines_queue_url = lines_response["QueueUrl"]
        words_queue_url = words_response["QueueUrl"]

        # Store original env vars to restore later
        original_lines = os.environ.get("LINES_QUEUE_URL")
        original_words = os.environ.get("WORDS_QUEUE_URL")

        os.environ["LINES_QUEUE_URL"] = lines_queue_url
        os.environ["WORDS_QUEUE_URL"] = words_queue_url

        yield {
            "sqs_client": sqs,
            "lines_queue_url": lines_queue_url,
            "words_queue_url": words_queue_url,
        }

        # Restore original environment variables
        if original_lines is not None:
            os.environ["LINES_QUEUE_URL"] = original_lines
        else:
            os.environ.pop("LINES_QUEUE_URL", None)

        if original_words is not None:
            os.environ["WORDS_QUEUE_URL"] = original_words
        else:
            os.environ.pop("WORDS_QUEUE_URL", None)


@pytest.fixture
def mock_chromadb_collections():
    """Fixture that creates mocked ChromaDB collections for integration testing."""
    
    # Create mock collection objects
    mock_lines_collection = Mock()
    mock_words_collection = Mock()
    
    # Configure mock collection behaviors
    mock_lines_collection.get.return_value = {
        "ids": ["IMAGE#7e2bd911-7afb-4e0a-84de-57f51ce4daff#RECEIPT#00001#LINE#00001"],
        "metadatas": [{
            "canonical_merchant_name": "30740 Russell Ranch Rd (Westlake Village)",
            "merchant_category": "Retail",
            "address": "30740 Russell Ranch Rd, Westlake Village, CA 91362, USA"
        }]
    }
    
    mock_words_collection.get.return_value = {
        "ids": ["IMAGE#7e2bd911-7afb-4e0a-84de-57f51ce4daff#RECEIPT#00001#LINE#00001#WORD#00001"],
        "metadatas": [{
            "label": "PRODUCT_NAME",
            "reasoning": "This appears to be a product name",
            "validation_status": "CONFIRMED"
        }]
    }
    
    # Track update operations
    mock_lines_collection.update = Mock()
    mock_words_collection.update = Mock()
    
    # Create mock ChromaDB client
    mock_chroma_client = Mock()
    
    def mock_get_collection(collection_name):
        if "lines" in collection_name:
            return mock_lines_collection
        elif "words" in collection_name:
            return mock_words_collection
        else:
            raise Exception(f"Collection {collection_name} not found")
    
    mock_chroma_client.get_collection = Mock(side_effect=mock_get_collection)
    
    # Patch the ChromaDBClient class and DynamoDB client at import locations
    with patch('receipt_label.utils.chroma_client.ChromaDBClient') as MockChromeDBClient, \
         patch('receipt_dynamo.data.dynamo_client.DynamoClient') as MockDynamoClient:
        
        MockChromeDBClient.return_value = mock_chroma_client
        MockDynamoClient.return_value = Mock()  # Mock DynamoDB client
        
        yield {
            "chroma_client": mock_chroma_client,
            "lines_collection": mock_lines_collection,
            "words_collection": mock_words_collection,
        }


@pytest.fixture  
def mock_s3_operations():
    """Fixture that mocks S3 upload/download operations for ChromaDB snapshots."""
    
    mock_download_result = {
        "status": "downloaded",
        "local_path": "/tmp/test_chromadb",
        "message": "Successfully downloaded snapshot"
    }
    
    mock_upload_result = {
        "status": "uploaded", 
        "s3_key": "test/chromadb_delta.zip",
        "message": "Successfully uploaded delta"
    }
    
    with patch('receipt_label.utils.chroma_s3_helpers.download_snapshot_from_s3') as mock_download, \
         patch('receipt_label.utils.chroma_s3_helpers.upload_delta_to_s3') as mock_upload, \
         patch('tempfile.mkdtemp') as mock_tempdir:
        
        mock_download.return_value = mock_download_result
        mock_upload.return_value = mock_upload_result
        mock_tempdir.return_value = "/tmp/test_chromadb"
        
        yield {
            "download_snapshot": mock_download,
            "upload_delta": mock_upload,
            "download_result": mock_download_result,
            "upload_result": mock_upload_result,
        }
