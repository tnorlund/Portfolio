import uuid
from copy import deepcopy
from typing import Any, Dict, List, Literal, Optional, Type
from unittest.mock import MagicMock, call, patch

import pytest
from botocore.exceptions import ClientError, ParamValidationError

from receipt_dynamo import (
    ReceiptValidationResult,
    item_to_receipt_validation_result,
)
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBAccessError,
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBValidationError,
    EntityAlreadyExistsError,
)


@pytest.fixture
def sample_receipt_validation_result():
    """
    Creates a sample ReceiptValidationResult for testing.
    """
    return ReceiptValidationResult(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        field_name="total_amount",
        result_index=0,
        type="error",
        message="Total amount does not match sum of items",
        reasoning="The total ($45.99) does not equal the sum of line items ($42.99)",
        field="price",
        expected_value="42.99",
        actual_value="45.99",
        validation_timestamp="2023-05-15T10:30:00",
        metadata={
            "source_info": {"model": "validation-v1"},
            "confidence": 0.92,
        },
    )


# Now let's implement test for addReceiptValidationResult
@pytest.mark.integration
def test_addReceiptValidationResult_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_result: ReceiptValidationResult,
):
    """Test adding a validation result successfully"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act
    client.add_receipt_validation_result(sample_receipt_validation_result)

    # Assert
    # Verify the item was added by retrieving it
    response = client._client.get_item(
        TableName=dynamodb_table,
        Key={
            "PK": {"S": f"IMAGE#{sample_receipt_validation_result.image_id}"},
            "SK": {
                "S": f"RECEIPT#{sample_receipt_validation_result.receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{sample_receipt_validation_result.field_name}#RESULT#{sample_receipt_validation_result.result_index}"
            },
        },
    )

    assert "Item" in response
    result = item_to_receipt_validation_result(response["Item"])
    assert result == sample_receipt_validation_result


@pytest.mark.integration
def test_addReceiptValidationResult_duplicate_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_result: ReceiptValidationResult,
):
    """Test that adding a duplicate validation result raises an error"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Add the result first time
    client.add_receipt_validation_result(sample_receipt_validation_result)

    # Act & Assert
    with pytest.raises(
        EntityAlreadyExistsError,
        match="already exists",
    ):
        # Try to add the same result again
        client.add_receipt_validation_result(sample_receipt_validation_result)


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "result cannot be None"),
        (
            "not-a-validation-result",
            "result must be an instance of the ReceiptValidationResult class.",
        ),
    ],
)
def test_addReceiptValidationResult_invalid_parameters(
    dynamodb_table,
    sample_receipt_validation_result,
    mocker,
    invalid_input,
    expected_error,
):
    """Test adding a validation result with invalid parameters"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Mock the put_item method to avoid actual DynamoDB calls for invalid inputs
    mocker.patch.object(client._client, "put_item")

    # Act & Assert
    with pytest.raises(ValueError, match=expected_error):
        client.add_receipt_validation_result(invalid_input)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_exception",
    [
        (
            "ConditionalCheckFailedException",
            "Item already exists",
            "already exists",
        ),
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found",
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
            "Could not add receipt validation result to DynamoDB",
        ),
        (
            "ValidationException",
            "One or more parameters given were invalid",
            "One or more parameters given were invalid",
        ),
        ("AccessDeniedException", "Access denied", "Access denied"),
    ],
)
def test_addReceiptValidationResult_client_errors(
    dynamodb_table,
    sample_receipt_validation_result,
    mocker,
    error_code,
    error_message,
    expected_exception,
):
    """Test handling of client errors when adding a validation result"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Create a ClientError with the specified error code
    client_error = ClientError(
        {
            "Error": {
                "Code": error_code,
                "Message": error_message,
            }
        },
        "PutItem",
    )

    # Mock the put_item method to raise the client error
    mocker.patch.object(client._client, "put_item", side_effect=client_error)

    # Act & Assert
    with pytest.raises(Exception, match=expected_exception):
        client.add_receipt_validation_result(sample_receipt_validation_result)


# Now let's implement tests for addReceiptValidationResults
@pytest.mark.integration
def test_addReceiptValidationResults_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_result: ReceiptValidationResult,
):
    """Test adding multiple validation results successfully"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Create a few different validation results
    result1 = sample_receipt_validation_result

    result2 = deepcopy(result1)
    result2.result_index = 1
    result2.message = "Different message"

    result3 = deepcopy(result1)
    result3.result_index = 2
    result3.field_name = "subtotal"

    validation_results = [result1, result2, result3]

    # Act
    client.add_receipt_validation_results(validation_results)

    # Assert
    # Verify each result was added correctly
    for result in validation_results:
        response = client._client.get_item(
            TableName=dynamodb_table,
            Key={
                "PK": {"S": f"IMAGE#{result.image_id}"},
                "SK": {
                    "S": f"RECEIPT#{result.receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{result.field_name}#RESULT#{result.result_index}"
                },
            },
        )
        assert "Item" in response
        retrieved_result = item_to_receipt_validation_result(response["Item"])
        assert retrieved_result == result


@pytest.mark.integration
def test_addReceiptValidationResults_with_large_batch(
    dynamodb_table, sample_receipt_validation_result
):
    """Test adding a large batch of validation results (more than 25)"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Create 30 different validation results (which exceeds the batch limit of 25)
    validation_results = []
    for i in range(30):
        result = deepcopy(sample_receipt_validation_result)
        result.result_index = i
        validation_results.append(result)

    # Act
    client.add_receipt_validation_results(validation_results)

    # Assert
    # Verify each result was added correctly
    for result in validation_results:
        response = client._client.get_item(
            TableName=dynamodb_table,
            Key={
                "PK": {"S": f"IMAGE#{result.image_id}"},
                "SK": {
                    "S": f"RECEIPT#{result.receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{result.field_name}#RESULT#{result.result_index}"
                },
            },
        )
        assert "Item" in response
        retrieved_result = item_to_receipt_validation_result(response["Item"])
        assert retrieved_result == result


@pytest.mark.integration
def test_addReceiptValidationResults_with_unprocessed_items_retries(
    dynamodb_table, sample_receipt_validation_result, mocker
):
    """Test that unprocessed items are retried when adding validation results"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Create 5 different validation results
    validation_results = []
    for i in range(5):
        result = deepcopy(sample_receipt_validation_result)
        result.result_index = i
        validation_results.append(result)

    # Mock batch_write_item to simulate unprocessed items on first call
    original_batch_write = client._client.batch_write_item

    # Create a side effect that returns unprocessed items on first call, then succeeds
    def batch_write_side_effect(*args, **kwargs):
        if batch_write_side_effect.call_count == 0:
            batch_write_side_effect.call_count += 1
            # Return unprocessed items on first call
            return {
                "UnprocessedItems": {
                    dynamodb_table: [
                        {
                            "PutRequest": {
                                "Item": validation_results[0].to_item()
                            }
                        }
                    ]
                }
            }
        else:
            # Return empty unprocessed items on subsequent calls
            return {"UnprocessedItems": {}}

    batch_write_side_effect.call_count = 0

    mock_batch_write = mocker.patch.object(
        client._client, "batch_write_item", side_effect=batch_write_side_effect
    )

    # Act
    client.add_receipt_validation_results(validation_results)

    # Assert
    # Verify batch_write_item was called at least twice (once for initial write, once for retry)
    assert mock_batch_write.call_count >= 2


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "results cannot be None"),
        (
            "not-a-list",
            "results must be a list",
        ),
        (
            ["not-a-validation-result"],
            "results must be a list of ReceiptValidationResult instances.",
        ),
    ],
)
def test_addReceiptValidationResults_invalid_parameters(
    dynamodb_table,
    sample_receipt_validation_result,
    mocker,
    invalid_input,
    expected_error,
):
    """Test adding validation results with invalid parameters"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Mock batch_write_item to avoid actual DynamoDB calls for invalid inputs
    mocker.patch.object(client._client, "batch_write_item")

    # Act & Assert
    with pytest.raises(ValueError, match=expected_error):
        client.add_receipt_validation_results(invalid_input)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error_message",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found",
        ),
        (
            "ProvisionedThroughputExceededException",
            "Throughput exceeded",
            "Throughput exceeded",
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
            "Could not add receipt validation result to DynamoDB",
        ),
    ],
)
def test_addReceiptValidationResults_client_errors(
    dynamodb_table,
    sample_receipt_validation_result,
    mocker,
    error_code,
    error_message,
    expected_error_message,
):
    """Test handling of client errors when adding multiple validation results"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Create 3 different validation results
    validation_results = []
    for i in range(3):
        result = deepcopy(sample_receipt_validation_result)
        result.result_index = i
        validation_results.append(result)

    # Create a ClientError with the specified error code
    client_error = ClientError(
        {
            "Error": {
                "Code": error_code,
                "Message": error_message,
            }
        },
        "BatchWriteItem",
    )

    # Mock batch_write_item to raise the client error
    mocker.patch.object(
        client._client, "batch_write_item", side_effect=client_error
    )

    # Act & Assert
    with pytest.raises(Exception, match=expected_error_message):
        client.add_receipt_validation_results(validation_results)


# Now let's implement tests for updateReceiptValidationResult
@pytest.mark.integration
def test_updateReceiptValidationResult_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_result: ReceiptValidationResult,
):
    """Test updating a validation result successfully"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # First add the result
    client.add_receipt_validation_result(sample_receipt_validation_result)

    # Modify the result
    updated_result = deepcopy(sample_receipt_validation_result)
    updated_result.message = "Updated message for testing"
    updated_result.reasoning = "Updated reasoning for testing"

    # Act
    client.update_receipt_validation_result(updated_result)

    # Assert
    # Verify the item was updated correctly
    response = client._client.get_item(
        TableName=dynamodb_table,
        Key={
            "PK": {"S": f"IMAGE#{updated_result.image_id}"},
            "SK": {
                "S": f"RECEIPT#{updated_result.receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{updated_result.field_name}#RESULT#{updated_result.result_index}"
            },
        },
    )

    assert "Item" in response
    result = item_to_receipt_validation_result(response["Item"])
    assert result == updated_result
    assert result.message == "Updated message for testing"
    assert result.reasoning == "Updated reasoning for testing"


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "result cannot be None"),
        (
            "not a ReceiptValidationResult",
            "result must be an instance of the ReceiptValidationResult class.",
        ),
    ],
)
def test_updateReceiptValidationResult_invalid_parameters(
    dynamodb_table,
    sample_receipt_validation_result,
    mocker,
    invalid_input,
    expected_error,
):
    """Test updating a validation result with invalid parameters"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Mock the put_item method to avoid actual DynamoDB calls for invalid inputs
    mocker.patch.object(client._client, "put_item")

    # Act & Assert
    with pytest.raises(ValueError, match=expected_error):
        client.update_receipt_validation_result(invalid_input)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error",
    [
        (
            "ConditionalCheckFailedException",
            "Item does not exist",
            "does not exist",
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
            "Table not found",
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
            "Could not update receipt validation result in DynamoDB",
        ),
    ],
)
def test_updateReceiptValidationResult_client_errors(
    dynamodb_table,
    sample_receipt_validation_result,
    mocker,
    error_code,
    error_message,
    expected_error,
):
    """Test handling of client errors when updating a validation result"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Create a ClientError with the specified error code
    client_error = ClientError(
        {
            "Error": {
                "Code": error_code,
                "Message": error_message,
            }
        },
        "PutItem",
    )

    # Mock the put_item method to raise the client error
    mocker.patch.object(client._client, "put_item", side_effect=client_error)

    # Act & Assert
    with pytest.raises(Exception, match=expected_error):
        client.update_receipt_validation_result(
            sample_receipt_validation_result
        )


# Now let's implement tests for updateReceiptValidationResults
@pytest.mark.integration
def test_updateReceiptValidationResults_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_result: ReceiptValidationResult,
):
    """Test updating multiple validation results successfully"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Create a few different validation results
    result1 = sample_receipt_validation_result

    result2 = deepcopy(result1)
    result2.result_index = 1
    result2.message = "Second message"

    result3 = deepcopy(result1)
    result3.result_index = 2
    result3.field_name = "subtotal"

    validation_results = [result1, result2, result3]

    # First add all the results
    client.add_receipt_validation_results(validation_results)

    # Create updated versions
    updated_results = []
    for i, result in enumerate(validation_results):
        updated = deepcopy(result)
        updated.message = f"Updated message {i}"
        updated.reasoning = f"Updated reasoning {i}"
        updated_results.append(updated)

    # Act
    client.update_receipt_validation_results(updated_results)

    # Assert
    # Verify each result was updated correctly
    for i, result in enumerate(updated_results):
        response = client._client.get_item(
            TableName=dynamodb_table,
            Key={
                "PK": {"S": f"IMAGE#{result.image_id}"},
                "SK": {
                    "S": f"RECEIPT#{result.receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{result.field_name}#RESULT#{result.result_index}"
                },
            },
        )
        assert "Item" in response
        retrieved_result = item_to_receipt_validation_result(response["Item"])
        assert retrieved_result.message == f"Updated message {i}"
        assert retrieved_result.reasoning == f"Updated reasoning {i}"


@pytest.mark.integration
def test_updateReceiptValidationResults_with_large_batch(
    dynamodb_table, sample_receipt_validation_result
):
    """Test updating a large batch of validation results (more than 25)"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Create 30 different validation results (which exceeds the batch limit of 25)
    validation_results = []
    for i in range(30):
        result = deepcopy(sample_receipt_validation_result)
        result.result_index = i
        validation_results.append(result)

    # First add all the results
    client.add_receipt_validation_results(validation_results)

    # Create updated versions
    updated_results = []
    for i, result in enumerate(validation_results):
        updated = deepcopy(result)
        updated.message = f"Large batch updated message {i}"
        updated_results.append(updated)

    # Act
    client.update_receipt_validation_results(updated_results)

    # Assert
    # Verify each result was updated correctly (check a sample)
    for i in [0, 15, 29]:  # Check first, middle, and last
        result = updated_results[i]
        response = client._client.get_item(
            TableName=dynamodb_table,
            Key={
                "PK": {"S": f"IMAGE#{result.image_id}"},
                "SK": {
                    "S": f"RECEIPT#{result.receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{result.field_name}#RESULT#{result.result_index}"
                },
            },
        )
        assert "Item" in response
        retrieved_result = item_to_receipt_validation_result(response["Item"])
        assert retrieved_result.message == f"Large batch updated message {i}"


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "results cannot be None"),
        (
            "not-a-list",
            "results must be a list",
        ),
        (
            [123, "not-a-validation-result"],
            "results must be a list of ReceiptValidationResult instances",
        ),
    ],
)
def test_updateReceiptValidationResults_invalid_inputs(
    dynamodb_table,
    sample_receipt_validation_result,
    mocker,
    invalid_input,
    expected_error,
):
    """Test updating validation results with invalid parameters"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Mock transact_write_items to avoid actual DynamoDB calls for invalid inputs
    mocker.patch.object(client._client, "transact_write_items")

    # Act & Assert
    with pytest.raises(ValueError, match=expected_error):
        client.update_receipt_validation_results(invalid_input)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error,cancellation_reasons,exception_type",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found",
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
            "Could not update receipt validation result in DynamoDB",
            None,
            DynamoDBError,
        ),
    ],
)
def test_updateReceiptValidationResults_client_errors(
    dynamodb_table,
    sample_receipt_validation_result,
    mocker,
    error_code,
    error_message,
    expected_error,
    cancellation_reasons,
    exception_type,
):
    """Test handling of client errors when updating multiple validation results"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Create 3 different validation results
    validation_results = []
    for i in range(3):
        result = deepcopy(sample_receipt_validation_result)
        result.result_index = i
        validation_results.append(result)

    # Create a client error response
    error_response = {
        "Error": {
            "Code": error_code,
            "Message": error_message,
        }
    }

    # For TransactionCanceledException, add CancellationReasons if provided
    if cancellation_reasons:
        error_response["CancellationReasons"] = cancellation_reasons

    client_error = ClientError(error_response, "TransactWriteItems")

    # Mock transact_write_items to raise the client error
    mocker.patch.object(
        client._client, "transact_write_items", side_effect=client_error
    )

    # Act & Assert
    with pytest.raises(exception_type, match=expected_error):
        client.update_receipt_validation_results(validation_results)


# Now let's implement tests for deleteReceiptValidationResult
@pytest.mark.integration
def test_deleteReceiptValidationResult_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_result: ReceiptValidationResult,
):
    """Test deleting a validation result successfully"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # First add the result
    client.add_receipt_validation_result(sample_receipt_validation_result)

    # Verify it was added
    response = client._client.get_item(
        TableName=dynamodb_table,
        Key={
            "PK": {"S": f"IMAGE#{sample_receipt_validation_result.image_id}"},
            "SK": {
                "S": f"RECEIPT#{sample_receipt_validation_result.receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{sample_receipt_validation_result.field_name}#RESULT#{sample_receipt_validation_result.result_index}"
            },
        },
    )
    assert "Item" in response

    # Act
    client.delete_receipt_validation_result(sample_receipt_validation_result)

    # Assert
    # Verify the item was deleted
    response = client._client.get_item(
        TableName=dynamodb_table,
        Key={
            "PK": {"S": f"IMAGE#{sample_receipt_validation_result.image_id}"},
            "SK": {
                "S": f"RECEIPT#{sample_receipt_validation_result.receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{sample_receipt_validation_result.field_name}#RESULT#{sample_receipt_validation_result.result_index}"
            },
        },
    )
    assert "Item" not in response


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "result cannot be None"),
        (
            "not-a-validation-result",
            "result must be an instance of the ReceiptValidationResult class",
        ),
    ],
)
def test_deleteReceiptValidationResult_invalid_parameters(
    dynamodb_table,
    sample_receipt_validation_result,
    mocker,
    invalid_input,
    expected_error,
):
    """Test deleting a validation result with invalid parameters"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Mock delete_item to avoid actual DynamoDB calls for invalid inputs
    mocker.patch.object(client._client, "delete_item")

    # Act & Assert
    with pytest.raises(ValueError, match=expected_error):
        client.delete_receipt_validation_result(invalid_input)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error",
    [
        (
            "ConditionalCheckFailedException",
            "Item does not exist",
            "does not exist",
        ),
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found",
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
            "Could not delete receipt validation result from DynamoDB",
        ),
    ],
)
def test_deleteReceiptValidationResult_client_errors(
    dynamodb_table,
    sample_receipt_validation_result,
    mocker,
    error_code,
    error_message,
    expected_error,
):
    """Test handling of client errors when deleting a validation result"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Create a ClientError with the specified error code
    client_error = ClientError(
        {
            "Error": {
                "Code": error_code,
                "Message": error_message,
            }
        },
        "DeleteItem",
    )

    # Mock delete_item to raise the client error
    mocker.patch.object(
        client._client, "delete_item", side_effect=client_error
    )

    # Act & Assert
    with pytest.raises(Exception, match=expected_error):
        client.delete_receipt_validation_result(
            sample_receipt_validation_result
        )


# Now let's implement tests for deleteReceiptValidationResults
@pytest.mark.integration
def test_deleteReceiptValidationResults_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_result: ReceiptValidationResult,
):
    """Test deleting multiple validation results successfully"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Create a few different validation results
    result1 = sample_receipt_validation_result

    result2 = deepcopy(result1)
    result2.result_index = 1

    result3 = deepcopy(result1)
    result3.result_index = 2
    result3.field_name = "subtotal"

    validation_results = [result1, result2, result3]

    # First add all the results
    client.add_receipt_validation_results(validation_results)

    # Verify they were added
    for result in validation_results:
        response = client._client.get_item(
            TableName=dynamodb_table,
            Key={
                "PK": {"S": f"IMAGE#{result.image_id}"},
                "SK": {
                    "S": f"RECEIPT#{result.receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{result.field_name}#RESULT#{result.result_index}"
                },
            },
        )
        assert "Item" in response

    # Act
    client.delete_receipt_validation_results(validation_results)

    # Assert
    # Verify all items were deleted
    for result in validation_results:
        response = client._client.get_item(
            TableName=dynamodb_table,
            Key={
                "PK": {"S": f"IMAGE#{result.image_id}"},
                "SK": {
                    "S": f"RECEIPT#{result.receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{result.field_name}#RESULT#{result.result_index}"
                },
            },
        )
        assert "Item" not in response


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "results cannot be None"),
        (
            "not-a-list",
            "results must be a list",
        ),
        (
            [123, "not-a-validation-result"],
            "results must be a list of ReceiptValidationResult instances",
        ),
    ],
)
def test_deleteReceiptValidationResults_invalid_parameters(
    dynamodb_table,
    sample_receipt_validation_result,
    mocker,
    invalid_input,
    expected_error,
):
    """Test deleting validation results with invalid parameters"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Mock batch_write_item to avoid actual DynamoDB calls for invalid inputs
    mocker.patch.object(client._client, "batch_write_item")

    # Act & Assert
    with pytest.raises(ValueError, match=expected_error):
        client.delete_receipt_validation_results(invalid_input)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found",
        ),
        (
            "ProvisionedThroughputExceededException",
            "Throughput exceeded",
            "Throughput exceeded",
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
            "Could not delete receipt validation result from DynamoDB",
        ),
    ],
)
def test_deleteReceiptValidationResults_client_errors(
    dynamodb_table,
    sample_receipt_validation_result,
    mocker,
    error_code,
    error_message,
    expected_error,
):
    """Test handling of client errors when deleting multiple validation results"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Create 3 different validation results
    validation_results = []
    for i in range(3):
        result = deepcopy(sample_receipt_validation_result)
        result.result_index = i
        validation_results.append(result)

    # Create a ClientError with the specified error code
    client_error = ClientError(
        {
            "Error": {
                "Code": error_code,
                "Message": error_message,
            }
        },
        "BatchWriteItem",
    )

    # Mock batch_write_item to raise the client error
    mocker.patch.object(
        client._client, "batch_write_item", side_effect=client_error
    )

    # Act & Assert
    with pytest.raises(Exception, match=expected_error):
        client.delete_receipt_validation_results(validation_results)


@pytest.mark.integration
def test_deleteReceiptValidationResults_with_unprocessed_items_retries(
    dynamodb_table, sample_receipt_validation_result, mocker
):
    """Test that unprocessed items are retried when deleting validation results"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Create 5 different validation results
    validation_results = []
    for i in range(5):
        result = deepcopy(sample_receipt_validation_result)
        result.result_index = i
        validation_results.append(result)

    # First add the results so we can delete them
    client.add_receipt_validation_results(validation_results)

    # Create a side effect that returns unprocessed items on first call, then succeeds
    def batch_write_side_effect(*args, **kwargs):
        if batch_write_side_effect.call_count == 0:
            batch_write_side_effect.call_count += 1
            # Return unprocessed items on first call
            return {
                "UnprocessedItems": {
                    dynamodb_table: [
                        {"DeleteRequest": {"Key": validation_results[0].key}}
                    ]
                }
            }
        else:
            # Return empty unprocessed items on subsequent calls
            return {"UnprocessedItems": {}}

    batch_write_side_effect.call_count = 0

    mock_batch_write = mocker.patch.object(
        client._client, "batch_write_item", side_effect=batch_write_side_effect
    )

    # Act
    client.delete_receipt_validation_results(validation_results)

    # Assert
    # Verify batch_write_item was called at least twice (once for initial write, once for retry)
    assert mock_batch_write.call_count >= 2


@pytest.mark.integration
def test_deleteReceiptValidationResults_with_large_batch(
    dynamodb_table, sample_receipt_validation_result
):
    """Test deleting a large batch of validation results (more than 25)"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Create 30 different validation results (which exceeds the batch limit of 25)
    validation_results = []
    for i in range(30):
        result = deepcopy(sample_receipt_validation_result)
        result.result_index = i
        validation_results.append(result)

    # First add all the results
    client.add_receipt_validation_results(validation_results)

    # Verify they were added
    for result in validation_results:
        response = client._client.get_item(
            TableName=dynamodb_table,
            Key={
                "PK": {"S": f"IMAGE#{result.image_id}"},
                "SK": {
                    "S": f"RECEIPT#{result.receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{result.field_name}#RESULT#{result.result_index}"
                },
            },
        )
        assert "Item" in response

    # Act
    client.delete_receipt_validation_results(validation_results)

    # Assert
    # Verify all items were deleted (check a sample)
    for i in [0, 15, 29]:  # Check first, middle, and last
        result = validation_results[i]
        response = client._client.get_item(
            TableName=dynamodb_table,
            Key={
                "PK": {"S": f"IMAGE#{result.image_id}"},
                "SK": {
                    "S": f"RECEIPT#{result.receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{result.field_name}#RESULT#{result.result_index}"
                },
            },
        )
        assert "Item" not in response


# Now let's implement tests for getReceiptValidationResult
@pytest.mark.integration
def test_getReceiptValidationResult_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_result: ReceiptValidationResult,
):
    """Test retrieving a validation result successfully"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # First add the result
    client.add_receipt_validation_result(sample_receipt_validation_result)

    # Act
    result = client.get_receipt_validation_result(
        receipt_id=sample_receipt_validation_result.receipt_id,
        image_id=sample_receipt_validation_result.image_id,
        field_name=sample_receipt_validation_result.field_name,
        result_index=sample_receipt_validation_result.result_index,
    )

    # Assert
    assert result is not None
    assert result == sample_receipt_validation_result
    assert result.message == sample_receipt_validation_result.message
    assert result.reasoning == sample_receipt_validation_result.reasoning


@pytest.mark.integration
def test_getReceiptValidationResult_not_found(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_result: ReceiptValidationResult,
):
    """Test retrieving a validation result that doesn't exist"""
    # Arrange
    client = DynamoClient(dynamodb_table)
    field_name = sample_receipt_validation_result.field_name
    result_index = sample_receipt_validation_result.result_index

    # Mock get_item to return an empty response (no Item key)
    empty_response: Dict[str, Any] = {}

    # Create a mock for the DynamoDB client
    client._client.get_item = MagicMock(return_value=empty_response)  # type: ignore[method-assign]

    # Act & Assert
    with pytest.raises(
        ValueError,
        match=f"ReceiptValidationResult with field {field_name} and index {result_index} not found",
    ):
        client.get_receipt_validation_result(
            receipt_id=sample_receipt_validation_result.receipt_id,
            image_id=sample_receipt_validation_result.image_id,
            field_name=field_name,
            result_index=result_index,
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "receipt_id,image_id,field_name,result_index,expected_error",
    [
        (
            None,
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "field",
            0,
            "receipt_id cannot be None",
        ),
        (
            1,
            None,
            "field",
            0,
            "image_id cannot be None",
        ),
        (
            1,
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            None,
            0,
            "field_name cannot be None",
        ),
        (
            1,
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "field",
            None,
            "result_index cannot be None",
        ),
        (1, "", "field", 0, "uuid must be a valid UUIDv4"),
        (
            1,
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "",
            0,
            "field_name must not be empty.",
        ),
        (
            1,
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "field",
            -1,
            "result_index must be non-negative.",
        ),
    ],
)
def test_getReceiptValidationResult_invalid_parameters(
    dynamodb_table: Literal["MyMockedTable"],
    receipt_id,
    image_id,
    field_name,
    result_index,
    expected_error,
):
    """Test retrieving a validation result with invalid parameters"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act & Assert
    with pytest.raises((ValueError, AssertionError), match=expected_error):
        client.get_receipt_validation_result(
            receipt_id=receipt_id,
            image_id=image_id,
            field_name=field_name,
            result_index=result_index,
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found",
        ),
        (
            "ProvisionedThroughputExceededException",
            "Throughput exceeded",
            "Throughput exceeded",
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
            "Could not get receipt validation result",
        ),
    ],
)
def test_getReceiptValidationResult_client_errors(
    dynamodb_table,
    sample_receipt_validation_result,
    mocker,
    error_code,
    error_message,
    expected_error,
):
    """Test handling of client errors when retrieving a validation result"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Create a ClientError with the specified error code
    client_error = ClientError(
        {
            "Error": {
                "Code": error_code,
                "Message": error_message,
            }
        },
        "GetItem",
    )

    # Mock get_item to raise the client error
    mocker.patch.object(client._client, "get_item", side_effect=client_error)

    # Act & Assert
    with pytest.raises(Exception, match=expected_error):
        client.get_receipt_validation_result(
            receipt_id=sample_receipt_validation_result.receipt_id,
            image_id=sample_receipt_validation_result.image_id,
            field_name=sample_receipt_validation_result.field_name,
            result_index=sample_receipt_validation_result.result_index,
        )


# Now let's implement tests for listReceiptValidationResults
@pytest.mark.integration
def test_listReceiptValidationResults_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_result: ReceiptValidationResult,
    mocker,
):
    """Test listing all validation results successfully"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Create a few different validation results
    result1 = sample_receipt_validation_result

    result2 = deepcopy(result1)
    result2.result_index = 1
    result2.field_name = "subtotal"

    result3 = deepcopy(result1)
    result3.result_index = 2
    result3.field_name = "tax_amount"

    # Add all the results
    client.add_receipt_validation_results([result1, result2, result3])

    # Mock the scan response since we'll get all results
    mock_scan = mocker.patch.object(client._client, "scan")
    mock_scan.return_value = {
        "Items": [result1.to_item(), result2.to_item(), result3.to_item()],
        "Count": 3,
        "ScannedCount": 3,
    }

    # Act
    results, last_key = client.list_receipt_validation_results()

    # Assert
    assert len(results) == 3
    assert last_key is None

    # Verify all expected results are in the list
    field_names = [r.field_name for r in results]
    assert "total_amount" in field_names
    assert "subtotal" in field_names
    assert "tax_amount" in field_names


@pytest.mark.integration
def test_listReceiptValidationResults_with_pagination(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_result: ReceiptValidationResult,
    mocker,
):
    """Test listing validation results with pagination"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Create 10 different validation results
    validation_results = []
    for i in range(10):
        result = deepcopy(sample_receipt_validation_result)
        result.result_index = i
        result.field_name = f"field_{i}"
        validation_results.append(result)

    # Add all the results
    client.add_receipt_validation_results(validation_results)

    # Mock the scan response for pagination
    mock_scan = mocker.patch.object(client._client, "scan")

    # First page with pagination token
    first_page_items = [r.to_item() for r in validation_results[:3]]
    first_page_response = {
        "Items": first_page_items,
        "Count": 3,
        "ScannedCount": 3,
        "LastEvaluatedKey": {"PK": {"S": "some_key"}, "SK": {"S": "some_sk"}},
    }

    # Second page with pagination token
    second_page_items = [r.to_item() for r in validation_results[3:6]]
    second_page_response = {
        "Items": second_page_items,
        "Count": 3,
        "ScannedCount": 3,
        "LastEvaluatedKey": {
            "PK": {"S": "some_key2"},
            "SK": {"S": "some_sk2"},
        },
    }

    # Third page with pagination token
    third_page_items = [r.to_item() for r in validation_results[6:9]]
    third_page_response = {
        "Items": third_page_items,
        "Count": 3,
        "ScannedCount": 3,
        "LastEvaluatedKey": {
            "PK": {"S": "some_key3"},
            "SK": {"S": "some_sk3"},
        },
    }

    # Fourth page (last) without pagination token
    fourth_page_items = [r.to_item() for r in validation_results[9:]]
    fourth_page_response = {
        "Items": fourth_page_items,
        "Count": 1,
        "ScannedCount": 1,
    }

    # Setup the mock to return different responses on subsequent calls
    mock_scan.side_effect = [
        first_page_response,
        second_page_response,
        third_page_response,
        fourth_page_response,
    ]

    # Act - Get first page with limit of 3
    page1_results, pagination_key1 = client.list_receipt_validation_results(
        limit=3
    )

    # Check pagination info from first page
    assert pagination_key1 is not None
    assert len(page1_results) == 3

    # Get second page
    page2_results, pagination_key2 = client.list_receipt_validation_results(
        limit=3, last_evaluated_key=pagination_key1
    )

    # Check pagination info from second page
    assert pagination_key2 is not None
    assert len(page2_results) == 3

    # Get third page
    page3_results, pagination_key3 = client.list_receipt_validation_results(
        limit=3, last_evaluated_key=pagination_key2
    )

    # Check pagination info from third page
    assert pagination_key3 is not None
    assert len(page3_results) == 3

    # Get fourth page (should be last with just 1 item)
    page4_results, pagination_key4 = client.list_receipt_validation_results(
        limit=3, last_evaluated_key=pagination_key3
    )

    # Check pagination info from fourth page
    assert pagination_key4 is None  # Should be None as this is the last page
    assert len(page4_results) == 1

    # Ensure all field names across pages are as expected
    all_field_names = [
        r.field_name
        for r in page1_results + page2_results + page3_results + page4_results
    ]
    assert len(all_field_names) == 10
    assert len(set(all_field_names)) == 10


@pytest.mark.integration
def test_listReceiptValidationResults_empty_results(
    dynamodb_table: Literal["MyMockedTable"],
    mocker,
):
    """Test listing validation results when none exist"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Mock the scan response with no results
    mock_scan = mocker.patch.object(client._client, "scan")
    mock_scan.return_value = {
        "Items": [],
        "Count": 0,
        "ScannedCount": 0,
    }

    # Act
    results, last_key = client.list_receipt_validation_results()

    # Assert
    assert len(results) == 0
    assert last_key is None


@pytest.mark.integration
def test_listReceiptValidationResults_with_negative_limit(
    dynamodb_table: Literal["MyMockedTable"],
):
    """Test listing validation results with a negative limit should raise ParamValidationError"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act & Assert
    with pytest.raises(
        ParamValidationError, match="Invalid value for parameter Limit"
    ):
        client.list_receipt_validation_results(limit=-1)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found",
        ),
        (
            "ProvisionedThroughputExceededException",
            "Throughput exceeded",
            "Throughput exceeded",
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
            "Could not list receipt validation result from DynamoDB",
        ),
    ],
)
def test_listReceiptValidationResults_client_errors(
    dynamodb_table,
    mocker,
    error_code,
    error_message,
    expected_error,
):
    """Test handling of client errors when listing validation results"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Create a ClientError with the specified error code
    client_error = ClientError(
        {
            "Error": {
                "Code": error_code,
                "Message": error_message,
            }
        },
        "Query",
    )

    # Mock query to raise the client error
    mocker.patch.object(client._client, "query", side_effect=client_error)

    # Act & Assert
    with pytest.raises(Exception, match=expected_error):
        client.list_receipt_validation_results()


# Now let's implement tests for listReceiptValidationResultsByType
@pytest.mark.integration
def test_listReceiptValidationResultsByType_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_result: ReceiptValidationResult,
    mocker,
):
    """Test listing validation results by type successfully"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Create results with different types
    result1 = deepcopy(sample_receipt_validation_result)
    result1.type = "error"

    result2 = deepcopy(result1)
    result2.result_index = 1
    result2.field_name = "subtotal"
    result2.type = "error"

    result3 = deepcopy(result1)
    result3.result_index = 2
    result3.field_name = "tax_amount"
    result3.type = "warning"

    result4 = deepcopy(result1)
    result4.result_index = 3
    result4.field_name = "date"
    result4.type = "info"

    # Add all the results
    client.add_receipt_validation_results([result1, result2, result3, result4])

    # Since GSI3 might not be available in the mocked DynamoDB,
    # we'll mock the query response
    mock_query = mocker.patch.object(client._client, "query")

    # Create a mock response for the error type
    error_response = {
        "Items": [result1.to_item(), result2.to_item()],
        "Count": 2,
        "ScannedCount": 2,
    }
    mock_query.return_value = error_response

    # Act - Get results of type "error"
    error_results, _ = client.list_receipt_validation_results_by_type(
        result_type="error"
    )

    # Assert
    assert len(error_results) == 2

    # Verify all results have the correct type
    for result in error_results:
        assert result.type == "error"

    # Create a mock response for the warning type
    warning_response = {
        "Items": [result3.to_item()],
        "Count": 1,
        "ScannedCount": 1,
    }
    mock_query.return_value = warning_response

    # Act - Get results of type "warning"
    warning_results, _ = client.list_receipt_validation_results_by_type(
        result_type="warning"
    )

    # Assert
    assert len(warning_results) == 1
    assert warning_results[0].type == "warning"
    assert warning_results[0].field_name == "tax_amount"

    # Create a mock response for the info type
    info_response = {
        "Items": [result4.to_item()],
        "Count": 1,
        "ScannedCount": 1,
    }
    mock_query.return_value = info_response

    # Act - Get results of type "info"
    info_results, _ = client.list_receipt_validation_results_by_type(
        result_type="info"
    )

    # Assert
    assert len(info_results) == 1
    assert info_results[0].type == "info"
    assert info_results[0].field_name == "date"


@pytest.mark.integration
def test_listReceiptValidationResultsByType_pagination(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_result: ReceiptValidationResult,
    mocker,
):
    """Test listing validation results by type with pagination"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Create 10 different validation results of type "error"
    validation_results = []
    for i in range(10):
        result = deepcopy(sample_receipt_validation_result)
        result.result_index = i
        result.field_name = f"field_{i}"
        result.type = "error"
        validation_results.append(result)

    # Add all the results
    client.add_receipt_validation_results(validation_results)

    # Since GSI3 might not be available in the mocked DynamoDB,
    # we'll mock the query response for pagination
    mock_query = mocker.patch.object(client._client, "query")

    # Create mock responses for each page

    # First page with pagination token
    first_page_items = [r.to_item() for r in validation_results[:3]]
    first_page_response = {
        "Items": first_page_items,
        "Count": 3,
        "ScannedCount": 3,
        "LastEvaluatedKey": {
            "PK": {"S": "some_key"},
            "SK": {"S": "some_sk"},
            "GSI3SK": {"S": "some_gsi3sk"},
        },
    }

    # Second page with pagination token
    second_page_items = [r.to_item() for r in validation_results[3:6]]
    second_page_response = {
        "Items": second_page_items,
        "Count": 3,
        "ScannedCount": 3,
        "LastEvaluatedKey": {
            "PK": {"S": "some_key2"},
            "SK": {"S": "some_sk2"},
            "GSI3SK": {"S": "some_gsi3sk2"},
        },
    }

    # Third page with pagination token
    third_page_items = [r.to_item() for r in validation_results[6:9]]
    third_page_response = {
        "Items": third_page_items,
        "Count": 3,
        "ScannedCount": 3,
        "LastEvaluatedKey": {
            "PK": {"S": "some_key3"},
            "SK": {"S": "some_sk3"},
            "GSI3SK": {"S": "some_gsi3sk3"},
        },
    }

    # Fourth page (last) without pagination token
    fourth_page_items = [r.to_item() for r in validation_results[9:]]
    fourth_page_response = {
        "Items": fourth_page_items,
        "Count": 1,
        "ScannedCount": 1,
    }

    # Setup the mock to return different responses on subsequent calls
    mock_query.side_effect = [
        first_page_response,
        second_page_response,
        third_page_response,
        fourth_page_response,
    ]

    # Act - Get first page with limit of 3
    page1_results, pagination_key1 = (
        client.list_receipt_validation_results_by_type(
            result_type="error", limit=3
        )
    )

    # Check pagination info from first page
    assert pagination_key1 is not None
    assert len(page1_results) == 3

    # Get second page
    page2_results, pagination_key2 = (
        client.list_receipt_validation_results_by_type(
            result_type="error", limit=3, last_evaluated_key=pagination_key1
        )
    )

    # Check pagination info from second page
    assert pagination_key2 is not None
    assert len(page2_results) == 3

    # Get third page
    page3_results, pagination_key3 = (
        client.list_receipt_validation_results_by_type(
            result_type="error", limit=3, last_evaluated_key=pagination_key2
        )
    )

    # Check pagination info from third page
    assert pagination_key3 is not None
    assert len(page3_results) == 3

    # Get fourth page (should be last with just 1 item)
    page4_results, pagination_key4 = (
        client.list_receipt_validation_results_by_type(
            result_type="error", limit=3, last_evaluated_key=pagination_key3
        )
    )

    # Check pagination info from fourth page
    assert pagination_key4 is None  # Should be None as this is the last page
    assert len(page4_results) == 1

    # Verify all results across pages are of type "error"
    all_results = page1_results + page2_results + page3_results + page4_results
    assert len(all_results) == 10
    assert all(r.type == "error" for r in all_results)


@pytest.mark.integration
def test_listReceiptValidationResultsByType_empty_results(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_result: ReceiptValidationResult,
    mocker,
):
    """Test listing validation results by type when none exist"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Add a single result of type "error"
    result = deepcopy(sample_receipt_validation_result)
    result.type = "error"
    client.add_receipt_validation_result(result)

    # Mock the query response for a different type with no results
    mock_query = mocker.patch.object(client._client, "query")
    mock_query.return_value = {
        "Items": [],
        "Count": 0,
        "ScannedCount": 0,
    }

    # Act - Get results of type "warning" (which doesn't exist)
    results, pagination_key = client.list_receipt_validation_results_by_type(
        result_type="warning"
    )

    # Assert
    assert len(results) == 0
    assert pagination_key is None


@pytest.mark.integration
@pytest.mark.parametrize(
    "result_type,expected_error",
    [
        (None, "result_type parameter is required"),
        ("", "result_type must not be empty"),
    ],
)
def test_listReceiptValidationResultsByType_invalid_parameters(
    dynamodb_table,
    result_type,
    expected_error,
):
    """Test listing validation results by type with invalid parameters"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act & Assert
    with pytest.raises(ValueError, match=expected_error):
        client.list_receipt_validation_results_by_type(result_type=result_type)


@pytest.mark.integration
def test_listReceiptValidationResultsByType_with_negative_limit(
    dynamodb_table: Literal["MyMockedTable"],
):
    """Test listing validation results by type with a negative limit should raise ParamValidationError"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act & Assert
    with pytest.raises(
        ParamValidationError, match="Invalid value for parameter Limit"
    ):
        client.list_receipt_validation_results_by_type(
            result_type="error", limit=-1
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found",
        ),
        (
            "ProvisionedThroughputExceededException",
            "Throughput exceeded",
            "Throughput exceeded",
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
            "Could not list receipt validation result from DynamoDB",
        ),
    ],
)
def test_listReceiptValidationResultsByType_client_errors(
    dynamodb_table,
    mocker,
    error_code,
    error_message,
    expected_error,
):
    """Test handling of client errors when listing validation results by type"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Create a ClientError with the specified error code
    client_error = ClientError(
        {
            "Error": {
                "Code": error_code,
                "Message": error_message,
            }
        },
        "Query",
    )

    # Mock query to raise the client error
    mocker.patch.object(client._client, "query", side_effect=client_error)

    # Act & Assert
    with pytest.raises(Exception, match=expected_error):
        client.list_receipt_validation_results_by_type(result_type="error")


@pytest.mark.integration
def test_listReceiptValidationResultsForField_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_result: ReceiptValidationResult,
    mocker,
):
    """
    Tests that listReceiptValidationResultsForField successfully retrieves validation results for a specific field.
    """
    # Setup
    client = DynamoClient(table_name=dynamodb_table)

    # Mock the query response
    mock_query = mocker.patch.object(client._client, "query")
    mock_query.return_value = {
        "Items": [
            {
                "PK": {
                    "S": f"IMAGE#{sample_receipt_validation_result.image_id}"
                },
                "SK": {
                    "S": f"RECEIPT#{sample_receipt_validation_result.receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{sample_receipt_validation_result.field_name}#RESULT#0"
                },
                "receipt_id": {
                    "N": str(sample_receipt_validation_result.receipt_id)
                },
                "image_id": {"S": sample_receipt_validation_result.image_id},
                "field_name": {
                    "S": sample_receipt_validation_result.field_name
                },
                "result_index": {"N": "0"},
                "type": {"S": sample_receipt_validation_result.type},
                "message": {"S": sample_receipt_validation_result.message},
                "reasoning": {"S": sample_receipt_validation_result.reasoning},
                "field": {"S": sample_receipt_validation_result.field},
                "expected_value": {
                    "S": sample_receipt_validation_result.expected_value
                },
                "actual_value": {
                    "S": sample_receipt_validation_result.actual_value
                },
                "validation_timestamp": {
                    "S": sample_receipt_validation_result.validation_timestamp
                },
                "metadata": {
                    "M": {
                        "source_info": {
                            "M": {"model": {"S": "validation-v1"}}
                        },
                        "confidence": {"N": "0.92"},
                    }
                },
            }
        ],
        "Count": 1,
        "ScannedCount": 1,
    }

    # Execute
    results, _ = client.list_receipt_validation_results_for_field(
        receipt_id=sample_receipt_validation_result.receipt_id,
        image_id=sample_receipt_validation_result.image_id,
        field_name=sample_receipt_validation_result.field_name,
    )

    # Assert
    assert len(results) == 1
    assert results[0].receipt_id == sample_receipt_validation_result.receipt_id
    assert results[0].image_id == sample_receipt_validation_result.image_id
    assert results[0].field_name == sample_receipt_validation_result.field_name
    assert results[0].type == sample_receipt_validation_result.type

    # Verify query parameters
    mock_query.assert_called_once_with(
        TableName=dynamodb_table,
        KeyConditionExpression="#pk = :pk AND begins_with(#sk, :sk_prefix)",
        ExpressionAttributeNames={
            "#pk": "PK",
            "#sk": "SK",
        },
        ExpressionAttributeValues={
            ":pk": {"S": f"IMAGE#{sample_receipt_validation_result.image_id}"},
            ":sk_prefix": {
                "S": f"RECEIPT#{sample_receipt_validation_result.receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{sample_receipt_validation_result.field_name}#RESULT#"
            },
        },
    )


@pytest.mark.integration
def test_listReceiptValidationResultsForField_with_pagination(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_validation_result: ReceiptValidationResult,
    mocker,
):
    """
    Tests that listReceiptValidationResultsForField correctly handles pagination.
    """
    # Setup
    client = DynamoClient(table_name=dynamodb_table)

    # Create a copy of the sample result with a different result_index
    result2 = deepcopy(sample_receipt_validation_result)
    result2.result_index = 1

    # Mock the query response with pagination
    mock_query = mocker.patch.object(client._client, "query")

    # First response with LastEvaluatedKey
    first_response = {
        "Items": [
            {
                "PK": {
                    "S": f"IMAGE#{sample_receipt_validation_result.image_id}"
                },
                "SK": {
                    "S": f"RECEIPT#{sample_receipt_validation_result.receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{sample_receipt_validation_result.field_name}#RESULT#0"
                },
                "receipt_id": {
                    "N": str(sample_receipt_validation_result.receipt_id)
                },
                "image_id": {"S": sample_receipt_validation_result.image_id},
                "field_name": {
                    "S": sample_receipt_validation_result.field_name
                },
                "result_index": {"N": "0"},
                "type": {"S": sample_receipt_validation_result.type},
                "message": {"S": sample_receipt_validation_result.message},
                "reasoning": {"S": sample_receipt_validation_result.reasoning},
                "field": {"S": sample_receipt_validation_result.field},
                "expected_value": {
                    "S": sample_receipt_validation_result.expected_value
                },
                "actual_value": {
                    "S": sample_receipt_validation_result.actual_value
                },
                "validation_timestamp": {
                    "S": sample_receipt_validation_result.validation_timestamp
                },
                "metadata": {
                    "M": {
                        "source_info": {
                            "M": {"model": {"S": "validation-v1"}}
                        },
                        "confidence": {"N": "0.92"},
                    }
                },
            }
        ],
        "Count": 1,
        "ScannedCount": 1,
        "LastEvaluatedKey": {
            "PK": {"S": f"IMAGE#{sample_receipt_validation_result.image_id}"},
            "SK": {
                "S": f"RECEIPT#{sample_receipt_validation_result.receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{sample_receipt_validation_result.field_name}#RESULT#0"
            },
        },
    }

    # Second response without LastEvaluatedKey
    second_response = {
        "Items": [
            {
                "PK": {"S": f"IMAGE#{result2.image_id}"},
                "SK": {
                    "S": f"RECEIPT#{result2.receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{result2.field_name}#RESULT#1"
                },
                "receipt_id": {"N": str(result2.receipt_id)},
                "image_id": {"S": result2.image_id},
                "field_name": {"S": result2.field_name},
                "result_index": {"N": "1"},
                "type": {"S": result2.type},
                "message": {"S": result2.message},
                "reasoning": {"S": result2.reasoning},
                "field": {"S": result2.field},
                "expected_value": {"S": result2.expected_value},
                "actual_value": {"S": result2.actual_value},
                "validation_timestamp": {"S": result2.validation_timestamp},
                "metadata": {
                    "M": {
                        "source_info": {
                            "M": {"model": {"S": "validation-v1"}}
                        },
                        "confidence": {"N": "0.92"},
                    }
                },
            }
        ],
        "Count": 1,
        "ScannedCount": 1,
    }

    mock_query.side_effect = [first_response, second_response]

    # Execute
    results, _ = client.list_receipt_validation_results_for_field(
        receipt_id=sample_receipt_validation_result.receipt_id,
        image_id=sample_receipt_validation_result.image_id,
        field_name=sample_receipt_validation_result.field_name,
    )

    # Assert
    assert len(results) == 2
    assert results[0].receipt_id == sample_receipt_validation_result.receipt_id
    assert results[0].result_index == 0
    assert results[1].receipt_id == result2.receipt_id
    assert results[1].result_index == 1

    # Verify query parameters for both calls
    assert mock_query.call_count == 2

    # First call
    first_call_args = mock_query.call_args_list[0][1]
    assert first_call_args["TableName"] == dynamodb_table
    assert (
        first_call_args["KeyConditionExpression"]
        == "#pk = :pk AND begins_with(#sk, :sk_prefix)"
    )

    # Second call with ExclusiveStartKey
    second_call_args = mock_query.call_args_list[1][1]
    assert second_call_args["TableName"] == dynamodb_table
    assert (
        second_call_args["KeyConditionExpression"]
        == "#pk = :pk AND begins_with(#sk, :sk_prefix)"
    )
    assert (
        second_call_args["ExclusiveStartKey"]
        == first_response["LastEvaluatedKey"]
    )


@pytest.mark.integration
def test_listReceiptValidationResultsForField_empty_results(
    dynamodb_table: Literal["MyMockedTable"],
    mocker,
):
    """
    Tests that listReceiptValidationResultsForField returns an empty list when no results are found.
    """
    # Setup
    client = DynamoClient(table_name=dynamodb_table)

    # Mock the query response with no items
    mock_query = mocker.patch.object(client._client, "query")
    mock_query.return_value = {
        "Items": [],
        "Count": 0,
        "ScannedCount": 0,
    }

    # Execute
    results, _ = client.list_receipt_validation_results_for_field(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        field_name="total_amount",
    )

    # Assert
    assert len(results) == 0
    assert isinstance(results, list)


@pytest.mark.integration
@pytest.mark.parametrize(
    "receipt_id,image_id,field_name,expected_error",
    [
        (
            None,
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "field_name",
            "receipt_id cannot be None",
        ),
        (
            "not_an_int",
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "field_name",
            "receipt_id must be an integer.",
        ),
        (
            1,
            None,
            "field_name",
            "image_id cannot be None",
        ),
        (1, "invalid-uuid", "field_name", "uuid must be a valid UUIDv4"),
        (
            1,
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            None,
            "field_name cannot be None",
        ),
        (
            1,
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            123,
            "field_name must be a string.",
        ),
        (
            1,
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "",
            "field_name must not be empty.",
        ),
    ],
)
def test_listReceiptValidationResultsForField_invalid_parameters(
    dynamodb_table,
    receipt_id,
    image_id,
    field_name,
    expected_error,
):
    """
    Tests that listReceiptValidationResultsForField raises appropriate errors for invalid parameters.
    """
    # Setup
    client = DynamoClient(table_name=dynamodb_table)

    # Execute and Assert
    with pytest.raises(ValueError, match=expected_error):
        client.list_receipt_validation_results_for_field(
            receipt_id=receipt_id,
            image_id=image_id,
            field_name=field_name,
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found",
        ),
        (
            "ProvisionedThroughputExceededException",
            "Throughput exceeded",
            "Throughput exceeded",
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
            "Could not list receipt validation result from DynamoDB",
        ),
    ],
)
def test_listReceiptValidationResultsForField_client_errors(
    dynamodb_table,
    mocker,
    error_code,
    error_message,
    expected_error,
):
    """
    Tests that listReceiptValidationResultsForField handles client errors appropriately.
    """
    # Setup
    client = DynamoClient(table_name=dynamodb_table)

    # Mock the client error
    mock_query = mocker.patch.object(client._client, "query")
    mock_query.side_effect = ClientError(
        {
            "Error": {
                "Code": error_code,
                "Message": error_message,
            }
        },
        "Query",
    )

    # Execute and Assert
    with pytest.raises(Exception, match=expected_error):
        client.list_receipt_validation_results_for_field(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            field_name="total_amount",
        )
