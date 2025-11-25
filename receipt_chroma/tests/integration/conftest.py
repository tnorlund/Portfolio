"""Integration test fixtures for receipt_chroma using moto for AWS mocking."""

import boto3
import pytest
from moto import mock_aws

from receipt_dynamo import DynamoClient
from receipt_dynamo.constants import ChromaDBCollection


@pytest.fixture
def dynamodb_table():
    """
    Spins up a mock DynamoDB instance, creates a table (with GSIs: GSI1, GSI2,
    GSI3, and GSITYPE), waits until both the table and the GSIs are active, then
    yields the table name for tests.

    After the tests, everything is torn down automatically.
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
