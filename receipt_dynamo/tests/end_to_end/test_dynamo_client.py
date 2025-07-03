import pytest

from receipt_dynamo import DynamoClient
from receipt_dynamo.data._pulumi import load_env


@pytest.fixture(scope="session")
def dynamodb_table() -> str:
    """
    Fixture that retrieves the DynamoDB table name from Pulumi dev environment.

    Returns:
        str: The name of the DynamoDB table
    """
    env_vars = load_env("prod")
    return env_vars["dynamodb_table_name"]


@pytest.mark.end_to_end
def test_dynamo_client_init_success(dynamodb_table: str):
    """
    Tests that DynamoClient initializes successfully when provided an existing
    table.
    """
    DynamoClient(dynamodb_table)


@pytest.mark.end_to_end
def test_dynamo_client_list_images(dynamodb_table: str):
    """
    Tests that DynamoClient can list images from the table.
    """
    client = DynamoClient(dynamodb_table)
    images, _ = client.list_images(10)
    assert len(images) == 10


@pytest.mark.end_to_end
def test_dynamo_client_list_lines(dynamodb_table: str):
    """
    Tests that DynamoClient can list lines from the table.
    """
    client = DynamoClient(dynamodb_table)
    lines, _ = client.list_lines(10)
    assert len(lines) == 10


@pytest.mark.end_to_end
def test_dynamo_client_list_words(dynamodb_table: str):
    """
    Tests that DynamoClient can list words from the table.
    """
    client = DynamoClient(dynamodb_table)
    words, _ = client.list_words(10)
    assert len(words) == 10


@pytest.mark.end_to_end
def test_dynamo_client_list_letters(dynamodb_table: str):
    """
    Tests that DynamoClient can list letters from the table.
    """
    client = DynamoClient(dynamodb_table)
    letters, _ = client.list_letters(10)
    assert len(letters) == 10


@pytest.mark.end_to_end
def test_dynamo_client_list_receipts(dynamodb_table: str):
    """
    Tests that DynamoClient can list receipts from the table.
    """
    client = DynamoClient(dynamodb_table)
    receipts, _ = client.list_receipts(10)
    assert len(receipts) == 10


@pytest.mark.end_to_end
def test_dynamo_client_list_receipt_words(dynamodb_table: str):
    """
    Tests that DynamoClient can list receipt words from the table.
    """
    client = DynamoClient(dynamodb_table)
    receipt_words, _ = client.list_receipt_words(10)
    assert len(receipt_words) == 10


@pytest.mark.end_to_end
def test_dynamo_client_list_receipt_letters(dynamodb_table: str):
    """
    Tests that DynamoClient can list receipt letters from the table.
    """
    client = DynamoClient(dynamodb_table)
    receipt_letters, _ = client.list_receipt_letters(10)
    assert len(receipt_letters) == 10
