import boto3
import pytest
from moto import mock_aws


@pytest.fixture
def dynamodb_table():
    """
    Spins up a mock DynamoDB instance, creates a table (with 2 GSIs: GSI1 and GSITYPE),
    waits until both the table and the GSIs are active, then yields
    the table name for tests.

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
                {"AttributeName": "TYPE", "AttributeType": "S"},
            ],
            ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
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
                    "IndexName": "GSITYPE",
                    "KeySchema": [{"AttributeName": "TYPE", "KeyType": "HASH"}],
                    "Projection": {"ProjectionType": "ALL"},
                    "ProvisionedThroughput": {
                        "ReadCapacityUnits": 5,
                        "WriteCapacityUnits": 5,
                    },
                },
            ],
        )

        # Wait for the table to be created
        dynamodb.meta.client.get_waiter("table_exists").wait(TableName=table_name)

        # Yield the table name so your tests can reference it
        yield table_name

@pytest.fixture
def s3_bucket():
    """
    Spins up a mock S3 instance, creates a bucket, then yields the bucket name for tests.

    After the tests, everything is torn down automatically.
    """
    with mock_aws():
        s3 = boto3.client("s3", region_name="us-east-1")

        bucket_name = "raw-image-bucket"
        s3.create_bucket(Bucket=bucket_name)

        # Yield the bucket name so your tests can reference it
        yield bucket_name