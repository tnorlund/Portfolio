import io
import os
from pathlib import Path
from types import SimpleNamespace

import boto3
import pytest
import receipt_label.utils.clients as clients
from moto import mock_aws

from receipt_dynamo.data.dynamo_client import DynamoClient


def pytest_runtest_setup(item):
    """Setup for each test - skip performance tests if environment variable is set."""
    if item.get_closest_marker("performance"):
        if os.environ.get("SKIP_PERFORMANCE_TESTS", "").lower() == "true":
            pytest.skip(
                "Skipping performance tests (SKIP_PERFORMANCE_TESTS=true)"
            )


@pytest.fixture
def dynamodb_table_and_s3_bucket():
    """
    Spins up a mock DynamoDB instance, creates a table (with GSIs: GSI1, GSI2,
    and GSITYPE), waits until both the table and the GSIs are active, then
    yields the table name for tests.
    """
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")

        table_name = "TestTable"
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

        # Create a mock S3 bucket for uploads
        s3 = boto3.client("s3", region_name="us-east-1")
        bucket_name = "test-bucket"
        s3.create_bucket(Bucket=bucket_name)

        yield table_name, bucket_name


@pytest.fixture
def places_api(dynamodb_table):
    """
    Creates a PlacesAPI instance with a mock DynamoDB table.
    """
    # Import here to avoid circular imports
    from receipt_label.data.places_api import PlacesAPI

    return PlacesAPI("test_api_key", dynamodb_table)


@pytest.fixture
def batch_processor(dynamodb_table):
    """
    Creates a BatchPlacesProcessor instance with a mock DynamoDB table.
    """
    # Import here to avoid circular imports
    from receipt_label.data.places_api import BatchPlacesProcessor

    return BatchPlacesProcessor("test_api_key", dynamodb_table)


@pytest.fixture(autouse=True)
def patch_clients(mocker, dynamodb_table_and_s3_bucket):
    """
    Autouse fixture that:
    1) Patches receipt_label.utils.clients.get_clients to return the Moto DynamoClient
       AND
    2) Patches both submit_batch and poll_batch modules' openai_client to a simple mock.
    """
    table_name, _ = dynamodb_table_and_s3_bucket

    # Only set table_name from dynamodb_table_and_s3_bucket if not already in environment
    if "DYNAMODB_TABLE_NAME" not in os.environ:
        os.environ["DYNAMODB_TABLE_NAME"] = table_name

    # Use the table name from environment (which might have been set by a specific test)
    table_name = os.environ["DYNAMODB_TABLE_NAME"]

    # 1) Fake Dynamo + OpenAI in get_clients()
    fake_openai = mocker.Mock()
    fake_openai.files.create.return_value = SimpleNamespace(id="fake-file-id")
    fake_openai.batches.create.return_value = SimpleNamespace(
        id="fake-batch-id"
    )
    # Stub the batch status retrieval to return "completed"
    fake_openai.batches.retrieve.return_value = SimpleNamespace(
        status="completed", output_file_id="fake-output-file-id"
    )

    # Stub files.content() to return our JSONL fixture as a file-like object
    fixture_path = Path(__file__).parent / "fixtures" / "batch_output.jsonl"
    jsonl = fixture_path.read_text()
    fake_file = io.BytesIO(jsonl.encode("utf-8"))
    fake_openai.files.content.return_value = fake_file

    # Create a fake index with dynamic upsert behavior
    fake_index = mocker.Mock()

    # Attach our upsert logic so pinecone_index.upsert(...) returns the correct dict
    def fake_upsert(vectors=None, **kwargs):
        # Return upserted_count equal to number of vectors
        return {"upserted_count": len(vectors or [])}

    fake_index.upsert.side_effect = fake_upsert

    # Create mock client_manager
    from receipt_label.utils.client_manager import ClientManager

    mock_client_manager = mocker.Mock(spec=ClientManager)
    # Use a mock for dynamo since the real one requires AWS setup
    mock_dynamo = mocker.Mock()
    mock_client_manager.dynamo = mock_dynamo
    mock_client_manager.openai = fake_openai
    mock_client_manager.pinecone = fake_index

    # Patch get_client_manager to return our mock
    mocker.patch(
        "receipt_label.utils.clients.get_client_manager",
        return_value=mock_client_manager,
    )

    # Also patch it in the main utils module
    mocker.patch(
        "receipt_label.utils.get_client_manager",
        return_value=mock_client_manager,
    )

    # Legacy support - keep get_clients patched for any old code
    def fake_get_clients():
        # Use mock dynamo instead of real DynamoClient to avoid AWS dependencies
        return mock_dynamo, fake_openai, fake_index

    mocker.patch.object(clients, "get_clients", fake_get_clients)

    return fake_openai, fake_index
