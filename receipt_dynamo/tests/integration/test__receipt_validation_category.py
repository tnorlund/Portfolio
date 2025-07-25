import uuid
from typing import Literal

import pytest
from botocore.exceptions import ClientError

from receipt_dynamo import ReceiptValidationCategory
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBAccessError,
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBValidationError,
    EntityAlreadyExistsError,
)


@pytest.fixture
def sample_receipt_validation_category():
    """Returns a sample ReceiptValidationCategory for testing."""
    return ReceiptValidationCategory(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        field_name="total_amount",
        field_category="amount",
        status="valid",
        reasoning="The total amount value matches the expected format",
        result_summary={"valid": 3, "invalid": 0},
        validation_timestamp="2023-05-15T12:34:56.789Z",
        metadata={"confidence": 0.95},
    )


@pytest.mark.integration
def test_addReceiptValidationCategory_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_category: ReceiptValidationCategory,
):
    """
    Tests that addReceiptValidationCategory correctly adds a category to DynamoDB.

    Args:
        dynamodb_table: The mocked DynamoDB table name.
        sample_receipt_validation_category: A sample ReceiptValidationCategory for testing.
    """
    # Setup
    client = DynamoClient(table_name=dynamodb_table)

    # Execute
    client.add_receipt_validation_category(sample_receipt_validation_category)

    # Verify
    response = client._client.get_item(
        TableName=dynamodb_table,
        Key=sample_receipt_validation_category.key,
    )
    assert "Item" in response, "Item was not added to the table."


@pytest.mark.integration
def test_addReceiptValidationCategory_duplicate_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_category: ReceiptValidationCategory,
):
    """
    Tests that adding a duplicate ReceiptValidationCategory raises an error.

    Args:
        dynamodb_table: The mocked DynamoDB table name.
        sample_receipt_validation_category: A sample ReceiptValidationCategory for testing.
    """
    # Setup
    client = DynamoClient(table_name=dynamodb_table)

    # Add the category first time
    client.add_receipt_validation_category(sample_receipt_validation_category)

    # Attempt to add the same category again and expect an error
    with pytest.raises(
        EntityAlreadyExistsError,
        match=f"Entity already exists: ReceiptValidationCategory with receipt_id={sample_receipt_validation_category.receipt_id}",
    ):
        client.add_receipt_validation_category(
            sample_receipt_validation_category
        )

    # Clean up
    client._client.delete_item(
        TableName=dynamodb_table,
        Key=sample_receipt_validation_category.key,
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "category cannot be None"),
        (
            "not-a-validation-category",
            "category must be an instance of the ReceiptValidationCategory class.",
        ),
    ],
)
def test_addReceiptValidationCategory_invalid_parameters(
    dynamodb_table,
    sample_receipt_validation_category,
    mocker,
    invalid_input,
    expected_error,
):
    """
    Tests that addReceiptValidationCategory validates input parameters correctly.

    Args:
        dynamodb_table: The mocked DynamoDB table name.
        sample_receipt_validation_category: A sample ReceiptValidationCategory for testing.
        mocker: Pytest mocker fixture.
        invalid_input: Invalid input parameter.
        expected_error: Expected error message.
    """
    # Setup
    client = DynamoClient(table_name=dynamodb_table)
    mock_put_item = mocker.patch.object(client._client, "put_item")

    # Execute and Assert
    with pytest.raises(ValueError, match=expected_error):
        client.add_receipt_validation_category(invalid_input)

    # Verify that put_item was not called
    mock_put_item.assert_not_called()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_exception",
    [
        (
            "ConditionalCheckFailedException",
            "Item already exists",
            "Entity already exists: ReceiptValidationCategory with receipt_id=1",
        ),
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found for operation add_receipt_validation_category",
        ),
        (
            "ProvisionedThroughputExceededException",
            "Provisioned throughput exceeded",
            "Provisioned throughput exceeded",
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
        ),
        (
            "UnknownError",
            "Unknown error",
            "Could not add receipt to DynamoDB",
        ),
        (
            "ValidationException",
            "One or more parameters given were invalid",
            "One or more parameters given were invalid",
        ),
        ("AccessDeniedException", "Access denied", "Access denied"),
    ],
)
def test_addReceiptValidationCategory_client_errors(
    dynamodb_table,
    sample_receipt_validation_category,
    mocker,
    error_code,
    error_message,
    expected_exception,
):
    """
    Tests that addReceiptValidationCategory handles client errors appropriately.

    Args:
        dynamodb_table: The mocked DynamoDB table name.
        sample_receipt_validation_category: A sample ReceiptValidationCategory for testing.
        mocker: Pytest mocker fixture.
        error_code: AWS error code to simulate.
        error_message: AWS error message to simulate.
        expected_exception: Expected exception message.
    """
    # Setup
    client = DynamoClient(table_name=dynamodb_table)

    # Mock the DynamoDB client to raise the specified error
    mock_put_item = mocker.patch.object(client._client, "put_item")
    mock_put_item.side_effect = ClientError(
        {"Error": {"Code": error_code, "Message": error_message}}, "PutItem"
    )

    # Execute and Assert
    with pytest.raises(Exception, match=expected_exception):
        client.add_receipt_validation_category(
            sample_receipt_validation_category
        )


@pytest.mark.integration
def test_addReceiptValidationCategories_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_category: ReceiptValidationCategory,
):
    """
    Tests that addReceiptValidationCategories correctly adds multiple categories to DynamoDB.

    Args:
        dynamodb_table: The mocked DynamoDB table name.
        sample_receipt_validation_category: A sample ReceiptValidationCategory for testing.
    """
    # Setup
    client = DynamoClient(table_name=dynamodb_table)

    # Create a list of categories
    categories = [
        sample_receipt_validation_category,
        ReceiptValidationCategory(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            field_name="payment_method",
            field_category="payment",
            status="valid",
            reasoning="The payment method is valid",
            result_summary={"valid": 2, "invalid": 0},
            validation_timestamp="2023-05-15T12:34:56.789Z",
            metadata={"confidence": 0.9},
        ),
        ReceiptValidationCategory(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            field_name="merchant_name",
            field_category="merchant",
            status="invalid",
            reasoning="The merchant name could not be validated",
            result_summary={"valid": 0, "invalid": 1},
            validation_timestamp="2023-05-15T12:34:56.789Z",
            metadata={"confidence": 0.7},
        ),
    ]

    # Execute
    client.add_receipt_validation_categories(categories)

    # Verify
    for category in categories:
        response = client._client.get_item(
            TableName=dynamodb_table,
            Key=category.key,
        )
        assert (
            "Item" in response
        ), f"Category {category.field_name} was not added to the table."

    # Clean up
    for category in categories:
        client._client.delete_item(
            TableName=dynamodb_table,
            Key=category.key,
        )


@pytest.mark.integration
def test_addReceiptValidationCategories_with_large_batch(
    dynamodb_table, sample_receipt_validation_category
):
    """
    Tests that addReceiptValidationCategories correctly handles large batches by splitting them.

    Args:
        dynamodb_table: The mocked DynamoDB table name.
        sample_receipt_validation_category: A sample ReceiptValidationCategory for testing.
    """
    # Setup
    client = DynamoClient(table_name=dynamodb_table)

    # Create 30 categories (more than the 25 batch limit)
    categories = []
    for i in range(30):
        categories.append(
            ReceiptValidationCategory(
                receipt_id=1,
                image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                field_name=f"field_{i}",
                field_category="test_category",
                status="valid",
                reasoning=f"Test reasoning {i}",
                result_summary={"valid": i, "invalid": 0},
                validation_timestamp="2023-05-15T12:34:56.789Z",
                metadata={"index": i},
            )
        )

    # Execute
    client.add_receipt_validation_categories(categories)

    # Verify
    for category in categories:
        response = client._client.get_item(
            TableName=dynamodb_table,
            Key=category.key,
        )
        assert (
            "Item" in response
        ), f"Category {category.field_name} was not added to the table."

    # Clean up
    for category in categories:
        client._client.delete_item(
            TableName=dynamodb_table,
            Key=category.key,
        )


@pytest.mark.integration
def test_addReceiptValidationCategories_with_unprocessed_items_retries(
    dynamodb_table, sample_receipt_validation_category, mocker
):
    """
    Tests that addReceiptValidationCategories correctly retries unprocessed items.

    Args:
        dynamodb_table: The mocked DynamoDB table name.
        sample_receipt_validation_category: A sample ReceiptValidationCategory for testing.
        mocker: Pytest mocker fixture.
    """
    # Setup
    client = DynamoClient(table_name=dynamodb_table)

    # Create a list of 2 categories
    categories = [
        sample_receipt_validation_category,
        ReceiptValidationCategory(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            field_name="payment_method",
            field_category="payment",
            status="valid",
            reasoning="The payment method is valid",
            result_summary={"valid": 2, "invalid": 0},
            validation_timestamp="2023-05-15T12:34:56.789Z",
            metadata={"confidence": 0.9},
        ),
    ]

    # Mock the batch_write_item to simulate unprocessed items on first call
    mock_batch_write = mocker.patch.object(client._client, "batch_write_item")

    # Define the side effect function to return unprocessed items on first call and nothing on second call
    def batch_write_side_effect(*args, **kwargs):
        if mock_batch_write.call_count == 1:
            # First call: return one unprocessed item
            return {
                "UnprocessedItems": {
                    dynamodb_table: [
                        {"PutRequest": {"Item": categories[1].to_item()}}
                    ]
                }
            }
        else:
            # Second call: all processed successfully
            return {"UnprocessedItems": {}}

    mock_batch_write.side_effect = batch_write_side_effect

    # Execute
    client.add_receipt_validation_categories(categories)

    # Verify batch_write_item was called exactly twice
    assert (
        mock_batch_write.call_count == 2
    ), "batch_write_item should be called exactly twice"


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "categories cannot be None"),
        (
            "not-a-list",
            "categories must be a list of ReceiptValidationCategory instances.",
        ),
        (
            ["not-a-validation-category"],
            "All categories must be instances of the ReceiptValidationCategory class.",
        ),
    ],
)
def test_addReceiptValidationCategories_invalid_parameters(
    dynamodb_table,
    sample_receipt_validation_category,
    mocker,
    invalid_input,
    expected_error,
):
    """
    Tests that addReceiptValidationCategories validates input parameters correctly.

    Args:
        dynamodb_table: The mocked DynamoDB table name.
        sample_receipt_validation_category: A sample ReceiptValidationCategory for testing.
        mocker: Pytest mocker fixture.
        invalid_input: Invalid input parameter.
        expected_error: Expected error message.
    """
    # Setup
    client = DynamoClient(table_name=dynamodb_table)
    mock_batch_write = mocker.patch.object(client._client, "batch_write_item")

    # Execute and Assert
    with pytest.raises(ValueError, match=expected_error):
        client.add_receipt_validation_categories(invalid_input)

    # Verify that batch_write_item was not called
    mock_batch_write.assert_not_called()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error_message",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found for operation add_receipt_validation_categories",
        ),
        (
            "ProvisionedThroughputExceededException",
            "Throughput exceeded",
            "Provisioned throughput exceeded",
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
        ),
        (
            "ValidationException",
            "One or more parameters given were invalid",
            "One or more parameters given were invalid",
        ),
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied",
        ),
        (
            "UnknownError",
            "Unknown error occurred",
            "Could not add receipt to DynamoDB",
        ),
    ],
)
def test_addReceiptValidationCategories_client_errors(
    dynamodb_table,
    sample_receipt_validation_category,
    mocker,
    error_code,
    error_message,
    expected_error_message,
):
    """
    Tests that addReceiptValidationCategories handles client errors correctly.

    Args:
        dynamodb_table: The mocked DynamoDB table name.
        sample_receipt_validation_category: A sample ReceiptValidationCategory for testing.
        mocker: Pytest mocker fixture.
        error_code: AWS error code to simulate.
        error_message: AWS error message to simulate.
        expected_error_message: Expected error message.
    """
    # Setup
    client = DynamoClient(table_name=dynamodb_table)
    mock_batch_write = mocker.patch.object(client._client, "batch_write_item")
    mock_batch_write.side_effect = ClientError(
        {"Error": {"Code": error_code, "Message": error_message}},
        "BatchWriteItem",
    )

    # Execute and Assert
    with pytest.raises(Exception, match=expected_error_message):
        client.add_receipt_validation_categories(
            [sample_receipt_validation_category]
        )


@pytest.mark.integration
def test_updateReceiptValidationCategory_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_category: ReceiptValidationCategory,
):
    """
    Tests that updateReceiptValidationCategory correctly updates an existing category.

    Args:
        dynamodb_table: The mocked DynamoDB table name.
        sample_receipt_validation_category: A sample ReceiptValidationCategory for testing.
    """
    # Setup
    client = DynamoClient(table_name=dynamodb_table)

    # First, add the category
    client.add_receipt_validation_category(sample_receipt_validation_category)

    # Create an updated version
    updated_category = ReceiptValidationCategory(
        receipt_id=sample_receipt_validation_category.receipt_id,
        image_id=sample_receipt_validation_category.image_id,
        field_name=sample_receipt_validation_category.field_name,
        field_category=sample_receipt_validation_category.field_category,
        status="invalid",  # Changed status
        reasoning="Updated reasoning after review",  # Changed reasoning
        result_summary={"valid": 1, "invalid": 2},  # Changed result summary
        validation_timestamp="2023-05-16T10:20:30.456Z",  # Updated timestamp
        metadata={"confidence": 0.85, "updated": True},  # Updated metadata
    )

    # Execute
    client.update_receipt_validation_category(updated_category)

    # Verify
    response = client._client.get_item(
        TableName=dynamodb_table,
        Key=updated_category.key,
    )
    assert "Item" in response, "Item was not found in the table."

    # Verify specific attributes were updated
    item = response["Item"]
    assert "status" in item, "Status field is missing"
    assert item["status"]["S"] == "invalid", "Status was not updated correctly"
    assert "reasoning" in item, "Reasoning field is missing"
    assert (
        item["reasoning"]["S"] == "Updated reasoning after review"
    ), "Reasoning was not updated correctly"

    # Clean up
    client._client.delete_item(
        TableName=dynamodb_table,
        Key=updated_category.key,
    )


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "category cannot be None"),
        (
            "not a ReceiptValidationCategory",
            "category must be an instance of the ReceiptValidationCategory class.",
        ),
    ],
)
def test_updateReceiptValidationCategory_invalid_parameters(
    dynamodb_table,
    sample_receipt_validation_category,
    mocker,
    invalid_input,
    expected_error,
):
    """
    Tests that updateReceiptValidationCategory validates input parameters correctly.

    Args:
        dynamodb_table: The mocked DynamoDB table name.
        sample_receipt_validation_category: A sample ReceiptValidationCategory for testing.
        mocker: Pytest mocker fixture.
        invalid_input: Invalid input parameter.
        expected_error: Expected error message.
    """
    # Setup
    client = DynamoClient(table_name=dynamodb_table)
    mock_put_item = mocker.patch.object(client._client, "put_item")

    # Execute and Assert
    with pytest.raises(ValueError, match=expected_error):
        client.update_receipt_validation_category(invalid_input)

    # Verify that put_item was not called
    mock_put_item.assert_not_called()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error",
    [
        (
            "ConditionalCheckFailedException",
            "Item does not exist",
            "Entity does not exist: ReceiptValidationCategory with receipt_id=1",
        ),
        (
            "ProvisionedThroughputExceededException",
            "Provisioned throughput exceeded",
            "Provisioned throughput exceeded",
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
        ),
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found for operation update_receipt_validation_category",
        ),
        (
            "ValidationException",
            "One or more parameters given were invalid",
            "One or more parameters given were invalid",
        ),
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied",
        ),
        (
            "UnknownError",
            "Unknown error occurred",
            "Could not update receipt in DynamoDB",
        ),
    ],
)
def test_updateReceiptValidationCategory_client_errors(
    dynamodb_table,
    sample_receipt_validation_category,
    mocker,
    error_code,
    error_message,
    expected_error,
):
    """
    Tests that updateReceiptValidationCategory handles client errors appropriately.

    Args:
        dynamodb_table: The mocked DynamoDB table name.
        sample_receipt_validation_category: A sample ReceiptValidationCategory for testing.
        mocker: Pytest mocker fixture.
        error_code: AWS error code to simulate.
        error_message: AWS error message to simulate.
        expected_error: Expected exception message.
    """
    # Setup
    client = DynamoClient(table_name=dynamodb_table)

    # Mock the DynamoDB client to raise the specified error
    mock_put_item = mocker.patch.object(client._client, "put_item")
    mock_put_item.side_effect = ClientError(
        {"Error": {"Code": error_code, "Message": error_message}}, "PutItem"
    )

    # Execute and Assert
    with pytest.raises(Exception, match=expected_error):
        client.update_receipt_validation_category(
            sample_receipt_validation_category
        )


@pytest.mark.integration
def test_updateReceiptValidationCategories_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_category: ReceiptValidationCategory,
):
    """
    Tests that updateReceiptValidationCategories correctly updates multiple categories.

    Args:
        dynamodb_table: The mocked DynamoDB table name.
        sample_receipt_validation_category: A sample ReceiptValidationCategory for testing.
    """
    # Setup
    client = DynamoClient(table_name=dynamodb_table)

    # Create multiple categories
    categories = [
        sample_receipt_validation_category,
        ReceiptValidationCategory(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            field_name="payment_method",
            field_category="payment",
            status="valid",
            reasoning="The payment method is valid",
            result_summary={"valid": 2, "invalid": 0},
            validation_timestamp="2023-05-15T12:34:56.789Z",
            metadata={"confidence": 0.9},
        ),
    ]

    # Add all categories first
    for category in categories:
        client.add_receipt_validation_category(category)

    # Create updated versions
    updated_categories = [
        ReceiptValidationCategory(
            receipt_id=categories[0].receipt_id,
            image_id=categories[0].image_id,
            field_name=categories[0].field_name,
            field_category=categories[0].field_category,
            status="invalid",  # Changed status
            reasoning="Updated reasoning for first category",
            result_summary={"valid": 1, "invalid": 2},
            validation_timestamp="2023-05-16T10:20:30.456Z",
            metadata={"confidence": 0.85, "updated": True},
        ),
        ReceiptValidationCategory(
            receipt_id=categories[1].receipt_id,
            image_id=categories[1].image_id,
            field_name=categories[1].field_name,
            field_category=categories[1].field_category,
            status="invalid",  # Changed status
            reasoning="Updated reasoning for second category",
            result_summary={"valid": 0, "invalid": 3},
            validation_timestamp="2023-05-16T10:20:30.456Z",
            metadata={"confidence": 0.75, "updated": True},
        ),
    ]

    # Execute
    client.update_receipt_validation_categories(updated_categories)

    # Verify
    for category in updated_categories:
        response = client._client.get_item(
            TableName=dynamodb_table,
            Key=category.key,
        )
        assert (
            "Item" in response
        ), f"Category {category.field_name} was not found in the table."
        item = response["Item"]
        assert (
            item["status"]["S"] == "invalid"
        ), f"Category {category.field_name} status was not updated correctly"

    # Clean up
    for category in updated_categories:
        client._client.delete_item(
            TableName=dynamodb_table,
            Key=category.key,
        )


@pytest.mark.integration
def test_updateReceiptValidationCategories_with_large_batch(
    dynamodb_table, sample_receipt_validation_category
):
    """
    Tests that updateReceiptValidationCategories correctly handles large batches by splitting them.

    Args:
        dynamodb_table: The mocked DynamoDB table name.
        sample_receipt_validation_category: A sample ReceiptValidationCategory for testing.
    """
    # Setup
    client = DynamoClient(table_name=dynamodb_table)

    # Create 30 categories (more than the 25 batch limit)
    categories = []
    for i in range(30):
        category = ReceiptValidationCategory(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            field_name=f"field_{i}",
            field_category="test_category",
            status="valid",
            reasoning=f"Test reasoning {i}",
            result_summary={"valid": i, "invalid": 0},
            validation_timestamp="2023-05-15T12:34:56.789Z",
            metadata={"index": i},
        )
        categories.append(category)
        # Add each category first
        client.add_receipt_validation_category(category)

    # Create updated versions
    updated_categories = []
    for i, category in enumerate(categories):
        updated_categories.append(
            ReceiptValidationCategory(
                receipt_id=category.receipt_id,
                image_id=category.image_id,
                field_name=category.field_name,
                field_category=category.field_category,
                status="updated",
                reasoning=f"Updated reasoning {i}",
                result_summary={"valid": 0, "invalid": i},
                validation_timestamp="2023-05-16T10:20:30.456Z",
                metadata={"index": i, "updated": True},
            )
        )

    # Execute
    client.update_receipt_validation_categories(updated_categories)

    # Verify
    for category in updated_categories:
        response = client._client.get_item(
            TableName=dynamodb_table,
            Key=category.key,
        )
        assert (
            "Item" in response
        ), f"Category {category.field_name} was not found in the table."
        item = response["Item"]
        assert (
            item["status"]["S"] == "updated"
        ), f"Category {category.field_name} status was not updated correctly"

    # Clean up
    for category in updated_categories:
        client._client.delete_item(
            TableName=dynamodb_table,
            Key=category.key,
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "categories cannot be None"),
        (
            "not-a-list",
            "categories must be a list of ReceiptValidationCategory instances.",
        ),
        (
            [123, "not-a-validation-category"],
            "All categories must be instances of the ReceiptValidationCategory class.",
        ),
    ],
)
def test_updateReceiptValidationCategories_invalid_inputs(
    dynamodb_table,
    sample_receipt_validation_category,
    mocker,
    invalid_input,
    expected_error,
):
    """
    Tests that updateReceiptValidationCategories validates input parameters correctly.

    Args:
        dynamodb_table: The mocked DynamoDB table name.
        sample_receipt_validation_category: A sample ReceiptValidationCategory for testing.
        mocker: Pytest mocker fixture.
        invalid_input: Invalid input parameter.
        expected_error: Expected error message.
    """
    # Setup
    client = DynamoClient(table_name=dynamodb_table)
    mock_transact_write = mocker.patch.object(
        client._client, "transact_write_items"
    )

    # Execute and Assert
    with pytest.raises(ValueError, match=expected_error):
        client.update_receipt_validation_categories(invalid_input)

    # Verify that transact_write_items was not called
    mock_transact_write.assert_not_called()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error,cancellation_reasons,exception_type",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found for operation update_receipt_validation_categories",
            None,
            DynamoDBError,
        ),
        (
            "TransactionCanceledException",
            "Transaction canceled due to ConditionalCheckFailed",
            "One or more entities do not exist or conditions failed",
            [{"Code": "ConditionalCheckFailed"}],
            ValueError,
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
            None,
            DynamoDBServerError,
        ),
        (
            "ProvisionedThroughputExceededException",
            "Provisioned throughput exceeded",
            "Provisioned throughput exceeded",
            None,
            Exception,  # Still Exception in the test
        ),
        (
            "ValidationException",
            "One or more parameters given were invalid",
            "One or more parameters given were invalid",
            None,
            DynamoDBValidationError,
        ),
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied",
            None,
            DynamoDBAccessError,
        ),
        (
            "UnknownError",
            "Unknown error occurred",
            "Could not update receipt in DynamoDB",
            None,
            DynamoDBError,
        ),
    ],
)
def test_updateReceiptValidationCategories_client_errors(
    dynamodb_table,
    sample_receipt_validation_category,
    mocker,
    error_code,
    error_message,
    expected_error,
    cancellation_reasons,
    exception_type,
):
    """
    Tests that updateReceiptValidationCategories handles client errors correctly.

    Args:
        dynamodb_table: The mocked DynamoDB table name.
        sample_receipt_validation_category: A sample ReceiptValidationCategory for testing.
        mocker: Pytest mocker fixture.
        error_code: AWS error code to simulate.
        error_message: AWS error message to simulate.
        expected_error: Expected exception message.
        cancellation_reasons: Transaction cancellation reasons for TransactionCanceledException.
    """
    # Setup
    client = DynamoClient(table_name=dynamodb_table)
    mock_transact_write = mocker.patch.object(
        client._client, "transact_write_items"
    )

    # Create a ClientError with the necessary structure
    error_response = {"Error": {"Code": error_code, "Message": error_message}}

    # Add cancellation reasons for TransactionCanceledException
    if cancellation_reasons:
        error_response["CancellationReasons"] = cancellation_reasons

    mock_transact_write.side_effect = ClientError(
        error_response, "TransactWriteItems"
    )

    # Execute and Assert
    with pytest.raises(exception_type, match=expected_error):
        client.update_receipt_validation_categories(
            [sample_receipt_validation_category]
        )


@pytest.mark.integration
def test_deleteReceiptValidationCategory_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_category: ReceiptValidationCategory,
):
    """
    Tests that deleteReceiptValidationCategory correctly deletes a category.

    Args:
        dynamodb_table: The mocked DynamoDB table name.
        sample_receipt_validation_category: A sample ReceiptValidationCategory for testing.
    """
    # Setup
    client = DynamoClient(table_name=dynamodb_table)

    # Add the category first
    client.add_receipt_validation_category(sample_receipt_validation_category)

    # Verify it was added
    response = client._client.get_item(
        TableName=dynamodb_table,
        Key=sample_receipt_validation_category.key,
    )
    assert "Item" in response, "Item was not added to the table."

    # Execute
    client.delete_receipt_validation_category(
        sample_receipt_validation_category
    )

    # Verify it was deleted
    response = client._client.get_item(
        TableName=dynamodb_table,
        Key=sample_receipt_validation_category.key,
    )
    assert "Item" not in response, "Item was not deleted from the table."


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "category cannot be None"),
        (
            "not-a-validation-category",
            "category must be an instance of the ReceiptValidationCategory class.",
        ),
    ],
)
def test_deleteReceiptValidationCategory_invalid_parameters(
    dynamodb_table,
    sample_receipt_validation_category,
    mocker,
    invalid_input,
    expected_error,
):
    """
    Tests that deleteReceiptValidationCategory validates input parameters correctly.

    Args:
        dynamodb_table: The mocked DynamoDB table name.
        sample_receipt_validation_category: A sample ReceiptValidationCategory for testing.
        mocker: Pytest mocker fixture.
        invalid_input: Invalid input parameter.
        expected_error: Expected error message.
    """
    # Setup
    client = DynamoClient(table_name=dynamodb_table)
    mock_delete_item = mocker.patch.object(client._client, "delete_item")

    # Execute and Assert
    with pytest.raises(ValueError, match=expected_error):
        client.delete_receipt_validation_category(invalid_input)

    # Verify that delete_item was not called
    mock_delete_item.assert_not_called()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error",
    [
        (
            "ConditionalCheckFailedException",
            "Item does not exist",
            "Entity does not exist: ReceiptValidationCategory with receipt_id=1",
        ),
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found for operation delete_receipt_validation_category",
        ),
        (
            "ProvisionedThroughputExceededException",
            "Provisioned throughput exceeded",
            "Provisioned throughput exceeded",
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
        ),
        (
            "ValidationException",
            "One or more parameters given were invalid",
            "One or more parameters given were invalid",
        ),
        ("AccessDeniedException", "Access denied", "Access denied"),
        (
            "UnknownError",
            "Unknown error occurred",
            "Could not delete receipt from DynamoDB",
        ),
    ],
)
def test_deleteReceiptValidationCategory_client_errors(
    dynamodb_table,
    sample_receipt_validation_category,
    mocker,
    error_code,
    error_message,
    expected_error,
):
    """
    Tests that deleteReceiptValidationCategory handles client errors appropriately.

    Args:
        dynamodb_table: The mocked DynamoDB table name.
        sample_receipt_validation_category: A sample ReceiptValidationCategory for testing.
        mocker: Pytest mocker fixture.
        error_code: AWS error code to simulate.
        error_message: AWS error message to simulate.
        expected_error: Expected error message.
    """
    # Setup
    client = DynamoClient(table_name=dynamodb_table)

    # Mock the DynamoDB client to raise the specified error
    mock_delete_item = mocker.patch.object(client._client, "delete_item")
    mock_delete_item.side_effect = ClientError(
        {"Error": {"Code": error_code, "Message": error_message}}, "DeleteItem"
    )

    # Execute and Assert
    with pytest.raises(Exception, match=expected_error):
        client.delete_receipt_validation_category(
            sample_receipt_validation_category
        )


@pytest.mark.integration
def test_deleteReceiptValidationCategories_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_category: ReceiptValidationCategory,
):
    """
    Tests that deleteReceiptValidationCategories correctly deletes multiple categories.

    Args:
        dynamodb_table: The mocked DynamoDB table name.
        sample_receipt_validation_category: A sample ReceiptValidationCategory for testing.
    """
    # Setup
    client = DynamoClient(table_name=dynamodb_table)

    # Create a list of categories
    categories = [
        sample_receipt_validation_category,
        ReceiptValidationCategory(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            field_name="payment_method",
            field_category="payment",
            status="valid",
            reasoning="The payment method is valid",
            result_summary={"valid": 2, "invalid": 0},
            validation_timestamp="2023-05-15T12:34:56.789Z",
            metadata={"confidence": 0.9},
        ),
        ReceiptValidationCategory(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            field_name="merchant_name",
            field_category="merchant",
            status="invalid",
            reasoning="The merchant name could not be validated",
            result_summary={"valid": 0, "invalid": 1},
            validation_timestamp="2023-05-15T12:34:56.789Z",
            metadata={"confidence": 0.7},
        ),
    ]

    # Add all categories first
    for category in categories:
        client.add_receipt_validation_category(category)

    # Execute
    client.delete_receipt_validation_categories(categories)

    # Verify
    for category in categories:
        response = client._client.get_item(
            TableName=dynamodb_table,
            Key=category.key,
        )
        assert (
            "Item" not in response
        ), f"Category {category.field_name} was not deleted from the table."


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "categories cannot be None"),
        (
            "not-a-list",
            "categories must be a list of ReceiptValidationCategory instances.",
        ),
        (
            [123, "not-a-validation-category"],
            "All categories must be instances of the ReceiptValidationCategory class.",
        ),
    ],
)
def test_deleteReceiptValidationCategories_invalid_parameters(
    dynamodb_table,
    sample_receipt_validation_category,
    mocker,
    invalid_input,
    expected_error,
):
    """
    Tests that deleteReceiptValidationCategories validates input parameters correctly.

    Args:
        dynamodb_table: The mocked DynamoDB table name.
        sample_receipt_validation_category: A sample ReceiptValidationCategory for testing.
        mocker: Pytest mocker fixture.
        invalid_input: Invalid input parameter.
        expected_error: Expected error message.
    """
    # Setup
    client = DynamoClient(table_name=dynamodb_table)
    mock_batch_write = mocker.patch.object(client._client, "batch_write_item")

    # Execute and Assert
    with pytest.raises(ValueError, match=expected_error):
        client.delete_receipt_validation_categories(invalid_input)

    # Verify that batch_write_item was not called
    mock_batch_write.assert_not_called()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found for operation delete_receipt_validation_categories",
        ),
        (
            "ProvisionedThroughputExceededException",
            "Throughput exceeded",
            "Provisioned throughput exceeded",
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
        ),
        (
            "ValidationException",
            "One or more parameters given were invalid",
            "One or more parameters given were invalid",
        ),
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied",
        ),
        (
            "UnknownError",
            "Unknown error occurred",
            "Could not delete receipt validation category from DynamoDB",
        ),
    ],
)
@pytest.mark.integration
@pytest.mark.skip(
    reason="Test structure issue: This is named as a 'success' test but has error parameters. "
    "Appears to be a copy-paste error where error test parameters were applied to a success test. "
    "Should be refactored into separate success and error tests."
)
def test_listReceiptValidationCategoriesForReceipt_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_category: ReceiptValidationCategory,
    mocker,
    error_code,
    error_message,
    expected_error,
):
    """
    Tests that listReceiptValidationCategoriesForReceipt correctly lists categories for a receipt.

    Args:
        dynamodb_table: The mocked DynamoDB table name.
        sample_receipt_validation_category: A sample ReceiptValidationCategory for testing.
        mocker: Pytest mocker fixture.
    """
    # Setup
    client = DynamoClient(table_name=dynamodb_table)
    receipt_id = sample_receipt_validation_category.receipt_id
    image_id = sample_receipt_validation_category.image_id

    # Mock the query response
    mock_query = mocker.patch.object(client._client, "query")

    # Create sample response
    mock_query.return_value = {
        "Items": [
            sample_receipt_validation_category.to_item(),
            {
                "PK": {"S": f"IMAGE#{image_id}"},
                "SK": {
                    "S": f"RECEIPT#{receipt_id}#ANALYSIS#VALIDATION#CATEGORY#payment_method"
                },
                "GSI1PK": {"S": "ANALYSIS_TYPE"},
                "GSI1SK": {
                    "S": "VALIDATION#2023-05-15T13:14:15.678Z#CATEGORY#payment_method"
                },
                "field_category": {"S": "payment"},
                "status": {"S": "valid"},
                "reasoning": {"S": "The payment method is valid"},
                "result_summary": {
                    "M": {"valid": {"N": "2"}, "invalid": {"N": "0"}}
                },
                "validation_timestamp": {"S": "2023-05-15T13:14:15.678Z"},
                "metadata": {"M": {"confidence": {"N": "0.9"}}},
            },
        ]
    }

    # Execute
    results, last_evaluated_key = (
        client.list_receipt_validation_categories_for_receipt(
            receipt_id=receipt_id,
            image_id=image_id,
        )
    )

    # Verify
    assert len(results) == 2, "Should return 2 categories"
    assert all(
        isinstance(result, ReceiptValidationCategory) for result in results
    ), "All results should be ReceiptValidationCategory instances"
    assert (
        results[0].field_name == sample_receipt_validation_category.field_name
    ), "First result should match sample"
    assert (
        results[1].field_name == "payment_method"
    ), "Second result should match mock data"

    # Verify the query parameters
    mock_query.assert_called_once()
    args, kwargs = mock_query.call_args
    assert "TableName" in kwargs, "Should specify table name"
    assert (
        kwargs["TableName"] == dynamodb_table
    ), "Should use the correct table name"
    assert (
        "KeyConditionExpression" in kwargs
    ), "Should have key condition expression"
    assert (
        "PK = :pkVal AND begins_with(SK, :skPrefix)"
        in kwargs["KeyConditionExpression"]
    ), "Should have correct key condition"


@pytest.mark.integration
def test_listReceiptValidationCategoriesForReceipt_with_pagination(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_category: ReceiptValidationCategory,
    mocker,
):
    """
    Tests that listReceiptValidationCategoriesForReceipt correctly handles pagination.

    Args:
        dynamodb_table: The mocked DynamoDB table name.
        sample_receipt_validation_category: A sample ReceiptValidationCategory for testing.
        mocker: Pytest mocker fixture.
    """
    # Setup
    client = DynamoClient(table_name=dynamodb_table)
    receipt_id = sample_receipt_validation_category.receipt_id
    image_id = sample_receipt_validation_category.image_id

    # Mock the query response
    mock_query = mocker.patch.object(client._client, "query")

    # Create sample responses with pagination
    last_evaluated_key = {
        "PK": {"S": f"IMAGE#{image_id}"},
        "SK": {
            "S": f"RECEIPT#{receipt_id}#ANALYSIS#VALIDATION#CATEGORY#some-field"
        },
    }
    mock_query.side_effect = [
        {
            "Items": [sample_receipt_validation_category.to_item()],
            "LastEvaluatedKey": last_evaluated_key,
        },
        {
            "Items": [
                {
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {
                        "S": f"RECEIPT#{receipt_id}#ANALYSIS#VALIDATION#CATEGORY#payment_method"
                    },
                    "GSI1PK": {"S": "ANALYSIS_TYPE"},
                    "GSI1SK": {
                        "S": "VALIDATION#2023-05-15T13:14:15.678Z#CATEGORY#payment_method"
                    },
                    "field_category": {"S": "payment"},
                    "status": {"S": "valid"},
                    "reasoning": {"S": "The payment method is valid"},
                    "result_summary": {
                        "M": {"valid": {"N": "2"}, "invalid": {"N": "0"}}
                    },
                    "validation_timestamp": {"S": "2023-05-15T13:14:15.678Z"},
                    "metadata": {"M": {"confidence": {"N": "0.9"}}},
                }
            ],
        },
    ]

    # Execute with pagination
    results, _ = client.list_receipt_validation_categories_for_receipt(
        receipt_id=receipt_id,
        image_id=image_id,
    )

    # Verify
    assert len(results) == 2, "Should return 2 categories in total"
    assert all(
        isinstance(result, ReceiptValidationCategory) for result in results
    ), "All results should be ReceiptValidationCategory instances"
    assert (
        results[0].field_name == sample_receipt_validation_category.field_name
    ), "First result should match sample"
    assert (
        results[1].field_name == "payment_method"
    ), "Second result should match mock data"

    # Verify the query parameters for pagination
    assert (
        mock_query.call_count == 2
    ), "Query should be called twice for pagination"
    _, second_call_kwargs = mock_query.call_args_list[1]
    assert (
        "ExclusiveStartKey" in second_call_kwargs
    ), "Second call should include LastEvaluatedKey"
    assert (
        second_call_kwargs["ExclusiveStartKey"] == last_evaluated_key
    ), "Should use the correct LastEvaluatedKey"


@pytest.mark.integration
def test_listReceiptValidationCategoriesForReceipt_empty_results(
    dynamodb_table: Literal["MyMockedTable"],
    mocker,
):
    """
    Tests that listReceiptValidationCategoriesForReceipt correctly handles empty results.

    Args:
        dynamodb_table: The mocked DynamoDB table name.
        mocker: Pytest mocker fixture.
    """
    # Setup
    client = DynamoClient(table_name=dynamodb_table)
    receipt_id = 1
    image_id = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"

    # Mock the query response to return no items
    mock_query = mocker.patch.object(client._client, "query")
    mock_query.return_value = {"Items": []}

    # Execute
    results, last_evaluated_key = (
        client.list_receipt_validation_categories_for_receipt(
            receipt_id=receipt_id,
            image_id=image_id,
        )
    )

    # Verify
    assert len(results) == 0, "Should return empty list"
    assert last_evaluated_key is None, "Last evaluated key should be None"


@pytest.mark.integration
@pytest.mark.parametrize(
    "receipt_id,image_id,expected_error",
    [
        (
            None,
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "receipt_id must be an integer, got NoneType",
        ),
        (1, None, "image_id must be a string, got NoneType"),
        (1, "", "uuid must be a valid UUIDv4"),
    ],
)
def test_listReceiptValidationCategoriesForReceipt_invalid_parameters(
    dynamodb_table: Literal["MyMockedTable"],
    receipt_id,
    image_id,
    expected_error,
):
    """
    Tests that listReceiptValidationCategoriesForReceipt validates input parameters correctly.

    Args:
        dynamodb_table: The mocked DynamoDB table name.
        receipt_id: Receipt ID to test.
        image_id: Image ID to test.
        expected_error: Expected error message.
    """
    # Setup
    client = DynamoClient(table_name=dynamodb_table)

    # Execute and Assert
    with pytest.raises(Exception, match=expected_error):
        client.list_receipt_validation_categories_for_receipt(
            receipt_id=receipt_id,
            image_id=image_id,
        )


@pytest.mark.integration
def test_listReceiptValidationCategoriesForReceipt_with_invalid_limit(
    dynamodb_table: Literal["MyMockedTable"],
):
    """
    Tests that listReceiptValidationCategoriesForReceipt validates the limit parameter.

    Args:
        dynamodb_table: The mocked DynamoDB table name.
    """
    # Setup
    client = DynamoClient(table_name=dynamodb_table)
    receipt_id = 1
    image_id = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"

    # Execute and Assert
    with pytest.raises(ValueError, match="limit must be an integer or None"):
        client.list_receipt_validation_categories_for_receipt(
            receipt_id=receipt_id, image_id=image_id, limit="not-an-integer"  # type: ignore[arg-type]
        )

    with pytest.raises(
        ValueError, match="last_evaluated_key must be a dictionary or None"
    ):
        client.list_receipt_validation_categories_for_receipt(
            receipt_id=receipt_id,
            image_id=image_id,
            last_evaluated_key="not-a-dict",  # type: ignore[arg-type]
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found for operation list_receipt_validation_categories_for_receipt",
        ),
        (
            "ProvisionedThroughputExceededException",
            "Throughput exceeded",
            "Provisioned throughput exceeded",
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
        ),
        (
            "ValidationException",
            "One or more parameters given were invalid",
            "One or more parameters given were invalid",
        ),
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied",
        ),
        (
            "UnknownError",
            "Unknown error occurred",
            "Could not list receipt from DynamoDB",
        ),
    ],
)
def test_listReceiptValidationCategoriesForReceipt_client_errors(
    dynamodb_table,
    mocker,
    error_code,
    error_message,
    expected_error,
):
    """
    Tests that listReceiptValidationCategoriesForReceipt handles client errors correctly.

    Args:
        dynamodb_table: The mocked DynamoDB table name.
        mocker: Pytest mocker fixture.
        error_code: AWS error code to simulate.
        error_message: AWS error message to simulate.
        expected_error: Expected exception message.
    """
    # Setup
    client = DynamoClient(table_name=dynamodb_table)
    receipt_id = 1
    image_id = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"

    # Mock the query to raise an error
    mock_query = mocker.patch.object(client._client, "query")
    mock_query.side_effect = ClientError(
        {"Error": {"Code": error_code, "Message": error_message}}, "Query"
    )

    # Execute and Assert
    with pytest.raises(Exception, match=expected_error):
        client.list_receipt_validation_categories_for_receipt(
            receipt_id=receipt_id,
            image_id=image_id,
        )
