import pytest
from receipt_dynamo import DynamoClient
from receipt_dynamo.data._pulumi import load_env
from receipt_trainer.utils.data import process_receipt_details
from receipt_trainer.utils.pulumi import get_auto_scaling_config


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


@pytest.mark.end_to_end
def test_get_auto_scaling_config():
    """
    Tests that the get_auto_scaling_config function properly retrieves configuration from Pulumi.
    """
    config = get_auto_scaling_config("dev")

    # Verify the config is returned as a dict
    assert config is not None
    assert isinstance(config, dict)

    # Verify required scaling parameters
    assert config["min_instances"] == 1
    assert config["max_instances"] == 10

    # Verify that the configuration contains required keys
    # Note: The actual values may vary depending on the Pulumi stack
    # so we just check for the presence of keys rather than specific values
    required_fields = [
        "queue_url",
        "dynamo_table",
        "min_instances",
        "max_instances",
    ]

    for field in required_fields:
        assert field in config, f"Required field '{field}' missing from config"

    # Verify some of the optional fields
    # These may or may not be present, so we just check they're there if they're provided
    optional_fields = [
        "instance_registry_table",
        "instance_ami",
        "instance_profile",
        "subnet_id",
        "security_group_id",
        "efs_id",
        "s3_bucket",
    ]

    present_optional_fields = [
        field for field in optional_fields if field in config
    ]
    assert (
        len(present_optional_fields) > 0
    ), "No optional fields found in config"

    # Print the fields that are available for debugging
    print(
        f"Available configuration fields: {', '.join(sorted(config.keys()))}"
    )
