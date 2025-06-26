import uuid
from typing import Literal

import pytest

from receipt_dynamo import DynamoClient


@pytest.mark.integration
def test_dynamo_client_init_success(dynamodb_table: Literal["MyMockedTable"]):
    """
    Tests that DynamoClient initializes successfully when provided an existing
    table.
    """
    client = DynamoClient(dynamodb_table)
    assert client.table_name == dynamodb_table

    # Optionally, confirm that describe_table returns valid table details.
    response = client._client.describe_table(TableName=dynamodb_table)
    assert "Table" in response


@pytest.mark.integration
def test_dynamo_client_init_table_not_found(dynamodb_table):
    """
    Tests that DynamoClient raises a ValueError when the specified table does
    not exist.
    """
    fake_table = f"NonExistentTable-{uuid.uuid4()}"
    with pytest.raises(
        ValueError,
        match=f"The table '{fake_table}' does not exist in region " "'us-east-1'.",
    ):
        DynamoClient(fake_table)
