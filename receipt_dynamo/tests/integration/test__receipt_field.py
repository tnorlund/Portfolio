from typing import Any, Dict, List, Literal, Optional, Type

import pytest
from botocore.exceptions import ClientError, ParamValidationError
from receipt_dynamo import DynamoClient, ReceiptField

# -------------------------------------------------------------------
#                        FIXTURES
# -------------------------------------------------------------------


@pytest.fixture
def sample_receipt_field():
    return ReceiptField(
        field_type="BUSINESS_NAME",
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=1,
        words=[
            {
                "word_id": 5,
                "line_id": 10,
                "label": "BUSINESS_NAME",
            }
        ],
        reasoning="This field appears to be the business name",
        timestamp_added="2024-03-20T12:00:00Z",
    )


# -------------------------------------------------------------------
#                        addReceiptField
# -------------------------------------------------------------------


@pytest.mark.integration
def test_addReceiptField_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_field: ReceiptField,
):
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act
    client.addReceiptField(sample_receipt_field)

    # Assert
    retrieved_field = client.getReceiptField(
        sample_receipt_field.field_type,
        sample_receipt_field.image_id,
        sample_receipt_field.receipt_id,
    )
    assert retrieved_field == sample_receipt_field


@pytest.mark.integration
def test_addReceiptField_duplicate_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_field: ReceiptField,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.addReceiptField(sample_receipt_field)

    # Act & Assert
    with pytest.raises(ValueError, match="already exists"):
        client.addReceiptField(sample_receipt_field)


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "ReceiptField parameter is required and cannot be None."),
        (
            "not-a-receipt-field",
            "receipt_field must be an instance of the ReceiptField class.",
        ),
    ],
)
def test_addReceiptField_invalid_parameters(
    dynamodb_table,
    sample_receipt_field,
    mocker,
    invalid_input,
    expected_error,
):
    """
    Tests that addReceiptField raises ValueError for invalid parameters:
    - When receipt field is None
    - When receipt field is not an instance of ReceiptField
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match=expected_error):
        client.addReceiptField(invalid_input)  # type: ignore


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
            "Could not add receipt field to DynamoDB",
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
            "Could not add receipt field to DynamoDB",
        ),
        (
            "ValidationException",
            "One or more parameters were invalid",
            "One or more parameters were invalid",
        ),
        ("AccessDeniedException", "Access denied", "Access denied"),
    ],
)
def test_addReceiptField_client_errors(
    dynamodb_table,
    sample_receipt_field,
    mocker,
    error_code,
    error_message,
    expected_exception,
):
    """
    Tests that addReceiptField handles various client errors appropriately:
    - ConditionalCheckFailedException when item already exists
    - ResourceNotFoundException when table not found
    - ProvisionedThroughputExceededException when throughput exceeded
    - InternalServerError for server-side errors
    - UnknownError for unexpected errors
    - ValidationException for invalid parameters
    - AccessDeniedException for access denied errors
    """
    client = DynamoClient(dynamodb_table)
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": error_code,
                    "Message": error_message,
                }
            },
            "PutItem",
        ),
    )

    with pytest.raises(Exception, match=expected_exception):
        client.addReceiptField(sample_receipt_field)
    mock_put.assert_called_once()


# -------------------------------------------------------------------
#                        addReceiptFields
# -------------------------------------------------------------------


@pytest.mark.integration
def test_addReceiptFields_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_field: ReceiptField,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    fields = [
        ReceiptField(
            field_type="BUSINESS_NAME",
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=1,
            words=[
                {
                    "word_id": 5,
                    "line_id": 10,
                    "label": "BUSINESS_NAME",
                }
            ],
            reasoning="This field appears to be the business name",
            timestamp_added="2024-03-20T12:00:00Z",
        ),
        ReceiptField(
            field_type="ADDRESS",
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=2,
            words=[
                {
                    "word_id": 10,
                    "line_id": 20,
                    "label": "ADDRESS",
                }
            ],
            reasoning="This field appears to be the address",
            timestamp_added="2024-03-20T12:00:00Z",
        ),
    ]

    # Act
    client.addReceiptFields(fields)

    # Assert
    for field in fields:
        retrieved_field = client.getReceiptField(
            field.field_type,
            field.image_id,
            field.receipt_id,
        )
        assert retrieved_field == field


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "ReceiptFields parameter is required and cannot be None."),
        (
            "not-a-list",
            "receipt_fields must be a list of ReceiptField instances.",
        ),
        (
            [1, 2, 3],
            "All receipt fields must be instances of the ReceiptField class.",
        ),
    ],
)
def test_addReceiptFields_invalid_parameters(
    dynamodb_table,
    sample_receipt_field,
    mocker,
    invalid_input,
    expected_error,
):
    """
    Tests that addReceiptFields raises ValueError for invalid parameters:
    - When receipt fields is None
    - When receipt fields is not a list
    - When receipt fields contains non-ReceiptField instances
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match=expected_error):
        client.addReceiptFields(invalid_input)  # type: ignore


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_exception",
    [
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
            "One or more parameters were invalid",
            "One or more parameters given were invalid",
        ),
        ("AccessDeniedException", "Access denied", "Access denied"),
        (
            "UnknownError",
            "Unknown error",
            "Error adding receipt fields",
        ),
    ],
)
def test_addReceiptFields_client_errors(
    dynamodb_table,
    sample_receipt_field,
    mocker,
    error_code,
    error_message,
    expected_exception,
):
    """
    Tests that addReceiptFields handles various client errors appropriately:
    - ProvisionedThroughputExceededException when throughput exceeded
    - InternalServerError for server-side errors
    - ValidationException for invalid parameters
    - AccessDeniedException for access denied errors
    - UnknownError for unexpected errors
    """
    client = DynamoClient(dynamodb_table)
    fields = [sample_receipt_field]
    mock_batch_write = mocker.patch.object(
        client._client,
        "batch_write_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": error_code,
                    "Message": error_message,
                }
            },
            "BatchWriteItem",
        ),
    )

    with pytest.raises(Exception, match=expected_exception):
        client.addReceiptFields(fields)
    mock_batch_write.assert_called_once()


@pytest.mark.integration
def test_addReceiptFields_unprocessed_items(
    dynamodb_table,
    sample_receipt_field,
    mocker,
):
    """
    Tests that addReceiptFields handles unprocessed items correctly by retrying them.
    """
    client = DynamoClient(dynamodb_table)
    fields = [sample_receipt_field]

    # Mock the batch_write_item to return unprocessed items on first call
    # and succeed on second call
    mock_batch_write = mocker.patch.object(
        client._client,
        "batch_write_item",
        side_effect=[
            {
                "UnprocessedItems": {
                    dynamodb_table: [
                        {
                            "PutRequest": {
                                "Item": sample_receipt_field.to_item()
                            }
                        }
                    ]
                }
            },
            {},  # Empty response on second call
        ],
    )

    client.addReceiptFields(fields)

    # Verify that batch_write_item was called twice
    assert mock_batch_write.call_count == 2


# -------------------------------------------------------------------
#                        updateReceiptField
# -------------------------------------------------------------------


@pytest.mark.integration
def test_updateReceiptField_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_field: ReceiptField,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.addReceiptField(sample_receipt_field)

    # Update the field
    updated_field = ReceiptField(
        field_type=sample_receipt_field.field_type,
        image_id=sample_receipt_field.image_id,
        receipt_id=sample_receipt_field.receipt_id,
        words=sample_receipt_field.words,
        reasoning="Updated reasoning",
        timestamp_added=sample_receipt_field.timestamp_added,
    )

    # Act
    client.updateReceiptField(updated_field)

    # Assert
    retrieved_field = client.getReceiptField(
        updated_field.field_type,
        updated_field.image_id,
        updated_field.receipt_id,
    )
    assert retrieved_field == updated_field


@pytest.mark.integration
def test_updateReceiptField_nonexistent_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_field: ReceiptField,
):
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act & Assert
    with pytest.raises(ValueError, match="does not exist"):
        client.updateReceiptField(sample_receipt_field)


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "ReceiptField parameter is required and cannot be None."),
        (
            "not-a-receipt-field",
            "receipt_field must be an instance of the ReceiptField class.",
        ),
    ],
)
def test_updateReceiptField_invalid_parameters(
    dynamodb_table,
    sample_receipt_field,
    mocker,
    invalid_input,
    expected_error,
):
    """
    Tests that updateReceiptField raises ValueError for invalid parameters:
    - When receipt field is None
    - When receipt field is not an instance of ReceiptField
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match=expected_error):
        client.updateReceiptField(invalid_input)  # type: ignore


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_exception",
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
            "ValidationException",
            "One or more parameters were invalid",
            "One or more parameters given were invalid",
        ),
        ("AccessDeniedException", "Access denied", "Access denied"),
        (
            "UnknownError",
            "Unknown error",
            "Error updating receipt field",
        ),
    ],
)
def test_updateReceiptField_client_errors(
    dynamodb_table,
    sample_receipt_field,
    mocker,
    error_code,
    error_message,
    expected_exception,
):
    """
    Tests that updateReceiptField handles various client errors appropriately:
    - ConditionalCheckFailedException when item does not exist
    - ProvisionedThroughputExceededException when throughput exceeded
    - InternalServerError for server-side errors
    - ValidationException for invalid parameters
    - AccessDeniedException for access denied errors
    - UnknownError for unexpected errors
    """
    client = DynamoClient(dynamodb_table)
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": error_code,
                    "Message": error_message,
                }
            },
            "PutItem",
        ),
    )

    with pytest.raises(Exception, match=expected_exception):
        client.updateReceiptField(sample_receipt_field)
    mock_put.assert_called_once()


# -------------------------------------------------------------------
#                        updateReceiptFields
# -------------------------------------------------------------------


@pytest.mark.integration
def test_updateReceiptFields_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_field: ReceiptField,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.addReceiptField(sample_receipt_field)

    # Create a second field
    second_field = ReceiptField(
        field_type="ADDRESS",
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=2,
        words=[
            {
                "word_id": 10,
                "line_id": 20,
                "label": "ADDRESS",
            }
        ],
        reasoning="This field appears to be the address",
        timestamp_added="2024-03-20T12:00:00Z",
    )
    client.addReceiptField(second_field)

    # Update both fields
    updated_fields = [
        ReceiptField(
            field_type=sample_receipt_field.field_type,
            image_id=sample_receipt_field.image_id,
            receipt_id=sample_receipt_field.receipt_id,
            words=sample_receipt_field.words,
            reasoning="Updated reasoning 1",
            timestamp_added=sample_receipt_field.timestamp_added,
        ),
        ReceiptField(
            field_type=second_field.field_type,
            image_id=second_field.image_id,
            receipt_id=second_field.receipt_id,
            words=second_field.words,
            reasoning="Updated reasoning 2",
            timestamp_added=second_field.timestamp_added,
        ),
    ]

    # Act
    client.updateReceiptFields(updated_fields)

    # Assert
    for field in updated_fields:
        retrieved_field = client.getReceiptField(
            field.field_type,
            field.image_id,
            field.receipt_id,
        )
        assert retrieved_field == field


@pytest.mark.integration
def test_updateReceiptFields_nonexistent_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_field: ReceiptField,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    fields = [sample_receipt_field]

    # Act & Assert
    with pytest.raises(
        ValueError,
        match="Error updating receipt fields: An error occurred \(TransactionCanceledException\) when calling the TransactWriteItems operation: Transaction cancelled, please refer cancellation reasons for specific reasons \[ConditionalCheckFailed\]",
    ):
        client.updateReceiptFields(fields)


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "ReceiptFields parameter is required and cannot be None."),
        (
            "not-a-list",
            "receipt_fields must be a list of ReceiptField instances.",
        ),
        (
            [1, 2, 3],
            "All receipt fields must be instances of the ReceiptField class.",
        ),
    ],
)
def test_updateReceiptFields_invalid_parameters(
    dynamodb_table,
    sample_receipt_field,
    mocker,
    invalid_input,
    expected_error,
):
    """
    Tests that updateReceiptFields raises ValueError for invalid parameters:
    - When receipt fields is None
    - When receipt fields is not a list
    - When receipt fields contains non-ReceiptField instances
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match=expected_error):
        client.updateReceiptFields(invalid_input)  # type: ignore


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_exception",
    [
        (
            "ConditionalCheckFailedException",
            "One or more items do not exist",
            "One or more receipt fields do not exist",
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
            "One or more parameters were invalid",
            "One or more parameters given were invalid",
        ),
        ("AccessDeniedException", "Access denied", "Access denied"),
        (
            "UnknownError",
            "Unknown error",
            "Error updating receipt fields",
        ),
    ],
)
def test_updateReceiptFields_client_errors(
    dynamodb_table,
    sample_receipt_field,
    mocker,
    error_code,
    error_message,
    expected_exception,
):
    """
    Tests that updateReceiptFields handles various client errors appropriately:
    - ConditionalCheckFailedException when items do not exist
    - ProvisionedThroughputExceededException when throughput exceeded
    - InternalServerError for server-side errors
    - ValidationException for invalid parameters
    - AccessDeniedException for access denied errors
    - UnknownError for unexpected errors
    """
    client = DynamoClient(dynamodb_table)
    fields = [sample_receipt_field]
    mock_transact_write = mocker.patch.object(
        client._client,
        "transact_write_items",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": error_code,
                    "Message": error_message,
                }
            },
            "TransactWriteItems",
        ),
    )

    with pytest.raises(Exception, match=expected_exception):
        client.updateReceiptFields(fields)
    mock_transact_write.assert_called_once()


@pytest.mark.integration
def test_updateReceiptFields_chunking(
    dynamodb_table,
    sample_receipt_field,
    mocker,
):
    """
    Tests that updateReceiptFields processes fields in chunks of 25 items.
    """
    client = DynamoClient(dynamodb_table)

    # Create 30 fields (should be processed in 2 chunks)
    fields = [
        ReceiptField(
            field_type=f"FIELD_{i}",
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=i,
            words=[
                {
                    "word_id": i * 5,
                    "line_id": i * 10,
                    "label": f"LABEL_{i}",
                }
            ],
            reasoning=f"Reasoning {i}",
            timestamp_added="2024-03-20T12:00:00Z",
        )
        for i in range(1, 31)
    ]

    # Mock the transact_write_items method
    mock_transact_write = mocker.patch.object(
        client._client,
        "transact_write_items",
        return_value={},
    )

    client.updateReceiptFields(fields)

    # Verify that transact_write_items was called twice (for 2 chunks)
    assert mock_transact_write.call_count == 2


# -------------------------------------------------------------------
#                        deleteReceiptField
# -------------------------------------------------------------------


@pytest.mark.integration
def test_deleteReceiptField_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_field: ReceiptField,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.addReceiptField(sample_receipt_field)

    # Act
    client.deleteReceiptField(sample_receipt_field)

    # Assert
    with pytest.raises(ValueError, match="does not exist"):
        client.getReceiptField(
            sample_receipt_field.field_type,
            sample_receipt_field.image_id,
            sample_receipt_field.receipt_id,
        )


@pytest.mark.integration
def test_deleteReceiptField_nonexistent_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_field: ReceiptField,
):
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act & Assert
    with pytest.raises(ValueError, match="does not exist"):
        client.deleteReceiptField(sample_receipt_field)


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "ReceiptField parameter is required and cannot be None."),
        (
            "not-a-receipt-field",
            "receipt_field must be an instance of the ReceiptField class.",
        ),
    ],
)
def test_deleteReceiptField_invalid_parameters(
    dynamodb_table,
    sample_receipt_field,
    mocker,
    invalid_input,
    expected_error,
):
    """
    Tests that deleteReceiptField raises ValueError for invalid parameters:
    - When receipt field is None
    - When receipt field is not an instance of ReceiptField
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match=expected_error):
        client.deleteReceiptField(invalid_input)  # type: ignore


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_exception",
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
            "ValidationException",
            "One or more parameters were invalid",
            "One or more parameters given were invalid",
        ),
        ("AccessDeniedException", "Access denied", "Access denied"),
        (
            "UnknownError",
            "Unknown error",
            "Error deleting receipt field",
        ),
    ],
)
def test_deleteReceiptField_client_errors(
    dynamodb_table,
    sample_receipt_field,
    mocker,
    error_code,
    error_message,
    expected_exception,
):
    """
    Tests that deleteReceiptField handles various client errors appropriately:
    - ConditionalCheckFailedException when item does not exist
    - ProvisionedThroughputExceededException when throughput exceeded
    - InternalServerError for server-side errors
    - ValidationException for invalid parameters
    - AccessDeniedException for access denied errors
    - UnknownError for unexpected errors
    """
    client = DynamoClient(dynamodb_table)
    mock_delete = mocker.patch.object(
        client._client,
        "delete_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": error_code,
                    "Message": error_message,
                }
            },
            "DeleteItem",
        ),
    )

    with pytest.raises(Exception, match=expected_exception):
        client.deleteReceiptField(sample_receipt_field)
    mock_delete.assert_called_once()


# -------------------------------------------------------------------
#                        deleteReceiptFields
# -------------------------------------------------------------------


@pytest.mark.integration
def test_deleteReceiptFields_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_field: ReceiptField,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.addReceiptField(sample_receipt_field)

    # Create a second field
    second_field = ReceiptField(
        field_type="ADDRESS",
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=2,
        words=[
            {
                "word_id": 10,
                "line_id": 20,
                "label": "ADDRESS",
            }
        ],
        reasoning="This field appears to be the address",
        timestamp_added="2024-03-20T12:00:00Z",
    )
    client.addReceiptField(second_field)

    fields = [sample_receipt_field, second_field]

    # Act
    client.deleteReceiptFields(fields)

    # Assert
    for field in fields:
        with pytest.raises(ValueError, match="does not exist"):
            client.getReceiptField(
                field.field_type,
                field.image_id,
                field.receipt_id,
            )


@pytest.mark.integration
def test_deleteReceiptFields_nonexistent_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_field: ReceiptField,
):
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act & Assert
    with pytest.raises(
        Exception,
        match="Error deleting receipt fields: An error occurred \(TransactionCanceledException\) when calling the TransactWriteItems operation: Transaction cancelled, please refer cancellation reasons for specific reasons \[ConditionalCheckFailed\]",
    ):
        client.deleteReceiptFields([sample_receipt_field])


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "ReceiptFields parameter is required and cannot be None."),
        (
            "not-a-list",
            "receipt_fields must be a list of ReceiptField instances.",
        ),
        (
            [1, 2, 3],
            "All receipt fields must be instances of the ReceiptField class.",
        ),
    ],
)
def test_deleteReceiptFields_invalid_parameters(
    dynamodb_table,
    sample_receipt_field,
    mocker,
    invalid_input,
    expected_error,
):
    """
    Tests that deleteReceiptFields raises ValueError for invalid parameters:
    - When receipt fields is None
    - When receipt fields is not a list
    - When receipt fields contains non-ReceiptField instances
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match=expected_error):
        client.deleteReceiptFields(invalid_input)  # type: ignore


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_exception",
    [
        (
            "ConditionalCheckFailedException",
            "One or more items do not exist",
            "One or more receipt fields do not exist",
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
            "One or more parameters were invalid",
            "One or more parameters given were invalid",
        ),
        ("AccessDeniedException", "Access denied", "Access denied"),
        (
            "UnknownError",
            "Unknown error",
            "Error deleting receipt fields",
        ),
    ],
)
def test_deleteReceiptFields_client_errors(
    dynamodb_table,
    sample_receipt_field,
    mocker,
    error_code,
    error_message,
    expected_exception,
):
    """
    Tests that deleteReceiptFields handles various client errors appropriately:
    - ConditionalCheckFailedException when items do not exist
    - ProvisionedThroughputExceededException when throughput exceeded
    - InternalServerError for server-side errors
    - ValidationException for invalid parameters
    - AccessDeniedException for access denied errors
    - UnknownError for unexpected errors
    """
    client = DynamoClient(dynamodb_table)
    fields = [sample_receipt_field]
    mock_transact_write = mocker.patch.object(
        client._client,
        "transact_write_items",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": error_code,
                    "Message": error_message,
                }
            },
            "TransactWriteItems",
        ),
    )

    with pytest.raises(Exception, match=expected_exception):
        client.deleteReceiptFields(fields)
    mock_transact_write.assert_called_once()


@pytest.mark.integration
def test_deleteReceiptFields_chunking(
    dynamodb_table,
    sample_receipt_field,
    mocker,
):
    """
    Tests that deleteReceiptFields processes fields in chunks of 25 items.
    """
    client = DynamoClient(dynamodb_table)

    # Create 30 fields (should be processed in 2 chunks)
    fields = [
        ReceiptField(
            field_type=f"FIELD_{i}",
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            receipt_id=i,
            words=[
                {
                    "word_id": i * 5,
                    "line_id": i * 10,
                    "label": f"LABEL_{i}",
                }
            ],
            reasoning=f"Reasoning {i}",
            timestamp_added="2024-03-20T12:00:00Z",
        )
        for i in range(1, 31)
    ]

    # Mock the transact_write_items method
    mock_transact_write = mocker.patch.object(
        client._client,
        "transact_write_items",
        return_value={},
    )

    client.deleteReceiptFields(fields)

    # Verify that transact_write_items was called twice (for 2 chunks)
    assert mock_transact_write.call_count == 2


# -------------------------------------------------------------------
#                        getReceiptField
# -------------------------------------------------------------------


@pytest.mark.integration
def test_getReceiptField_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_field: ReceiptField,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.addReceiptField(sample_receipt_field)

    # Act
    retrieved_field = client.getReceiptField(
        sample_receipt_field.field_type,
        sample_receipt_field.image_id,
        sample_receipt_field.receipt_id,
    )

    # Assert
    assert retrieved_field == sample_receipt_field


@pytest.mark.integration
def test_getReceiptField_nonexistent_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_field: ReceiptField,
):
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act & Assert
    with pytest.raises(ValueError, match="does not exist"):
        client.getReceiptField(
            sample_receipt_field.field_type,
            sample_receipt_field.image_id,
            sample_receipt_field.receipt_id,
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_params,expected_error",
    [
        (
            (None, "3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1),
            "Field type is required and cannot be None.",
        ),
        (
            ("BUSINESS_NAME", None, 1),
            "Image ID is required and cannot be None.",
        ),
        (
            ("BUSINESS_NAME", "3f52804b-2fad-4e00-92c8-b593da3a8ed3", None),
            "Receipt ID is required and cannot be None.",
        ),
        (
            ("", "3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1),
            "Field type must be a non-empty string.",
        ),
        (
            ("BUSINESS_NAME", "invalid-uuid", 1),
            "uuid must be a valid UUIDv4",
        ),
        (
            ("BUSINESS_NAME", "3f52804b-2fad-4e00-92c8-b593da3a8ed3", 0),
            "Receipt ID must be a positive integer.",
        ),
    ],
)
def test_getReceiptField_invalid_parameters(
    dynamodb_table,
    sample_receipt_field,
    mocker,
    invalid_params,
    expected_error,
):
    """
    Tests that getReceiptField raises ValueError for invalid parameters:
    - When any required parameter is None
    - When field_type is an empty string
    - When image_id is not a valid UUID
    - When receipt_id is not a positive integer
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match=expected_error):
        client.getReceiptField(*invalid_params)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_exception",
    [
        (
            "ProvisionedThroughputExceededException",
            "Provisioned throughput exceeded",
            "Provisioned throughput exceeded",
        ),
        (
            "ValidationException",
            "One or more parameters were invalid",
            "Validation error",
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
        ),
        ("AccessDeniedException", "Access denied", "Access denied"),
        (
            "UnknownError",
            "Unknown error",
            "Error getting receipt field",
        ),
    ],
)
def test_getReceiptField_client_errors(
    dynamodb_table,
    sample_receipt_field,
    mocker,
    error_code,
    error_message,
    expected_exception,
):
    """
    Tests that getReceiptField handles various client errors appropriately:
    - ProvisionedThroughputExceededException when throughput exceeded
    - ValidationException for invalid parameters
    - InternalServerError for server-side errors
    - AccessDeniedException for access denied errors
    - UnknownError for unexpected errors
    """
    client = DynamoClient(dynamodb_table)
    mock_get = mocker.patch.object(
        client._client,
        "get_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": error_code,
                    "Message": error_message,
                }
            },
            "GetItem",
        ),
    )

    with pytest.raises(Exception, match=expected_exception):
        client.getReceiptField(
            sample_receipt_field.field_type,
            sample_receipt_field.image_id,
            sample_receipt_field.receipt_id,
        )
    mock_get.assert_called_once()


# -------------------------------------------------------------------
#                        listReceiptFields
# -------------------------------------------------------------------


@pytest.mark.integration
def test_listReceiptFields_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_field: ReceiptField,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.addReceiptField(sample_receipt_field)

    # Create a second field
    second_field = ReceiptField(
        field_type="ADDRESS",
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=2,
        words=[
            {
                "word_id": 10,
                "line_id": 20,
                "label": "ADDRESS",
            }
        ],
        reasoning="This field appears to be the address",
        timestamp_added="2024-03-20T12:00:00Z",
    )
    client.addReceiptField(second_field)

    # Act
    fields, last_evaluated_key = client.listReceiptFields()

    # Assert
    assert len(fields) == 2
    assert sample_receipt_field in fields
    assert second_field in fields
    assert last_evaluated_key is None


@pytest.mark.integration
def test_listReceiptFields_with_limit(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_field: ReceiptField,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.addReceiptField(sample_receipt_field)

    # Create a second field
    second_field = ReceiptField(
        field_type="ADDRESS",
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=2,
        words=[
            {
                "word_id": 10,
                "line_id": 20,
                "label": "ADDRESS",
            }
        ],
        reasoning="This field appears to be the address",
        timestamp_added="2024-03-20T12:00:00Z",
    )
    client.addReceiptField(second_field)

    # Act
    fields, last_evaluated_key = client.listReceiptFields(limit=1)

    # Assert
    assert len(fields) == 1
    assert last_evaluated_key is not None


@pytest.mark.integration
def test_listReceiptFields_with_last_evaluated_key(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_field: ReceiptField,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.addReceiptField(sample_receipt_field)

    # Create a second field
    second_field = ReceiptField(
        field_type="ADDRESS",
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        receipt_id=2,
        words=[
            {
                "word_id": 10,
                "line_id": 20,
                "label": "ADDRESS",
            }
        ],
        reasoning="This field appears to be the address",
        timestamp_added="2024-03-20T12:00:00Z",
    )
    client.addReceiptField(second_field)

    # Get first page
    first_page, last_evaluated_key = client.listReceiptFields(limit=1)
    assert len(first_page) == 1
    assert last_evaluated_key is not None

    # Get second page
    second_page, last_evaluated_key = client.listReceiptFields(
        limit=1, lastEvaluatedKey=last_evaluated_key
    )
    assert len(second_page) == 1
    assert last_evaluated_key is None


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (
            {"limit": "not-an-int"},
            "Limit must be an integer",
        ),
        (
            {"limit": 0},
            "Limit must be greater than 0",
        ),
        (
            {"limit": -1},
            "Limit must be greater than 0",
        ),
        (
            {"lastEvaluatedKey": "not-a-dict"},
            "LastEvaluatedKey must be a dictionary",
        ),
        (
            {"lastEvaluatedKey": {}},
            "LastEvaluatedKey must contain keys: \\{['PK', 'SK']|['SK', 'PK']\\}",
        ),
        (
            {"lastEvaluatedKey": {"PK": "not-a-dict", "SK": {"S": "value"}}},
            "LastEvaluatedKey\\[PK\\] must be a dict containing a key 'S'",
        ),
    ],
)
def test_listReceiptFields_invalid_parameters(
    dynamodb_table,
    sample_receipt_field,
    mocker,
    invalid_input,
    expected_error,
):
    """
    Tests that listReceiptFields raises ValueError for invalid parameters:
    - When limit is not an integer
    - When limit is less than or equal to 0
    - When lastEvaluatedKey is not a dictionary
    - When lastEvaluatedKey is missing required keys
    - When lastEvaluatedKey values are not properly formatted
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match=expected_error):
        client.listReceiptFields(**invalid_input)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_exception",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Could not list receipt fields from the database",
        ),
        (
            "ProvisionedThroughputExceededException",
            "Provisioned throughput exceeded",
            "Provisioned throughput exceeded",
        ),
        (
            "ValidationException",
            "One or more parameters were invalid",
            "One or more parameters given were invalid",
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
        ),
        ("AccessDeniedException", "Access denied", "Access denied"),
        (
            "UnknownError",
            "Unknown error",
            "Could not list receipt fields from the database",
        ),
    ],
)
def test_listReceiptFields_client_errors(
    dynamodb_table,
    sample_receipt_field,
    mocker,
    error_code,
    error_message,
    expected_exception,
):
    """
    Tests that listReceiptFields handles various client errors appropriately:
    - ResourceNotFoundException when table not found
    - ProvisionedThroughputExceededException when throughput exceeded
    - ValidationException for invalid parameters
    - InternalServerError for server-side errors
    - AccessDeniedException for access denied errors
    - UnknownError for unexpected errors
    """
    client = DynamoClient(dynamodb_table)
    mock_query = mocker.patch.object(
        client._client,
        "query",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": error_code,
                    "Message": error_message,
                }
            },
            "Query",
        ),
    )

    with pytest.raises(Exception, match=expected_exception):
        client.listReceiptFields()
    mock_query.assert_called_once()


@pytest.mark.integration
def test_listReceiptFields_pagination_errors(
    dynamodb_table,
    sample_receipt_field,
    mocker,
):
    """
    Tests that listReceiptFields handles pagination errors appropriately:
    - When the first query fails
    - When a subsequent query fails
    """
    client = DynamoClient(dynamodb_table)

    # Test first query failure
    mock_query = mocker.patch.object(
        client._client,
        "query",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ResourceNotFoundException",
                    "Message": "Table not found during pagination",
                }
            },
            "Query",
        ),
    )

    with pytest.raises(
        Exception, match="Could not list receipt fields from the database"
    ):
        client.listReceiptFields()
    mock_query.assert_called_once()

    # Test subsequent query failure
    mock_query.reset_mock()
    mock_query.side_effect = [
        {
            "Items": [sample_receipt_field.to_item()],
            "LastEvaluatedKey": {"PK": {"S": "value"}, "SK": {"S": "value"}},
        },
        ClientError(
            {
                "Error": {
                    "Code": "UnknownError",
                    "Message": "An unexpected error occurred",
                }
            },
            "Query",
        ),
    ]

    with pytest.raises(
        Exception, match="Could not list receipt fields from the database"
    ):
        client.listReceiptFields()
    assert mock_query.call_count == 2


# -------------------------------------------------------------------
#                        getReceiptFieldsByImage
# -------------------------------------------------------------------


@pytest.mark.integration
def test_getReceiptFieldsByImage_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_field: ReceiptField,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.addReceiptField(sample_receipt_field)

    # Create a second field with the same image_id
    second_field = ReceiptField(
        field_type="ADDRESS",
        image_id=sample_receipt_field.image_id,
        receipt_id=2,
        words=[
            {
                "word_id": 10,
                "line_id": 20,
                "label": "ADDRESS",
            }
        ],
        reasoning="This field appears to be the address",
        timestamp_added="2024-03-20T12:00:00Z",
    )
    client.addReceiptField(second_field)

    # Act
    fields, last_evaluated_key = client.getReceiptFieldsByImage(
        sample_receipt_field.image_id
    )

    # Assert
    assert len(fields) == 2
    assert sample_receipt_field in fields
    assert second_field in fields
    assert last_evaluated_key is None


@pytest.mark.integration
def test_getReceiptFieldsByImage_with_limit(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_field: ReceiptField,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.addReceiptField(sample_receipt_field)

    # Create a second field with the same image_id
    second_field = ReceiptField(
        field_type="ADDRESS",
        image_id=sample_receipt_field.image_id,
        receipt_id=2,
        words=[
            {
                "word_id": 10,
                "line_id": 20,
                "label": "ADDRESS",
            }
        ],
        reasoning="This field appears to be the address",
        timestamp_added="2024-03-20T12:00:00Z",
    )
    client.addReceiptField(second_field)

    # Act
    fields, last_evaluated_key = client.getReceiptFieldsByImage(
        sample_receipt_field.image_id, limit=1
    )

    # Assert
    assert len(fields) == 1
    assert last_evaluated_key is not None


@pytest.mark.integration
def test_getReceiptFieldsByImage_with_last_evaluated_key(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_field: ReceiptField,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.addReceiptField(sample_receipt_field)

    # Create a second field with the same image_id
    second_field = ReceiptField(
        field_type="ADDRESS",
        image_id=sample_receipt_field.image_id,
        receipt_id=2,
        words=[
            {
                "word_id": 10,
                "line_id": 20,
                "label": "ADDRESS",
            }
        ],
        reasoning="This field appears to be the address",
        timestamp_added="2024-03-20T12:00:00Z",
    )
    client.addReceiptField(second_field)

    # Get first page
    first_page, last_evaluated_key = client.getReceiptFieldsByImage(
        sample_receipt_field.image_id, limit=1
    )
    assert len(first_page) == 1
    assert last_evaluated_key is not None

    # Get second page
    second_page, last_evaluated_key = client.getReceiptFieldsByImage(
        sample_receipt_field.image_id,
        limit=1,
        lastEvaluatedKey=last_evaluated_key,
    )
    assert len(second_page) == 1
    assert last_evaluated_key is None


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (
            {"image_id": None},
            "Image ID must be a string",
        ),
        (
            {"image_id": "invalid-uuid"},
            "uuid must be a valid UUIDv4",
        ),
        (
            {
                "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                "limit": "not-an-int",
            },
            "Limit must be an integer",
        ),
        (
            {"image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3", "limit": 0},
            "Limit must be greater than 0",
        ),
        (
            {"image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3", "limit": -1},
            "Limit must be greater than 0",
        ),
        (
            {
                "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                "lastEvaluatedKey": "not-a-dict",
            },
            "LastEvaluatedKey must be a dictionary",
        ),
        (
            {
                "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                "lastEvaluatedKey": {},
            },
            "LastEvaluatedKey must contain keys: \\{['PK', 'SK']|['SK', 'PK']\\}",
        ),
        (
            {
                "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                "lastEvaluatedKey": {"PK": "not-a-dict", "SK": {"S": "value"}},
            },
            "LastEvaluatedKey\\[PK\\] must be a dict containing a key 'S'",
        ),
    ],
)
def test_getReceiptFieldsByImage_invalid_parameters(
    dynamodb_table,
    sample_receipt_field,
    mocker,
    invalid_input,
    expected_error,
):
    """
    Tests that getReceiptFieldsByImage raises ValueError for invalid parameters:
    - When image_id is None or not a valid UUID
    - When limit is not an integer
    - When limit is less than or equal to 0
    - When lastEvaluatedKey is not a dictionary
    - When lastEvaluatedKey is missing required keys
    - When lastEvaluatedKey values are not properly formatted
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match=expected_error):
        client.getReceiptFieldsByImage(**invalid_input)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_exception",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Could not list receipt fields by image ID",
        ),
        (
            "ProvisionedThroughputExceededException",
            "Provisioned throughput exceeded",
            "Provisioned throughput exceeded",
        ),
        (
            "ValidationException",
            "One or more parameters were invalid",
            "One or more parameters given were invalid",
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
        ),
        ("AccessDeniedException", "Access denied", "Access denied"),
        (
            "UnknownError",
            "Unknown error",
            "Could not list receipt fields by image ID",
        ),
    ],
)
def test_getReceiptFieldsByImage_client_errors(
    dynamodb_table,
    sample_receipt_field,
    mocker,
    error_code,
    error_message,
    expected_exception,
):
    """
    Tests that getReceiptFieldsByImage handles various client errors appropriately:
    - ResourceNotFoundException when table not found
    - ProvisionedThroughputExceededException when throughput exceeded
    - ValidationException for invalid parameters
    - InternalServerError for server-side errors
    - AccessDeniedException for access denied errors
    - UnknownError for unexpected errors
    """
    client = DynamoClient(dynamodb_table)
    mock_query = mocker.patch.object(
        client._client,
        "query",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": error_code,
                    "Message": error_message,
                }
            },
            "Query",
        ),
    )

    with pytest.raises(Exception, match=expected_exception):
        client.getReceiptFieldsByImage(sample_receipt_field.image_id)
    mock_query.assert_called_once()


@pytest.mark.integration
def test_getReceiptFieldsByImage_pagination_errors(
    dynamodb_table,
    sample_receipt_field,
    mocker,
):
    """
    Tests that getReceiptFieldsByImage handles pagination errors appropriately:
    - When the first query fails
    - When a subsequent query fails
    """
    client = DynamoClient(dynamodb_table)

    # Test first query failure
    mock_query = mocker.patch.object(
        client._client,
        "query",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ResourceNotFoundException",
                    "Message": "Table not found during pagination",
                }
            },
            "Query",
        ),
    )

    with pytest.raises(
        Exception, match="Could not list receipt fields by image ID"
    ):
        client.getReceiptFieldsByImage(sample_receipt_field.image_id)
    mock_query.assert_called_once()

    # Test subsequent query failure
    mock_query.reset_mock()
    mock_query.side_effect = [
        {
            "Items": [sample_receipt_field.to_item()],
            "LastEvaluatedKey": {"PK": {"S": "value"}, "SK": {"S": "value"}},
        },
        ClientError(
            {
                "Error": {
                    "Code": "UnknownError",
                    "Message": "An unexpected error occurred",
                }
            },
            "Query",
        ),
    ]

    with pytest.raises(
        Exception, match="Could not list receipt fields by image ID"
    ):
        client.getReceiptFieldsByImage(sample_receipt_field.image_id)
    assert mock_query.call_count == 2


# -------------------------------------------------------------------
#                        getReceiptFieldsByReceipt
# -------------------------------------------------------------------


@pytest.mark.integration
def test_getReceiptFieldsByReceipt_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_field: ReceiptField,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.addReceiptField(sample_receipt_field)

    # Create a second field with the same image_id and receipt_id
    second_field = ReceiptField(
        field_type="ADDRESS",
        image_id=sample_receipt_field.image_id,
        receipt_id=sample_receipt_field.receipt_id,
        words=[
            {
                "word_id": 10,
                "line_id": 20,
                "label": "ADDRESS",
            }
        ],
        reasoning="This field appears to be the address",
        timestamp_added="2024-03-20T12:00:00Z",
    )
    client.addReceiptField(second_field)

    # Act
    fields, last_evaluated_key = client.getReceiptFieldsByReceipt(
        sample_receipt_field.image_id,
        sample_receipt_field.receipt_id,
    )

    # Assert
    assert len(fields) == 2
    assert sample_receipt_field in fields
    assert second_field in fields
    assert last_evaluated_key is None


@pytest.mark.integration
def test_getReceiptFieldsByReceipt_with_limit(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_field: ReceiptField,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.addReceiptField(sample_receipt_field)

    # Create a second field with the same image_id and receipt_id
    second_field = ReceiptField(
        field_type="ADDRESS",
        image_id=sample_receipt_field.image_id,
        receipt_id=sample_receipt_field.receipt_id,
        words=[
            {
                "word_id": 10,
                "line_id": 20,
                "label": "ADDRESS",
            }
        ],
        reasoning="This field appears to be the address",
        timestamp_added="2024-03-20T12:00:00Z",
    )
    client.addReceiptField(second_field)

    # Act
    fields, last_evaluated_key = client.getReceiptFieldsByReceipt(
        sample_receipt_field.image_id,
        sample_receipt_field.receipt_id,
        limit=1,
    )

    # Assert
    assert len(fields) == 1
    assert last_evaluated_key is not None


@pytest.mark.integration
def test_getReceiptFieldsByReceipt_with_last_evaluated_key(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_field: ReceiptField,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.addReceiptField(sample_receipt_field)

    # Create a second field with the same image_id and receipt_id
    second_field = ReceiptField(
        field_type="ADDRESS",
        image_id=sample_receipt_field.image_id,
        receipt_id=sample_receipt_field.receipt_id,
        words=[
            {
                "word_id": 10,
                "line_id": 20,
                "label": "ADDRESS",
            }
        ],
        reasoning="This field appears to be the address",
        timestamp_added="2024-03-20T12:00:00Z",
    )
    client.addReceiptField(second_field)

    # Get first page
    first_page, last_evaluated_key = client.getReceiptFieldsByReceipt(
        sample_receipt_field.image_id,
        sample_receipt_field.receipt_id,
        limit=1,
    )
    assert len(first_page) == 1
    assert last_evaluated_key is not None

    # Get second page
    second_page, last_evaluated_key = client.getReceiptFieldsByReceipt(
        sample_receipt_field.image_id,
        sample_receipt_field.receipt_id,
        limit=1,
        lastEvaluatedKey=last_evaluated_key,
    )
    assert len(second_page) == 1
    assert last_evaluated_key is None


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (
            {"image_id": None, "receipt_id": 1},
            "Image ID must be a string",
        ),
        (
            {"image_id": "invalid-uuid", "receipt_id": 1},
            "uuid must be a valid UUIDv4",
        ),
        (
            {
                "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                "receipt_id": None,
            },
            "Receipt ID must be a positive integer",
        ),
        (
            {
                "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                "receipt_id": 0,
            },
            "Receipt ID must be a positive integer",
        ),
        (
            {
                "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                "receipt_id": 1,
                "limit": "not-an-int",
            },
            "Limit must be an integer",
        ),
        (
            {
                "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                "receipt_id": 1,
                "limit": 0,
            },
            "Limit must be greater than 0",
        ),
        (
            {
                "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                "receipt_id": 1,
                "limit": -1,
            },
            "Limit must be greater than 0",
        ),
        (
            {
                "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                "receipt_id": 1,
                "lastEvaluatedKey": "not-a-dict",
            },
            "LastEvaluatedKey must be a dictionary",
        ),
        (
            {
                "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                "receipt_id": 1,
                "lastEvaluatedKey": {},
            },
            "LastEvaluatedKey must contain keys: \\{['PK', 'SK']|['SK', 'PK']\\}",
        ),
        (
            {
                "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                "receipt_id": 1,
                "lastEvaluatedKey": {"PK": "not-a-dict", "SK": {"S": "value"}},
            },
            "LastEvaluatedKey\\[PK\\] must be a dict containing a key 'S'",
        ),
    ],
)
def test_getReceiptFieldsByReceipt_invalid_parameters(
    dynamodb_table,
    sample_receipt_field,
    mocker,
    invalid_input,
    expected_error,
):
    """
    Tests that getReceiptFieldsByReceipt raises ValueError for invalid parameters:
    - When image_id is None or not a valid UUID
    - When receipt_id is None or not a positive integer
    - When limit is not an integer
    - When limit is less than or equal to 0
    - When lastEvaluatedKey is not a dictionary
    - When lastEvaluatedKey is missing required keys
    - When lastEvaluatedKey values are not properly formatted
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match=expected_error):
        client.getReceiptFieldsByReceipt(**invalid_input)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_exception",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Could not list receipt fields by receipt ID",
        ),
        (
            "ProvisionedThroughputExceededException",
            "Provisioned throughput exceeded",
            "Provisioned throughput exceeded",
        ),
        (
            "ValidationException",
            "One or more parameters were invalid",
            "One or more parameters given were invalid",
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
        ),
        ("AccessDeniedException", "Access denied", "Access denied"),
        (
            "UnknownError",
            "Unknown error",
            "Could not list receipt fields by receipt ID",
        ),
    ],
)
def test_getReceiptFieldsByReceipt_client_errors(
    dynamodb_table,
    sample_receipt_field,
    mocker,
    error_code,
    error_message,
    expected_exception,
):
    """
    Tests that getReceiptFieldsByReceipt handles various client errors appropriately:
    - ResourceNotFoundException when table not found
    - ProvisionedThroughputExceededException when throughput exceeded
    - ValidationException for invalid parameters
    - InternalServerError for server-side errors
    - AccessDeniedException for access denied errors
    - UnknownError for unexpected errors
    """
    client = DynamoClient(dynamodb_table)
    mock_query = mocker.patch.object(
        client._client,
        "query",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": error_code,
                    "Message": error_message,
                }
            },
            "Query",
        ),
    )

    with pytest.raises(Exception, match=expected_exception):
        client.getReceiptFieldsByReceipt(
            sample_receipt_field.image_id,
            sample_receipt_field.receipt_id,
        )
    mock_query.assert_called_once()


@pytest.mark.integration
def test_getReceiptFieldsByReceipt_pagination_errors(
    dynamodb_table,
    sample_receipt_field,
    mocker,
):
    """
    Tests that getReceiptFieldsByReceipt handles pagination errors appropriately:
    - When the first query fails
    - When a subsequent query fails
    """
    client = DynamoClient(dynamodb_table)

    # Test first query failure
    mock_query = mocker.patch.object(
        client._client,
        "query",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ResourceNotFoundException",
                    "Message": "Table not found during pagination",
                }
            },
            "Query",
        ),
    )

    with pytest.raises(
        Exception, match="Could not list receipt fields by receipt ID"
    ):
        client.getReceiptFieldsByReceipt(
            sample_receipt_field.image_id,
            sample_receipt_field.receipt_id,
        )
    mock_query.assert_called_once()

    # Test subsequent query failure
    mock_query.reset_mock()
    mock_query.side_effect = [
        {
            "Items": [sample_receipt_field.to_item()],
            "LastEvaluatedKey": {"PK": {"S": "value"}, "SK": {"S": "value"}},
        },
        ClientError(
            {
                "Error": {
                    "Code": "UnknownError",
                    "Message": "An unexpected error occurred",
                }
            },
            "Query",
        ),
    ]

    with pytest.raises(
        Exception, match="Could not list receipt fields by receipt ID"
    ):
        client.getReceiptFieldsByReceipt(
            sample_receipt_field.image_id,
            sample_receipt_field.receipt_id,
        )
    assert mock_query.call_count == 2
