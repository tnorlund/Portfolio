import pytest

from receipt_dynamo import DynamoClient
from receipt_dynamo.data._pulumi import load_env
from receipt_trainer.utils.data import process_receipt_details


@pytest.fixture(scope="session")
def dynamodb_table() -> str:
    """
    Fixture that retrieves the DynamoDB table name from Pulumi dev environment.

    Returns:
        str: The name of the DynamoDB table
    """
    env_vars = load_env("dev")
    if not env_vars or "dynamodb_table_name" not in env_vars:
        pytest.skip("DynamoDB table name not found in Pulumi stack outputs")
    return env_vars["dynamodb_table_name"]


@pytest.mark.end_to_end
def test_process_receipt_details(dynamodb_table: str):
    """
    Tests that the process_receipt_details function can process a receipt.
    """
    print(dynamodb_table)
    dynamo_client = DynamoClient(dynamodb_table)
    receipt_details_dict, _ = dynamo_client.listReceiptDetails(5)

    dataset = {}  # Initialize dataset dictionary
    for key, details in receipt_details_dict.items():
        processed_data = process_receipt_details(details)
        if processed_data:
            dataset[key] = processed_data

    # Add an assertion to validate the test
    assert len(dataset) > 0, "No receipt details were processed successfully"
