"""Integration test fixtures for receipt_chroma using moto for AWS mocking."""

import tempfile

import boto3
import pytest
from moto import mock_aws
from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ChromaDBCollection

from receipt_chroma import ChromaClient


@pytest.fixture
def temp_chromadb_dir():
    """Create a temporary directory for ChromaDB persistence."""
    with tempfile.TemporaryDirectory(prefix="chromadb_test_") as tmpdir:
        yield tmpdir


@pytest.fixture
def chroma_client_write(temp_chromadb_dir):
    """Create a ChromaClient in write mode."""
    client = ChromaClient(
        persist_directory=temp_chromadb_dir,
        mode="write",
        metadata_only=True,
    )
    yield client
    client.close()


@pytest.fixture
def chroma_client_read(temp_chromadb_dir):
    """Create a ChromaClient in read mode."""
    # First create some data with write mode
    with ChromaClient(
        persist_directory=temp_chromadb_dir,
        mode="write",
        metadata_only=True,
    ) as write_client:
        write_client.upsert(
            collection_name="test",
            ids=["1"],
            documents=["test document"],
        )

    # Now return read client
    client = ChromaClient(
        persist_directory=temp_chromadb_dir,
        mode="read",
    )
    yield client
    client.close()


@pytest.fixture
def chroma_client_delta(temp_chromadb_dir):
    """Create a ChromaClient in delta mode."""
    client = ChromaClient(
        persist_directory=temp_chromadb_dir,
        mode="delta",
        metadata_only=True,
    )
    yield client
    client.close()


@pytest.fixture
def populated_chroma_db(temp_chromadb_dir):
    """Create a ChromaDB with pre-populated data."""
    collection_name = "populated_test"
    client = ChromaClient(
        persist_directory=temp_chromadb_dir,
        mode="write",
        metadata_only=True,
    )

    # Populate with test data
    client.upsert(
        collection_name=collection_name,
        ids=["doc1", "doc2", "doc3"],
        documents=[
            "Python is a programming language",
            "JavaScript is used for web development",
            "Machine learning uses Python",
        ],
        metadatas=[
            {"topic": "programming"},
            {"topic": "web"},
            {"topic": "ml"},
        ],
    )

    yield client, collection_name
    client.close()


@pytest.fixture
def s3_bucket(request):
    """Create a mock S3 bucket using moto.

    Uses explicit mock_aws start/stop pattern to ensure proper isolation
    when tests run in parallel with pytest-xdist.
    """
    mock_context = mock_aws()
    mock_context.start()

    try:
        s3 = boto3.client("s3", region_name="us-east-1")
        bucket_name = request.param
        s3.create_bucket(Bucket=bucket_name)
        yield bucket_name
    finally:
        # Ensure moto context is properly cleaned up after test
        mock_context.stop()


@pytest.fixture
def dynamodb_table(request):
    """Spin up a moto DynamoDB table with GSIs and yield its name.

    Tears down automatically after tests. Uses function scope to ensure
    proper isolation when tests run in parallel with pytest-xdist.

    This fixture is NOT scoped at module level to prevent test pollution
    when running tests concurrently.
    """
    # Start a fresh moto mock context for each test
    mock_context = mock_aws()
    mock_context.start()

    try:
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")

        table_name = "MyMockedTable"
        dynamodb.create_table(
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
                {
                    "IndexName": "GSITYPE",
                    "KeySchema": [
                        {"AttributeName": "TYPE", "KeyType": "HASH"}
                    ],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {
                        "ReadCapacityUnits": 5,
                        "WriteCapacityUnits": 5,
                    },
                },
            ],
        )

        # Wait for the table to be created
        dynamodb.meta.client.get_waiter("table_exists").wait(
            TableName=table_name
        )

        # Yield the table name so your tests can reference it
        yield table_name
    finally:
        # Ensure moto context is properly cleaned up after test
        mock_context.stop()


@pytest.fixture
def dynamo_client(dynamodb_table):
    """Create a DynamoClient instance for testing."""
    return DynamoClient(table_name=dynamodb_table)


@pytest.fixture
def lock_manager_lines(dynamo_client):
    """Create a LockManager for LINES collection."""
    from receipt_chroma.lock_manager import LockManager

    return LockManager(
        dynamo_client=dynamo_client,
        collection=ChromaDBCollection.LINES,
        heartbeat_interval=1,  # Short interval for testing
        lock_duration_minutes=5,
    )


@pytest.fixture
def lock_manager_words(dynamo_client):
    """Create a LockManager for WORDS collection."""
    from receipt_chroma.lock_manager import LockManager

    return LockManager(
        dynamo_client=dynamo_client,
        collection=ChromaDBCollection.WORDS,
        heartbeat_interval=1,  # Short interval for testing
        lock_duration_minutes=5,
    )


@pytest.fixture
def mock_s3_bucket_compaction():
    """Create a mock S3 bucket for compaction testing.

    Uses explicit mock_aws start/stop pattern to ensure proper isolation
    when tests run in parallel with pytest-xdist. This prevents S3 state
    pollution across concurrent test workers.
    """
    mock_context = mock_aws()
    mock_context.start()

    try:
        s3_client = boto3.client("s3", region_name="us-east-1")
        bucket_name = "test-chromadb-bucket"
        s3_client.create_bucket(Bucket=bucket_name)
        yield s3_client, bucket_name
    finally:
        # Ensure moto context is properly cleaned up after test
        mock_context.stop()


@pytest.fixture(scope="function", autouse=False)
def reset_s3_state():
    """Reset S3 state between tests that use S3 mocks.

    This fixture helps prevent S3 checksum validation issues that can occur
    when running multiple S3 tests together due to moto's internal state.
    """
    yield
    # Cleanup happens after the test
    # Force garbage collection to help clean up S3 connections
    import gc
    gc.collect()


@pytest.fixture
def chroma_snapshot_with_data(temp_chromadb_dir):
    """Create a ChromaDB snapshot with test data for compaction testing.

    Creates both lines and words collections with sample embeddings.
    """
    client = ChromaClient(persist_directory=temp_chromadb_dir, mode="write")

    # Add test data to lines collection
    client.upsert(
        collection_name="lines",
        ids=[
            "IMAGE#test-id#RECEIPT#00001#LINE#00001",
            "IMAGE#test-id#RECEIPT#00001#LINE#00002",
        ],
        embeddings=[[0.1] * 1536, [0.2] * 1536],
        metadatas=[
            {"text": "Test line 1", "merchant_name": "Old Merchant"},
            {"text": "Test line 2", "merchant_name": "Old Merchant"},
        ],
    )

    # Add test data to words collection
    client.upsert(
        collection_name="words",
        ids=[
            "IMAGE#test-id#RECEIPT#00001#LINE#00001#WORD#00001",
            "IMAGE#test-id#RECEIPT#00001#LINE#00001#WORD#00002",
        ],
        embeddings=[[0.3] * 1536, [0.4] * 1536],
        metadatas=[
            {
                "text": "Test",
                "label_status": "auto_suggested",
                "valid_labels": "",
                "invalid_labels": "",
            },
            {
                "text": "Word",
                "label_status": "auto_suggested",
                "valid_labels": "",
                "invalid_labels": "",
            },
        ],
    )

    client.close()
    yield temp_chromadb_dir


@pytest.fixture
def mock_logger():
    """Create a mock logger for testing."""
    from tests.helpers.factories import create_mock_logger

    return create_mock_logger()


@pytest.fixture
def mock_metrics():
    """Create a mock metrics collector for testing."""
    from tests.helpers.factories import create_mock_metrics

    return create_mock_metrics()
