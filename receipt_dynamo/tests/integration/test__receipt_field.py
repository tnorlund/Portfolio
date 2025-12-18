from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Type

import pytest
from botocore.exceptions import ClientError, ParamValidationError

from receipt_dynamo import DynamoClient, ReceiptField
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBAccessError,
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    DynamoDBValidationError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
)

# This entity is not used in production infrastructure
pytestmark = [pytest.mark.integration, pytest.mark.unused_in_production]

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
        timestamp_added=datetime.fromisoformat("2024-03-20T12:00:00+00:00"),
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
    client.add_receipt_field(sample_receipt_field)

    # Assert
    retrieved_field = client.get_receipt_field(
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
    client.add_receipt_field(sample_receipt_field)

    # Act & Assert
    with pytest.raises(EntityAlreadyExistsError, match="already exists"):
        client.add_receipt_field(sample_receipt_field)


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "receipt_field cannot be None"),
        (
            "not-a-receipt-field",
            "receiptField must be an instance of the ReceiptField class.",
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
        client.add_receipt_field(invalid_input)  # type: ignore


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
            "Table not found for operation add_receipt_field",
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
            "One or more parameters given were invalid",
            "One or more parameters given were invalid",
        ),
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied for add_receipt_field",
        ),
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
        client.add_receipt_field(sample_receipt_field)
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
            timestamp_added=datetime.fromisoformat("2024-03-20T12:00:00+00:00"),
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
            timestamp_added=datetime.fromisoformat("2024-03-20T12:00:00+00:00"),
        ),
    ]

    # Act
    client.add_receipt_fields(fields)

    # Assert
    for field in fields:
        retrieved_field = client.get_receipt_field(
            field.field_type,
            field.image_id,
            field.receipt_id,
        )
        assert retrieved_field == field


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "receipt_fields cannot be None"),
        (
            "not-a-list",
            "receipt_fields must be a list of ReceiptField instances.",
        ),
        (
            [1, 2, 3],
            "All receipt_fields must be instances of the ReceiptField class.",
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
        client.add_receipt_fields(invalid_input)  # type: ignore


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
            "One or more parameters given were invalid",
            "One or more parameters given were invalid",
        ),
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied for add_receipt_fields",
        ),
        (
            "UnknownError",
            "Unknown error",
            "Could not add receipt field to DynamoDB",
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
        client.add_receipt_fields(fields)
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
                        {"PutRequest": {"Item": sample_receipt_field.to_item()}}
                    ]
                }
            },
            {},  # Empty response on second call
        ],
    )

    client.add_receipt_fields(fields)

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
    client.add_receipt_field(sample_receipt_field)

    # Update the field
    updated_field = ReceiptField(
        field_type=sample_receipt_field.field_type,
        image_id=sample_receipt_field.image_id,
        receipt_id=sample_receipt_field.receipt_id,
        words=sample_receipt_field.words,
        reasoning="Updated reasoning",
        timestamp_added=datetime.fromisoformat(sample_receipt_field.timestamp_added),
    )

    # Act
    client.update_receipt_field(updated_field)

    # Assert
    retrieved_field = client.get_receipt_field(
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
    with pytest.raises(EntityNotFoundError, match="does not exist"):
        client.update_receipt_field(sample_receipt_field)


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "receipt_field cannot be None"),
        (
            "not-a-receipt-field",
            "receiptField must be an instance of the ReceiptField class.",
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
        client.update_receipt_field(invalid_input)  # type: ignore


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
            "One or more parameters given were invalid",
            "One or more parameters given were invalid",
        ),
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied for update_receipt_field",
        ),
        (
            "UnknownError",
            "Unknown error",
            "Could not update receipt field in DynamoDB",
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
        client.update_receipt_field(sample_receipt_field)
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
    client.add_receipt_field(sample_receipt_field)

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
        timestamp_added=datetime.fromisoformat("2024-03-20T12:00:00+00:00"),
    )
    client.add_receipt_field(second_field)

    # Update both fields
    updated_fields = [
        ReceiptField(
            field_type=sample_receipt_field.field_type,
            image_id=sample_receipt_field.image_id,
            receipt_id=sample_receipt_field.receipt_id,
            words=sample_receipt_field.words,
            reasoning="Updated reasoning 1",
            timestamp_added=datetime.fromisoformat(
                sample_receipt_field.timestamp_added
            ),
        ),
        ReceiptField(
            field_type=second_field.field_type,
            image_id=second_field.image_id,
            receipt_id=second_field.receipt_id,
            words=second_field.words,
            reasoning="Updated reasoning 2",
            timestamp_added=datetime.fromisoformat(second_field.timestamp_added),
        ),
    ]

    # Act
    client.update_receipt_fields(updated_fields)

    # Assert
    for field in updated_fields:
        retrieved_field = client.get_receipt_field(
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
        match="One or more items do not exist",
    ):
        client.update_receipt_fields(fields)


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "receipt_fields cannot be None"),
        (
            "not-a-list",
            "receipt_fields must be a list of ReceiptField instances.",
        ),
        (
            [1, 2, 3],
            "All receipt_fields must be instances of the ReceiptField class.",
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
        client.update_receipt_fields(invalid_input)  # type: ignore


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error,expected_exception",
    [
        (
            "ConditionalCheckFailedException",
            "One or more items do not exist",
            "One or more items do not exist",
            EntityNotFoundError,
        ),
        (
            "ProvisionedThroughputExceededException",
            "Provisioned throughput exceeded",
            "Provisioned throughput exceeded",
            DynamoDBThroughputError,
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
            DynamoDBServerError,
        ),
        (
            "ValidationException",
            "One or more parameters given were invalid",
            "One or more parameters given were invalid",
            DynamoDBValidationError,
        ),
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied",
            DynamoDBAccessError,
        ),
        (
            "UnknownError",
            "Unknown error",
            "Could not update receipt field in DynamoDB",
            DynamoDBError,
        ),
    ],
)
def test_updateReceiptFields_client_errors(
    dynamodb_table,
    sample_receipt_field,
    mocker,
    error_code,
    error_message,
    expected_error,
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

    with pytest.raises(expected_exception, match=expected_error):
        client.update_receipt_fields(fields)
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
            timestamp_added=datetime.fromisoformat("2024-03-20T12:00:00+00:00"),
        )
        for i in range(1, 31)
    ]

    # Mock the transact_write_items method
    mock_transact_write = mocker.patch.object(
        client._client,
        "transact_write_items",
        return_value={},
    )

    client.update_receipt_fields(fields)

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
    client.add_receipt_field(sample_receipt_field)

    # Act
    client.delete_receipt_field(sample_receipt_field)

    # Assert
    with pytest.raises(EntityNotFoundError, match="does not exist"):
        client.get_receipt_field(
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
    with pytest.raises(EntityNotFoundError, match="does not exist"):
        client.delete_receipt_field(sample_receipt_field)


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "receipt_field cannot be None"),
        (
            "not-a-receipt-field",
            "receiptField must be an instance of the ReceiptField class.",
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
        client.delete_receipt_field(invalid_input)  # type: ignore


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
            "One or more parameters given were invalid",
            "One or more parameters given were invalid",
        ),
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied for delete_receipt_field",
        ),
        (
            "UnknownError",
            "Unknown error",
            "Could not delete receipt field from DynamoDB",
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
        client.delete_receipt_field(sample_receipt_field)
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
    client.add_receipt_field(sample_receipt_field)

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
        timestamp_added=datetime.fromisoformat("2024-03-20T12:00:00+00:00"),
    )
    client.add_receipt_field(second_field)

    fields = [sample_receipt_field, second_field]

    # Act
    client.delete_receipt_fields(fields)

    # Assert
    for field in fields:
        with pytest.raises(EntityNotFoundError, match="does not exist"):
            client.get_receipt_field(
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
        ValueError,
        match="One or more items do not exist",
    ):
        client.delete_receipt_fields([sample_receipt_field])


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "receipt_fields cannot be None"),
        (
            "not-a-list",
            "receipt_fields must be a list of ReceiptField instances.",
        ),
        (
            [1, 2, 3],
            "All receipt_fields must be instances of the ReceiptField class.",
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
        client.delete_receipt_fields(invalid_input)  # type: ignore


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_exception",
    [
        (
            "ConditionalCheckFailedException",
            "One or more items do not exist",
            "One or more items do not exist",
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
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied",
        ),
        (
            "UnknownError",
            "Unknown error",
            "Could not delete receipt field from DynamoDB",
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
        client.delete_receipt_fields(fields)
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
            timestamp_added=datetime.fromisoformat("2024-03-20T12:00:00+00:00"),
        )
        for i in range(1, 31)
    ]

    # Mock the transact_write_items method
    mock_transact_write = mocker.patch.object(
        client._client,
        "transact_write_items",
        return_value={},
    )

    client.delete_receipt_fields(fields)

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
    client.add_receipt_field(sample_receipt_field)

    # Act
    retrieved_field = client.get_receipt_field(
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
    with pytest.raises(EntityNotFoundError, match="does not exist"):
        client.get_receipt_field(
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
            "field_type cannot be None",
        ),
        (
            ("BUSINESS_NAME", None, 1),
            "image_id cannot be None",
        ),
        (
            ("BUSINESS_NAME", "3f52804b-2fad-4e00-92c8-b593da3a8ed3", None),
            "receipt_id cannot be None",
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
        client.get_receipt_field(*invalid_params)


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
            "One or more parameters given were invalid",
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
        client.get_receipt_field(
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
    client.add_receipt_field(sample_receipt_field)

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
        timestamp_added=datetime.fromisoformat("2024-03-20T12:00:00+00:00"),
    )
    client.add_receipt_field(second_field)

    # Act
    fields, last_evaluated_key = client.list_receipt_fields()

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
    client.add_receipt_field(sample_receipt_field)

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
        timestamp_added=datetime.fromisoformat("2024-03-20T12:00:00+00:00"),
    )
    client.add_receipt_field(second_field)

    # Act
    fields, last_evaluated_key = client.list_receipt_fields(limit=1)

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
    client.add_receipt_field(sample_receipt_field)

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
        timestamp_added=datetime.fromisoformat("2024-03-20T12:00:00+00:00"),
    )
    client.add_receipt_field(second_field)

    # Get first page
    first_page, last_evaluated_key = client.list_receipt_fields(limit=1)
    assert len(first_page) == 1
    assert last_evaluated_key is not None

    # Get second page
    second_page, last_evaluated_key = client.list_receipt_fields(
        limit=1, last_evaluated_key=last_evaluated_key
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
            {"last_evaluated_key": "not-a-dict"},
            "LastEvaluatedKey must be a dictionary",
        ),
        (
            {"last_evaluated_key": {}},
            "LastEvaluatedKey must contain keys: \\{['PK', 'SK']|['SK', 'PK']\\}",
        ),
        (
            {"last_evaluated_key": {"PK": "not-a-dict", "SK": {"S": "value"}}},
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
    - When last_evaluated_key is not a dictionary
    - When last_evaluated_key is missing required keys
    - When last_evaluated_key values are not properly formatted
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match=expected_error):
        client.list_receipt_fields(**invalid_input)


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
            "One or more parameters given were invalid",
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
        client.list_receipt_fields()
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
        client.list_receipt_fields()
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
        client.list_receipt_fields()
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
    client.add_receipt_field(sample_receipt_field)

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
        timestamp_added=datetime.fromisoformat("2024-03-20T12:00:00+00:00"),
    )
    client.add_receipt_field(second_field)

    # Act
    fields, last_evaluated_key = client.get_receipt_fields_by_image(
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
    client.add_receipt_field(sample_receipt_field)

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
        timestamp_added=datetime.fromisoformat("2024-03-20T12:00:00+00:00"),
    )
    client.add_receipt_field(second_field)

    # Act
    fields, last_evaluated_key = client.get_receipt_fields_by_image(
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
    client.add_receipt_field(sample_receipt_field)

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
        timestamp_added=datetime.fromisoformat("2024-03-20T12:00:00+00:00"),
    )
    client.add_receipt_field(second_field)

    # Get first page
    first_page, last_evaluated_key = client.get_receipt_fields_by_image(
        sample_receipt_field.image_id, limit=1
    )
    assert len(first_page) == 1
    assert last_evaluated_key is not None

    # Get second page
    second_page, last_evaluated_key = client.get_receipt_fields_by_image(
        sample_receipt_field.image_id,
        limit=1,
        last_evaluated_key=last_evaluated_key,
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
                "last_evaluated_key": "not-a-dict",
            },
            "LastEvaluatedKey must be a dictionary",
        ),
        (
            {
                "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                "last_evaluated_key": {},
            },
            "LastEvaluatedKey must contain keys: \\{['PK', 'SK']|['SK', 'PK']\\}",
        ),
        (
            {
                "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                "last_evaluated_key": {
                    "PK": "not-a-dict",
                    "SK": {"S": "value"},
                },
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
    - When last_evaluated_key is not a dictionary
    - When last_evaluated_key is missing required keys
    - When last_evaluated_key values are not properly formatted
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match=expected_error):
        client.get_receipt_fields_by_image(**invalid_input)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error,expected_exception",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Could not list receipt fields by image ID",
            DynamoDBError,
        ),
        (
            "ProvisionedThroughputExceededException",
            "Provisioned throughput exceeded",
            "Provisioned throughput exceeded",
            DynamoDBThroughputError,
        ),
        (
            "ValidationException",
            "One or more parameters given were invalid",
            "One or more parameters given were invalid",
            DynamoDBValidationError,
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
            DynamoDBServerError,
        ),
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied",
            DynamoDBAccessError,
        ),
        (
            "UnknownError",
            "Unknown error",
            "Could not list receipt fields by image ID",
            DynamoDBError,
        ),
    ],
)
def test_getReceiptFieldsByImage_client_errors(
    dynamodb_table,
    sample_receipt_field,
    mocker,
    error_code,
    error_message,
    expected_error,
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

    with pytest.raises(expected_exception, match=expected_error):
        client.get_receipt_fields_by_image(sample_receipt_field.image_id)
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
        DynamoDBError, match="Could not list receipt fields by image ID"
    ):
        client.get_receipt_fields_by_image(sample_receipt_field.image_id)
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
        DynamoDBError, match="Could not list receipt fields by image ID"
    ):
        client.get_receipt_fields_by_image(sample_receipt_field.image_id)
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
    client.add_receipt_field(sample_receipt_field)

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
        timestamp_added=datetime.fromisoformat("2024-03-20T12:00:00+00:00"),
    )
    client.add_receipt_field(second_field)

    # Act
    fields, last_evaluated_key = client.get_receipt_fields_by_receipt(
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
    client.add_receipt_field(sample_receipt_field)

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
        timestamp_added=datetime.fromisoformat("2024-03-20T12:00:00+00:00"),
    )
    client.add_receipt_field(second_field)

    # Act
    fields, last_evaluated_key = client.get_receipt_fields_by_receipt(
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
    client.add_receipt_field(sample_receipt_field)

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
        timestamp_added=datetime.fromisoformat("2024-03-20T12:00:00+00:00"),
    )
    client.add_receipt_field(second_field)

    # Get first page
    first_page, last_evaluated_key = client.get_receipt_fields_by_receipt(
        sample_receipt_field.image_id,
        sample_receipt_field.receipt_id,
        limit=1,
    )
    assert len(first_page) == 1
    assert last_evaluated_key is not None

    # Get second page
    second_page, last_evaluated_key = client.get_receipt_fields_by_receipt(
        sample_receipt_field.image_id,
        sample_receipt_field.receipt_id,
        limit=1,
        last_evaluated_key=last_evaluated_key,
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
                "last_evaluated_key": "not-a-dict",
            },
            "LastEvaluatedKey must be a dictionary",
        ),
        (
            {
                "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                "receipt_id": 1,
                "last_evaluated_key": {},
            },
            "LastEvaluatedKey must contain keys: \\{['PK', 'SK']|['SK', 'PK']\\}",
        ),
        (
            {
                "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
                "receipt_id": 1,
                "last_evaluated_key": {
                    "PK": "not-a-dict",
                    "SK": {"S": "value"},
                },
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
    - When last_evaluated_key is not a dictionary
    - When last_evaluated_key is missing required keys
    - When last_evaluated_key values are not properly formatted
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match=expected_error):
        client.get_receipt_fields_by_receipt(**invalid_input)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error,expected_exception",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Could not list receipt fields by receipt ID",
            DynamoDBError,
        ),
        (
            "ProvisionedThroughputExceededException",
            "Provisioned throughput exceeded",
            "Provisioned throughput exceeded",
            DynamoDBThroughputError,
        ),
        (
            "ValidationException",
            "One or more parameters given were invalid",
            "One or more parameters given were invalid",
            DynamoDBValidationError,
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
            DynamoDBServerError,
        ),
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied",
            DynamoDBAccessError,
        ),
        (
            "UnknownError",
            "Unknown error",
            "Could not list receipt fields by receipt ID",
            DynamoDBError,
        ),
    ],
)
def test_getReceiptFieldsByReceipt_client_errors(
    dynamodb_table,
    sample_receipt_field,
    mocker,
    error_code,
    error_message,
    expected_error,
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

    with pytest.raises(expected_exception, match=expected_error):
        client.get_receipt_fields_by_receipt(
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
        DynamoDBError, match="Could not list receipt fields by receipt ID"
    ):
        client.get_receipt_fields_by_receipt(
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
        DynamoDBError, match="Could not list receipt fields by receipt ID"
    ):
        client.get_receipt_fields_by_receipt(
            sample_receipt_field.image_id,
            sample_receipt_field.receipt_id,
        )
    assert mock_query.call_count == 2
