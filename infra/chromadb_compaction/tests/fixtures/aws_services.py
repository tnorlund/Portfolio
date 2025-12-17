"""
AWS Service Mocking Fixtures for ChromaDB Compaction Tests.

This module provides comprehensive AWS service mocking using moto,
following the same patterns used in the receipt_dynamo package.
"""

import os
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws, mock_dynamodb, mock_s3, mock_sqs


@pytest.fixture
def mock_sqs_queues():
    """
    Create mock SQS queues for lines and words collections.

    Returns a dictionary with:
    - sqs_client: Mocked SQS client
    - lines_queue_url: URL for lines collection queue
    - words_queue_url: URL for words collection queue
    """
    with mock_sqs():
        sqs = boto3.client("sqs", region_name="us-east-1")

        # Create lines queue
        lines_response = sqs.create_queue(
            QueueName="chromadb-lines-compaction-queue"
        )
        lines_queue_url = lines_response["QueueUrl"]

        # Create words queue
        words_response = sqs.create_queue(
            QueueName="chromadb-words-compaction-queue"
        )
        words_queue_url = words_response["QueueUrl"]

        yield {
            "sqs_client": sqs,
            "lines_queue_url": lines_queue_url,
            "words_queue_url": words_queue_url,
        }


@pytest.fixture
def mock_dynamodb_table():
    """
    Create mock DynamoDB table following receipt_dynamo pattern.

    Creates a table with the same structure as the production table:
    - PK (HASH), SK (RANGE) keys
    - GSI1, GSI2, GSI3 global secondary indexes
    - TYPE attribute for entity type filtering
    """
    with mock_dynamodb():
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
                {"AttributeName": "GSI2PK", "AttributeType": "S"},
                {"AttributeName": "GSI2SK", "AttributeType": "S"},
                {"AttributeName": "GSI3PK", "AttributeType": "S"},
                {"AttributeName": "GSI3SK", "AttributeType": "S"},
                {"AttributeName": "TYPE", "AttributeType": "S"},
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
                {
                    "IndexName": "GSI2",
                    "KeySchema": [
                        {"AttributeName": "GSI2PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI2SK", "KeyType": "RANGE"},
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {
                        "ReadCapacityUnits": 5,
                        "WriteCapacityUnits": 5,
                    },
                },
                {
                    "IndexName": "GSI3",
                    "KeySchema": [
                        {"AttributeName": "GSI3PK", "KeyType": "HASH"},
                        {"AttributeName": "GSI3SK", "KeyType": "RANGE"},
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

        yield table_name


@pytest.fixture
def mock_s3_bucket():
    """
    Create mock S3 bucket for ChromaDB snapshots.

    Creates a bucket and sets up basic structure for ChromaDB collections:
    - lines/snapshot/latest/
    - words/snapshot/latest/
    """
    with mock_s3():
        s3 = boto3.client("s3", region_name="us-east-1")

        bucket_name = "test-chromadb-bucket"
        s3.create_bucket(Bucket=bucket_name)

        # Create basic directory structure
        s3.put_object(
            Bucket=bucket_name, Key="lines/snapshot/latest/", Body=""
        )
        s3.put_object(
            Bucket=bucket_name, Key="words/snapshot/latest/", Body=""
        )

        yield bucket_name


@pytest.fixture
def mock_chromadb_collections():
    """
    Mock ChromaDB collections for testing.

    Returns mock collection objects that simulate ChromaDB operations.
    """
    from unittest.mock import MagicMock

    lines_collection = MagicMock()
    lines_collection.name = "lines"
    lines_collection.update = MagicMock()
    lines_collection.delete = MagicMock()
    lines_collection.get = MagicMock(return_value={"ids": [], "metadatas": []})

    words_collection = MagicMock()
    words_collection.name = "words"
    words_collection.update = MagicMock()
    words_collection.delete = MagicMock()
    words_collection.get = MagicMock(return_value={"ids": [], "metadatas": []})

    yield {
        "lines": lines_collection,
        "words": words_collection,
    }


@pytest.fixture
def mock_s3_operations():
    """
    Mock S3 operations for ChromaDB snapshot handling.

    Mocks the atomic snapshot download/upload operations used by
    the compaction handler.
    """
    from unittest.mock import MagicMock

    # Create direct mocks for S3 operations
    mock_download = MagicMock()
    mock_upload = MagicMock()

    # Mock successful download
    mock_download.return_value = {
        "status": "downloaded",
        "version_id": "test-version-123",
        "local_path": "/tmp/test-snapshot",
    }

    # Mock successful upload
    mock_upload.return_value = {
        "status": "uploaded",
        "version_id": "test-version-456",
        "s3_path": "s3://test-bucket/test-path",
    }

    yield {
        "download_snapshot_atomic": mock_download,
        "upload_snapshot_atomic": mock_upload,
    }


@pytest.fixture
def mock_dynamo_client(mock_dynamodb_table):
    """
    Create a DynamoClient instance using the mocked DynamoDB table.

    This follows the same pattern as receipt_dynamo tests.
    """
    from receipt_dynamo.data.dynamo_client import DynamoClient

    client = DynamoClient(table_name=mock_dynamodb_table)
    yield client


@pytest.fixture
def aws_test_environment(mock_sqs_queues, mock_dynamodb_table, mock_s3_bucket):
    """
    Complete AWS test environment with all services mocked.

    This fixture combines all AWS service mocks and sets up
    environment variables for testing.
    """
    with patch.dict(
        os.environ,
        {
            "DYNAMODB_TABLE_NAME": mock_dynamodb_table,
            "CHROMADB_BUCKET": mock_s3_bucket,
            "LINES_QUEUE_URL": mock_sqs_queues["lines_queue_url"],
            "WORDS_QUEUE_URL": mock_sqs_queues["words_queue_url"],
            "AWS_REGION": "us-east-1",
            "HEARTBEAT_INTERVAL_SECONDS": "30",
            "LOCK_DURATION_MINUTES": "3",
            "MAX_HEARTBEAT_FAILURES": "3",
        },
    ):
        yield {
            "sqs": mock_sqs_queues,
            "dynamodb_table": mock_dynamodb_table,
            "s3_bucket": mock_s3_bucket,
        }


# Integration test fixtures that combine multiple mocks
@pytest.fixture
def integration_test_environment(
    aws_test_environment, mock_chromadb_collections, mock_s3_operations
):
    """
    Complete integration test environment.

    Combines all AWS service mocks with ChromaDB and S3 operation mocks
    for comprehensive integration testing.
    """
    yield {
        **aws_test_environment,
        "chromadb_collections": mock_chromadb_collections,
        "s3_operations": mock_s3_operations,
    }
