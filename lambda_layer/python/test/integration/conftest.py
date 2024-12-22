import boto3
import pytest
from moto import mock_aws


@pytest.fixture
def dynamodb_table():
    """
    This fixture:
    - Spins up a mock DynamoDB table
    - Creates a table for testing
    - Yields the table name
    - Tears down the table after the test
    """
    with mock_aws():
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")

        # Create the mock table
        table_name = "MyMockedTable"
        table = dynamodb.create_table(
            TableName=table_name,
            KeySchema=[
                {"AttributeName": "PK", "KeyType": "HASH"},
                {"AttributeName": "SK", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "PK", "AttributeType": "S"},
                {"AttributeName": "SK", "AttributeType": "S"},
            ],
            ProvisionedThroughput={"ReadCapacityUnits": 5, "WriteCapacityUnits": 5},
        )
        dynamodb.meta.client.get_waiter("table_exists").wait(TableName=table_name)

        # Yield the table name so your tests can reference it
        yield table_name
