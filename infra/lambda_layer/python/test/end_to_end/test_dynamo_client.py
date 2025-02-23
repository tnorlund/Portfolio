import pytest
from typing import Literal
from dynamo import DynamoClient
from dynamo.data._pulumi import load_env

@pytest.fixture(scope="session")
def dynamodb_table() -> str:
    """
    Fixture that retrieves the DynamoDB table name from Pulumi dev environment.
    
    Returns:
        str: The name of the DynamoDB table
    """
    env_vars = load_env("dev")
    return env_vars["dynamodb_table_name"]

@pytest.mark.end_to_end
def test_dynamo_client_init_success(dynamodb_table: str):
    """
    Tests that DynamoClient initializes successfully when provided an existing table.
    """
    client = DynamoClient(dynamodb_table)