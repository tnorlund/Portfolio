"""Integration test fixtures for receipt_chroma using moto for AWS mocking.

CHROMADB CLIENT USAGE IN TESTS:
==============================

ChromaClient MUST be used with context managers (with statement).
For test code, always use the pattern below:

    with ChromaClient(persist_directory=tmpdir) as client:
        client.upsert(...)
        # Use client here
    # Automatic cleanup here via __exit__

DO NOT:
- Call client.close() manually outside try/finally
- Add time.sleep() after close() in tests
- Use nested ChromaClient contexts on same persist_directory
- Mix context managers with manual close() calls

WHY: ChromaDB doesn't expose close(). ChromaClient.close() handles complex
      cleanup including gc.collect() 3 times and 0.5s sleep. Context managers
      guarantee this happens at the right moment.

Reference: ChromaDB issue #5868, ChromaClient class docstring.

CHROMADB GLOBAL STATE RESET:
============================

ChromaDB's _system.stop() (called by PersistentClient.close()) corrupts global
Rust bindings state. The reset_chromadb_state fixture reimports chromadb modules
after each test to clear this corruption, allowing subsequent tests to create
new clients successfully.
"""

import gc
import sys
import tempfile

import boto3
import pytest
from moto import mock_aws
from receipt_chroma import ChromaClient

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ChromaDBCollection


def reset_chromadb_modules():
    """Reset ChromaDB global state by reimporting modules.

    ChromaDB's _system.stop() (called when closing PersistentClient) corrupts
    global Rust bindings state. This helper reimports chromadb modules to clear
    the corrupted state.

    Use this BETWEEN PersistentClient instances in the same test when you need
    to test data persistence across client sessions.

    IMPORTANT: This resets BOTH chromadb and receipt_chroma modules to ensure
    the receipt_chroma.data.chroma_client module gets a fresh chromadb reference.
    After calling this, you MUST re-import ChromaClient.

    See GitHub issue #5868 for context on ChromaDB cleanup challenges.
    """
    # Remove chromadb modules AND receipt_chroma modules from sys.modules
    # Both need to be reset so receipt_chroma.data.chroma_client gets a fresh
    # chromadb reference
    modules_to_reset = [
        mod
        for mod in list(sys.modules.keys())
        if mod == "chromadb"
        or mod.startswith("chromadb.")
        or mod == "receipt_chroma"
        or mod.startswith("receipt_chroma.")
    ]
    for mod in modules_to_reset:
        del sys.modules[mod]

    # Force garbage collection to fully release resources
    gc.collect()


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
    when tests run in parallel with pytest-xdist. Includes aggressive cleanup
    with garbage collection to ensure moto state is fully released.
    """
    import gc

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
        # Force garbage collection to release moto's internal state
        gc.collect()


@pytest.fixture
def dynamodb_table():
    """Spin up a moto DynamoDB table with GSIs and yield its name.

    Tears down automatically after tests.
    """
    with mock_aws():
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

    Each test gets a unique bucket name to prevent collisions when running
    tests in parallel. Includes aggressive cleanup with garbage collection
    to ensure moto state is fully released.
    """
    import gc
    import uuid

    mock_context = mock_aws()
    mock_context.start()

    try:
        s3_client = boto3.client("s3", region_name="us-east-1")
        # Use unique bucket name per test to prevent collisions in parallel execution
        bucket_name = f"test-chromadb-{uuid.uuid4().hex[:8]}"
        s3_client.create_bucket(Bucket=bucket_name)
        yield s3_client, bucket_name
    finally:
        # Ensure moto context is properly cleaned up after test
        mock_context.stop()
        # Force garbage collection to release moto's internal state
        gc.collect()


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
