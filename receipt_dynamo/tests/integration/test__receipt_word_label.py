from datetime import datetime
from typing import Any, Dict, List, Literal, Optional, Type

import pytest
from botocore.exceptions import ClientError, ParamValidationError

from receipt_dynamo import DynamoClient, ReceiptWordLabel
from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBAccessError,
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    DynamoDBValidationError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
)

# -------------------------------------------------------------------
#                        FIXTURES
# -------------------------------------------------------------------


@pytest.fixture
def sample_receipt_word_label():
    return ReceiptWordLabel(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=10,
        word_id=5,
        label="ITEM",
        reasoning="This word appears to be an item description",
        timestamp_added=datetime.fromisoformat("2024-03-20T12:00:00+00:00"),
        validation_status="VALID",
    )


# -------------------------------------------------------------------
#                        addReceiptWordLabel
# -------------------------------------------------------------------


@pytest.mark.integration
def test_addReceiptWordLabel_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_word_label: ReceiptWordLabel,
):
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act
    client.add_receipt_word_label(sample_receipt_word_label)

    # Assert
    retrieved_label = client.get_receipt_word_label(
        sample_receipt_word_label.image_id,
        sample_receipt_word_label.receipt_id,
        sample_receipt_word_label.line_id,
        sample_receipt_word_label.word_id,
        sample_receipt_word_label.label,
    )
    assert retrieved_label == sample_receipt_word_label


@pytest.mark.integration
def test_addReceiptWordLabel_duplicate_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_word_label: ReceiptWordLabel,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.add_receipt_word_label(sample_receipt_word_label)

    # Act & Assert
    with pytest.raises(EntityAlreadyExistsError, match="already exists"):
        client.add_receipt_word_label(sample_receipt_word_label)


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "receipt_word_label cannot be None"),
        (
            "not-a-receipt-word-label",
            "receipt_word_label must be an instance of the ReceiptWordLabel class.",
        ),
    ],
)
def test_addReceiptWordLabel_invalid_parameters(
    dynamodb_table,
    sample_receipt_word_label,
    mocker,
    invalid_input,
    expected_error,
):
    """
    Tests that addReceiptWordLabel raises ValueError for invalid parameters:
    - When receipt word label is None
    - When receipt word label is not an instance of ReceiptWordLabel
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match=expected_error):
        client.add_receipt_word_label(invalid_input)  # type: ignore


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
            "Could not add receipt word label to DynamoDB",
        ),
        (
            "ValidationException",
            "One or more parameters given were invalid",
            "One or more parameters given were invalid",
        ),
        ("AccessDeniedException", "Access denied", "Access denied"),
    ],
)
def test_addReceiptWordLabel_client_errors(
    dynamodb_table,
    sample_receipt_word_label,
    mocker,
    error_code,
    error_message,
    expected_exception,
):
    """
    Tests that addReceiptWordLabel handles various client errors appropriately:
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

    # Map error codes to expected exception types
    exception_mapping = {
        "ConditionalCheckFailedException": EntityAlreadyExistsError,
        "ResourceNotFoundException": DynamoDBError,
        "ProvisionedThroughputExceededException": DynamoDBThroughputError,
        "InternalServerError": DynamoDBServerError,
        "ValidationException": DynamoDBValidationError,
        "AccessDeniedException": DynamoDBAccessError,
        "UnknownError": DynamoDBError,
    }

    exception_type = exception_mapping.get(error_code, DynamoDBError)

    with pytest.raises(exception_type, match=expected_exception):
        client.add_receipt_word_label(sample_receipt_word_label)
    mock_put.assert_called_once()


# -------------------------------------------------------------------
#                        addReceiptWordLabels
# -------------------------------------------------------------------


@pytest.mark.integration
def test_addReceiptWordLabels_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_word_label: ReceiptWordLabel,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    labels = [
        ReceiptWordLabel(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=10,
            word_id=5,
            label="ITEM",
            reasoning="This word appears to be an item description",
            timestamp_added=datetime.fromisoformat(
                "2024-03-20T12:00:00+00:00"
            ),
        ),
        ReceiptWordLabel(
            receipt_id=2,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=20,
            word_id=10,
            label="PRICE",
            reasoning="This word appears to be a price",
            timestamp_added=datetime.fromisoformat(
                "2024-03-20T12:00:00+00:00"
            ),
        ),
    ]

    # Act
    client.add_receipt_word_labels(labels)

    # Assert
    for label in labels:
        retrieved_label = client.get_receipt_word_label(
            label.image_id,
            label.receipt_id,
            label.line_id,
            label.word_id,
            label.label,
        )
        assert retrieved_label == label


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "receipt_word_labels cannot be None"),
        (
            "not-a-list",
            "receipt_word_labels must be a list of ReceiptWordLabel instances.",
        ),
        (
            [1, 2, 3],
            "All receipt word labels must be instances of the ReceiptWordLabel class.",
        ),
    ],
)
def test_addReceiptWordLabels_invalid_parameters(
    dynamodb_table,
    sample_receipt_word_label,
    mocker,
    invalid_input,
    expected_error,
):
    """
    Tests that addReceiptWordLabels raises ValueError for invalid parameters:
    - When receipt word labels is None
    - When receipt word labels is not a list
    - When receipt word labels contains non-ReceiptWordLabel instances
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match=expected_error):
        client.add_receipt_word_labels(invalid_input)  # type: ignore


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
        ("AccessDeniedException", "Access denied", "Access denied"),
        (
            "UnknownError",
            "Unknown error",
            "Error adding receipt word labels",
        ),
    ],
)
def test_addReceiptWordLabels_client_errors(
    dynamodb_table,
    sample_receipt_word_label,
    mocker,
    error_code,
    error_message,
    expected_exception,
):
    """
    Tests that addReceiptWordLabels handles various client errors appropriately:
    - ProvisionedThroughputExceededException when throughput exceeded
    - InternalServerError for server-side errors
    - ValidationException for invalid parameters
    - AccessDeniedException for access denied errors
    - UnknownError for unexpected errors
    """
    client = DynamoClient(dynamodb_table)
    labels = [sample_receipt_word_label]
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

    # Map error codes to expected exception types
    exception_mapping = {
        "ProvisionedThroughputExceededException": DynamoDBThroughputError,
        "InternalServerError": DynamoDBServerError,
        "ValidationException": DynamoDBValidationError,
        "AccessDeniedException": DynamoDBAccessError,
        "UnknownError": DynamoDBError,
    }

    exception_type = exception_mapping.get(error_code, DynamoDBError)

    with pytest.raises(exception_type, match=expected_exception):
        client.add_receipt_word_labels(labels)
    mock_batch_write.assert_called_once()


@pytest.mark.integration
def test_addReceiptWordLabels_unprocessed_items(
    dynamodb_table,
    sample_receipt_word_label,
    mocker,
):
    """
    Tests that addReceiptWordLabels handles unprocessed items correctly by retrying them.
    """
    client = DynamoClient(dynamodb_table)
    labels = [sample_receipt_word_label]

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
                                "Item": sample_receipt_word_label.to_item()
                            }
                        }
                    ]
                }
            },
            {},  # Empty response on second call
        ],
    )

    client.add_receipt_word_labels(labels)

    # Verify that batch_write_item was called twice
    assert mock_batch_write.call_count == 2


# -------------------------------------------------------------------
#                        updateReceiptWordLabel
# -------------------------------------------------------------------


@pytest.mark.integration
def test_updateReceiptWordLabel_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_word_label: ReceiptWordLabel,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.add_receipt_word_label(sample_receipt_word_label)

    # Update the label
    updated_label = ReceiptWordLabel(
        receipt_id=sample_receipt_word_label.receipt_id,
        image_id=sample_receipt_word_label.image_id,
        line_id=sample_receipt_word_label.line_id,
        word_id=sample_receipt_word_label.word_id,
        label=sample_receipt_word_label.label,
        reasoning="Updated reasoning",
        timestamp_added=datetime.fromisoformat("2024-03-20T12:00:00+00:00"),
    )

    # Act
    client.update_receipt_word_label(updated_label)

    # Assert
    retrieved_label = client.get_receipt_word_label(
        updated_label.image_id,
        updated_label.receipt_id,
        updated_label.line_id,
        updated_label.word_id,
        updated_label.label,
    )
    assert retrieved_label == updated_label


@pytest.mark.integration
def test_updateReceiptWordLabel_nonexistent_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_word_label: ReceiptWordLabel,
):
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act & Assert
    with pytest.raises(
        EntityNotFoundError, match="Entity does not exist: ReceiptWordLabel"
    ):
        client.update_receipt_word_label(sample_receipt_word_label)


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "ReceiptWordLabel cannot be None"),
        (
            "not-a-receipt-word-label",
            "ReceiptWordLabel must be an instance of the ReceiptWordLabel class.",
        ),
    ],
)
def test_updateReceiptWordLabel_invalid_parameters(
    dynamodb_table,
    sample_receipt_word_label,
    mocker,
    invalid_input,
    expected_error,
):
    """
    Tests that updateReceiptWordLabel raises ValueError for invalid parameters:
    - When receipt word label is None
    - When receipt word label is not an instance of ReceiptWordLabel
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match=expected_error):
        client.update_receipt_word_label(invalid_input)  # type: ignore


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
        ("AccessDeniedException", "Access denied", "Access denied"),
        (
            "UnknownError",
            "Unknown error",
            "Could not update receipt word label in DynamoDB",
        ),
    ],
)
def test_updateReceiptWordLabel_client_errors(
    dynamodb_table,
    sample_receipt_word_label,
    mocker,
    error_code,
    error_message,
    expected_exception,
):
    """
    Tests that updateReceiptWordLabel handles various client errors appropriately:
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

    # Map error codes to expected exception types
    exception_mapping = {
        "ConditionalCheckFailedException": EntityNotFoundError,  # For update operations
        "ProvisionedThroughputExceededException": DynamoDBThroughputError,
        "InternalServerError": DynamoDBServerError,
        "ValidationException": DynamoDBValidationError,
        "AccessDeniedException": DynamoDBAccessError,
        "UnknownError": DynamoDBError,
    }

    exception_type = exception_mapping.get(error_code, DynamoDBError)

    with pytest.raises(exception_type, match=expected_exception):
        client.update_receipt_word_label(sample_receipt_word_label)
    mock_put.assert_called_once()


# -------------------------------------------------------------------
#                        updateReceiptWordLabels
# -------------------------------------------------------------------


@pytest.mark.integration
def test_updateReceiptWordLabels_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_word_label: ReceiptWordLabel,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.add_receipt_word_label(sample_receipt_word_label)

    # Create a second label
    second_label = ReceiptWordLabel(
        receipt_id=2,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=20,
        word_id=10,
        label="PRICE",
        reasoning="This word appears to be a price",
        timestamp_added=datetime.fromisoformat("2024-03-20T12:00:00+00:00"),
        validation_status="VALID",
    )
    client.add_receipt_word_label(second_label)

    # Update both labels
    updated_labels = [
        ReceiptWordLabel(
            receipt_id=sample_receipt_word_label.receipt_id,
            image_id=sample_receipt_word_label.image_id,
            line_id=sample_receipt_word_label.line_id,
            word_id=sample_receipt_word_label.word_id,
            label=sample_receipt_word_label.label,
            reasoning="Updated reasoning 1",
            timestamp_added=datetime.fromisoformat(
                "2024-03-20T12:00:00+00:00"
            ),
            validation_status="VALID",
        ),
        ReceiptWordLabel(
            receipt_id=second_label.receipt_id,
            image_id=second_label.image_id,
            line_id=second_label.line_id,
            word_id=second_label.word_id,
            label=second_label.label,
            reasoning="Updated reasoning 2",
            timestamp_added=datetime.fromisoformat(
                "2024-03-20T12:00:00+00:00"
            ),
            validation_status="VALID",
        ),
    ]

    # Act
    client.update_receipt_word_labels(updated_labels)

    # Assert
    for label in updated_labels:
        retrieved_label = client.get_receipt_word_label(
            label.image_id,
            label.receipt_id,
            label.line_id,
            label.word_id,
            label.label,
        )
        assert retrieved_label == label


@pytest.mark.integration
def test_updateReceiptWordLabels_nonexistent_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_word_label: ReceiptWordLabel,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    labels = [sample_receipt_word_label]

    # Act & Assert
    with pytest.raises(
        ValueError,
        match="One or more receipt word labels do not exist",
    ):
        client.update_receipt_word_labels(labels)


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "receipt_word_labels cannot be None"),
        (
            "not-a-list",
            "receipt_word_labels must be a list of ReceiptWordLabel instances.",
        ),
        (
            [1, 2, 3],
            "receipt_word_labels must be a list of ReceiptWordLabel instances.",
        ),
    ],
)
def test_updateReceiptWordLabels_invalid_parameters(
    dynamodb_table,
    sample_receipt_word_label,
    mocker,
    invalid_input,
    expected_error,
):
    """
    Tests that updateReceiptWordLabels raises ValueError for invalid parameters:
    - When receipt word labels is None
    - When receipt word labels is not a list
    - When receipt word labels contains non-ReceiptWordLabel instances
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match=expected_error):
        client.update_receipt_word_labels(invalid_input)  # type: ignore


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_exception,exception_type",
    [
        (
            "ConditionalCheckFailedException",
            "One or more items do not exist",
            "Entity does not exist: ReceiptWordLabel",
            EntityNotFoundError,
        ),
        (
            "ProvisionedThroughputExceededException",
            "Provisioned throughput exceeded",
            "Provisioned throughput exceeded",
            Exception,  # This is handled in transact_write_items, stays as Exception
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
            "Could not update receipt word label in DynamoDB",
            DynamoDBError,
        ),
    ],
)
def test_updateReceiptWordLabels_client_errors(
    dynamodb_table,
    sample_receipt_word_label,
    mocker,
    error_code,
    error_message,
    expected_exception,
    exception_type,
):
    """
    Tests that updateReceiptWordLabels handles various client errors appropriately:
    - ConditionalCheckFailedException when items do not exist
    - ProvisionedThroughputExceededException when throughput exceeded
    - InternalServerError for server-side errors
    - ValidationException for invalid parameters
    - AccessDeniedException for access denied errors
    - UnknownError for unexpected errors
    """
    client = DynamoClient(dynamodb_table)
    labels = [sample_receipt_word_label]
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

    with pytest.raises(exception_type, match=expected_exception):
        client.update_receipt_word_labels(labels)
    mock_transact_write.assert_called_once()


@pytest.mark.integration
def test_updateReceiptWordLabels_chunking(
    dynamodb_table,
    sample_receipt_word_label,
    mocker,
):
    """
    Tests that updateReceiptWordLabels processes labels in chunks of 25 items.
    """
    client = DynamoClient(dynamodb_table)

    # Create 30 labels (should be processed in 2 chunks)
    labels = [
        ReceiptWordLabel(
            receipt_id=i,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=i * 10,
            word_id=i * 5,
            label=f"LABEL_{i}",
            reasoning=f"Reasoning {i}",
            timestamp_added=datetime.fromisoformat(
                "2024-03-20T12:00:00+00:00"
            ),
            validation_status="VALID",
        )
        for i in range(1, 31)
    ]

    # Mock the transact_write_items method
    mock_transact_write = mocker.patch.object(
        client._client,
        "transact_write_items",
        return_value={},
    )

    client.update_receipt_word_labels(labels)

    # Verify that transact_write_items was called twice (for 2 chunks)
    assert mock_transact_write.call_count == 2


# -------------------------------------------------------------------
#                        deleteReceiptWordLabel
# -------------------------------------------------------------------


@pytest.mark.integration
def test_deleteReceiptWordLabel_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_word_label: ReceiptWordLabel,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.add_receipt_word_label(sample_receipt_word_label)

    # Act
    client.delete_receipt_word_label(sample_receipt_word_label)

    # Assert
    with pytest.raises(ValueError, match="Receipt Word Label.*does not exist"):
        client.get_receipt_word_label(
            sample_receipt_word_label.image_id,
            sample_receipt_word_label.receipt_id,
            sample_receipt_word_label.line_id,
            sample_receipt_word_label.word_id,
            sample_receipt_word_label.label,
        )


@pytest.mark.integration
def test_deleteReceiptWordLabel_nonexistent_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_word_label: ReceiptWordLabel,
):
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act & Assert
    with pytest.raises(
        EntityNotFoundError, match="Entity does not exist: ReceiptWordLabel"
    ):
        client.delete_receipt_word_label(sample_receipt_word_label)


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "ReceiptWordLabel cannot be None"),
        (
            "not-a-receipt-word-label",
            "ReceiptWordLabel must be an instance of the ReceiptWordLabel class.",
        ),
    ],
)
def test_deleteReceiptWordLabel_invalid_parameters(
    dynamodb_table,
    sample_receipt_word_label,
    mocker,
    invalid_input,
    expected_error,
):
    """
    Tests that deleteReceiptWordLabel raises ValueError for invalid parameters:
    - When receipt word label is None
    - When receipt word label is not an instance of ReceiptWordLabel
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match=expected_error):
        client.delete_receipt_word_label(invalid_input)  # type: ignore


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
        ("AccessDeniedException", "Access denied", "Access denied"),
        (
            "UnknownError",
            "Unknown error",
            "Could not delete receipt word label from DynamoDB",
        ),
    ],
)
def test_deleteReceiptWordLabel_client_errors(
    dynamodb_table,
    sample_receipt_word_label,
    mocker,
    error_code,
    error_message,
    expected_exception,
):
    """
    Tests that deleteReceiptWordLabel handles various client errors appropriately:
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

    # Map error codes to expected exception types
    exception_mapping = {
        "ConditionalCheckFailedException": EntityNotFoundError,  # For delete operations
        "ProvisionedThroughputExceededException": DynamoDBThroughputError,
        "InternalServerError": DynamoDBServerError,
        "ValidationException": DynamoDBValidationError,
        "AccessDeniedException": DynamoDBAccessError,
        "UnknownError": DynamoDBError,
    }

    exception_type = exception_mapping.get(error_code, DynamoDBError)

    with pytest.raises(exception_type, match=expected_exception):
        client.delete_receipt_word_label(sample_receipt_word_label)
    mock_delete.assert_called_once()


# -------------------------------------------------------------------
#                        deleteReceiptWordLabels
# -------------------------------------------------------------------


@pytest.mark.integration
def test_deleteReceiptWordLabels_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_word_label: ReceiptWordLabel,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.add_receipt_word_label(sample_receipt_word_label)

    # Create a second label
    second_label = ReceiptWordLabel(
        receipt_id=2,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=20,
        word_id=10,
        label="PRICE",
        reasoning="This word appears to be a price",
        timestamp_added=datetime.fromisoformat("2024-03-20T12:00:00+00:00"),
        validation_status="VALID",
    )
    client.add_receipt_word_label(second_label)

    labels = [sample_receipt_word_label, second_label]

    # Act
    client.delete_receipt_word_labels(labels)

    # Assert
    for label in labels:
        with pytest.raises(
            ValueError, match="Receipt Word Label.*does not exist"
        ):
            client.get_receipt_word_label(
                label.image_id,
                label.receipt_id,
                label.line_id,
                label.word_id,
                label.label,
            )


@pytest.mark.integration
def test_deleteReceiptWordLabels_nonexistent_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_word_label: ReceiptWordLabel,
):
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act & Assert
    with pytest.raises(
        ValueError,
        match="One or more receipt word labels do not exist",
    ):
        client.delete_receipt_word_labels([sample_receipt_word_label])


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "receipt_word_labels cannot be None"),
        (
            "not-a-list",
            "receipt_word_labels must be a list of ReceiptWordLabel instances.",
        ),
        (
            [1, 2, 3],
            "receipt_word_labels must be a list of ReceiptWordLabel instances.",
        ),
    ],
)
def test_deleteReceiptWordLabels_invalid_parameters(
    dynamodb_table,
    sample_receipt_word_label,
    mocker,
    invalid_input,
    expected_error,
):
    """
    Tests that deleteReceiptWordLabels raises ValueError for invalid parameters:
    - When receipt word labels is None
    - When receipt word labels is not a list
    - When receipt word labels contains non-ReceiptWordLabel instances
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match=expected_error):
        client.delete_receipt_word_labels(invalid_input)  # type: ignore


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_exception",
    [
        (
            "ConditionalCheckFailedException",
            "One or more items do not exist",
            "Entity does not exist: ReceiptWordLabel",
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
            "Unknown error",
            "Could not delete receipt word label from DynamoDB",
        ),
    ],
)
def test_deleteReceiptWordLabels_client_errors(
    dynamodb_table,
    sample_receipt_word_label,
    mocker,
    error_code,
    error_message,
    expected_exception,
):
    """
    Tests that deleteReceiptWordLabels handles various client errors appropriately:
    - ConditionalCheckFailedException when items do not exist
    - ProvisionedThroughputExceededException when throughput exceeded
    - InternalServerError for server-side errors
    - ValidationException for invalid parameters
    - AccessDeniedException for access denied errors
    - UnknownError for unexpected errors
    """
    client = DynamoClient(dynamodb_table)
    labels = [sample_receipt_word_label]
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

    # Map error codes to expected exception types
    exception_mapping = {
        "ConditionalCheckFailedException": EntityNotFoundError,  # For batch operations
        "ProvisionedThroughputExceededException": DynamoDBThroughputError,
        "InternalServerError": DynamoDBServerError,
        "ValidationException": DynamoDBValidationError,
        "AccessDeniedException": DynamoDBAccessError,
        "UnknownError": DynamoDBError,
    }

    exception_type = exception_mapping.get(error_code, DynamoDBError)

    with pytest.raises(exception_type, match=expected_exception):
        client.delete_receipt_word_labels(labels)
    mock_transact_write.assert_called_once()


@pytest.mark.integration
def test_deleteReceiptWordLabels_chunking(
    dynamodb_table,
    sample_receipt_word_label,
    mocker,
):
    """
    Tests that deleteReceiptWordLabels processes labels in chunks of 25 items.
    """
    client = DynamoClient(dynamodb_table)

    # Create 30 labels (should be processed in 2 chunks)
    labels = [
        ReceiptWordLabel(
            receipt_id=i,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=i * 10,
            word_id=i * 5,
            label=f"LABEL_{i}",
            reasoning=f"Reasoning {i}",
            timestamp_added=datetime.fromisoformat(
                "2024-03-20T12:00:00+00:00"
            ),
            validation_status="VALID",
        )
        for i in range(1, 31)
    ]

    # Mock the transact_write_items method
    mock_transact_write = mocker.patch.object(
        client._client,
        "transact_write_items",
        return_value={},
    )

    client.delete_receipt_word_labels(labels)

    # Verify that transact_write_items was called twice (for 2 chunks)
    assert mock_transact_write.call_count == 2


# -------------------------------------------------------------------
#                        getReceiptWordLabel
# -------------------------------------------------------------------


@pytest.mark.integration
def test_getReceiptWordLabel_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_word_label: ReceiptWordLabel,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.add_receipt_word_label(sample_receipt_word_label)

    # Act
    retrieved_label = client.get_receipt_word_label(
        sample_receipt_word_label.image_id,
        sample_receipt_word_label.receipt_id,
        sample_receipt_word_label.line_id,
        sample_receipt_word_label.word_id,
        sample_receipt_word_label.label,
    )

    # Assert
    assert retrieved_label == sample_receipt_word_label


@pytest.mark.integration
def test_getReceiptWordLabel_nonexistent_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_word_label: ReceiptWordLabel,
):
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act & Assert
    with pytest.raises(ValueError, match="Receipt Word Label.*does not exist"):
        client.get_receipt_word_label(
            sample_receipt_word_label.image_id,
            sample_receipt_word_label.receipt_id,
            sample_receipt_word_label.line_id,
            sample_receipt_word_label.word_id,
            sample_receipt_word_label.label,
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_params,expected_error",
    [
        (
            (None, 1, 10, 5, "ITEM"),
            "image_id cannot be None",
        ),
        (
            ("3f52804b-2fad-4e00-92c8-b593da3a8ed3", None, 10, 5, "ITEM"),
            "receipt_id cannot be None",
        ),
        (
            ("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, None, 5, "ITEM"),
            "line_id cannot be None",
        ),
        (
            ("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, 10, None, "ITEM"),
            "word_id cannot be None",
        ),
        (
            ("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, 10, 5, None),
            "label cannot be None",
        ),
        (
            ("invalid-uuid", 1, 10, 5, "ITEM"),
            "uuid must be a valid UUIDv4",
        ),
        (
            ("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 0, 10, 5, "ITEM"),
            "Receipt ID must be a positive integer.",
        ),
        (
            ("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, 0, 5, "ITEM"),
            "Line ID must be a positive integer.",
        ),
        (
            ("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, 10, 0, "ITEM"),
            "Word ID must be a positive integer.",
        ),
        (
            ("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, 10, 5, ""),
            "Label must be a non-empty string.",
        ),
    ],
)
def test_getReceiptWordLabel_invalid_parameters(
    dynamodb_table,
    sample_receipt_word_label,
    mocker,
    invalid_params,
    expected_error,
):
    """
    Tests that getReceiptWordLabel raises ValueError for invalid parameters:
    - When any required parameter is None
    - When image_id is not a valid UUID
    - When IDs are not positive integers
    - When label is an empty string
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match=expected_error):
        client.get_receipt_word_label(*invalid_params)


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
            "Could not get receipt word label",
        ),
    ],
)
def test_getReceiptWordLabel_client_errors(
    dynamodb_table,
    sample_receipt_word_label,
    mocker,
    error_code,
    error_message,
    expected_exception,
):
    """
    Tests that getReceiptWordLabel handles various client errors appropriately:
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

    # Map error codes to expected exception types
    exception_mapping = {
        "ProvisionedThroughputExceededException": DynamoDBThroughputError,
        "ValidationException": DynamoDBValidationError,
        "InternalServerError": DynamoDBServerError,
        "AccessDeniedException": DynamoDBAccessError,
        "UnknownError": DynamoDBError,
    }

    exception_type = exception_mapping.get(error_code, DynamoDBError)

    with pytest.raises(exception_type, match=expected_exception):
        client.get_receipt_word_label(
            sample_receipt_word_label.image_id,
            sample_receipt_word_label.receipt_id,
            sample_receipt_word_label.line_id,
            sample_receipt_word_label.word_id,
            sample_receipt_word_label.label,
        )
    mock_get.assert_called_once()


# -------------------------------------------------------------------
#                        listReceiptWordLabels
# -------------------------------------------------------------------


@pytest.mark.integration
def test_listReceiptWordLabels_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_word_label: ReceiptWordLabel,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.add_receipt_word_label(sample_receipt_word_label)

    # Create a second label
    second_label = ReceiptWordLabel(
        receipt_id=2,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=20,
        word_id=10,
        label="PRICE",
        reasoning="This word appears to be a price",
        timestamp_added=datetime.fromisoformat("2024-03-20T12:00:00+00:00"),
        validation_status="VALID",
    )
    client.add_receipt_word_label(second_label)

    # Act
    labels, last_evaluated_key = client.list_receipt_word_labels()

    # Assert
    assert len(labels) == 2
    assert sample_receipt_word_label in labels
    assert second_label in labels
    assert last_evaluated_key is None


@pytest.mark.integration
def test_listReceiptWordLabels_with_limit(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_word_label: ReceiptWordLabel,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.add_receipt_word_label(sample_receipt_word_label)

    # Create a second label
    second_label = ReceiptWordLabel(
        receipt_id=2,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=20,
        word_id=10,
        label="PRICE",
        reasoning="This word appears to be a price",
        timestamp_added=datetime.fromisoformat("2024-03-20T12:00:00+00:00"),
        validation_status="VALID",
    )
    client.add_receipt_word_label(second_label)

    # Act
    labels, last_evaluated_key = client.list_receipt_word_labels(limit=1)

    # Assert
    assert len(labels) == 1
    assert last_evaluated_key is not None


@pytest.mark.integration
def test_listReceiptWordLabels_with_last_evaluated_key(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_word_label: ReceiptWordLabel,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.add_receipt_word_label(sample_receipt_word_label)

    # Create a second label
    second_label = ReceiptWordLabel(
        receipt_id=2,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=20,
        word_id=10,
        label="PRICE",
        reasoning="This word appears to be a price",
        timestamp_added=datetime.fromisoformat("2024-03-20T12:00:00+00:00"),
        validation_status="VALID",
    )
    client.add_receipt_word_label(second_label)

    # Get first page
    first_page, last_evaluated_key = client.list_receipt_word_labels(limit=1)
    assert len(first_page) == 1
    assert last_evaluated_key is not None

    # Get second page
    second_page, last_evaluated_key = client.list_receipt_word_labels(
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
            "limit must be an integer",
        ),
        (
            {"limit": 0},
            "limit must be greater than 0",
        ),
        (
            {"limit": -1},
            "limit must be greater than 0",
        ),
        (
            {"last_evaluated_key": "not-a-dict"},
            "last_evaluated_key must be a dictionary",
        ),
        (
            {"last_evaluated_key": {}},
            "last_evaluated_key must contain keys: \\{'SK', 'PK'\\}|last_evaluated_key must contain keys: \\{'PK', 'SK'\\}",
        ),
        (
            {"last_evaluated_key": {"PK": "not-a-dict", "SK": {"S": "value"}}},
            "last_evaluated_key\\[PK\\] must be a dict containing a key 'S'",
        ),
    ],
)
def test_listReceiptWordLabels_invalid_parameters(
    dynamodb_table,
    sample_receipt_word_label,
    mocker,
    invalid_input,
    expected_error,
):
    """
    Tests that listReceiptWordLabels raises ValueError for invalid parameters:
    - When limit is not an integer
    - When limit is less than or equal to 0
    - When last_evaluated_key is not a dictionary
    - When last_evaluated_key is missing required keys
    - When last_evaluated_key values are not properly formatted
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match=expected_error):
        client.list_receipt_word_labels(**invalid_input)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_exception",
    [
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
            "Could not list receipt word label from DynamoDB",
        ),
    ],
)
def test_listReceiptWordLabels_client_errors(
    dynamodb_table,
    sample_receipt_word_label,
    mocker,
    error_code,
    error_message,
    expected_exception,
):
    """
    Tests that listReceiptWordLabels handles various client errors appropriately:
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

    # Map error codes to expected exception types
    exception_mapping = {
        "ResourceNotFoundException": DynamoDBError,
        "ProvisionedThroughputExceededException": DynamoDBThroughputError,
        "ValidationException": DynamoDBValidationError,
        "InternalServerError": DynamoDBServerError,
        "AccessDeniedException": DynamoDBAccessError,
        "UnknownError": DynamoDBError,
    }

    exception_type = exception_mapping.get(error_code, DynamoDBError)

    with pytest.raises(exception_type, match=expected_exception):
        client.list_receipt_word_labels()
    mock_query.assert_called_once()


@pytest.mark.integration
def test_listReceiptWordLabels_pagination_errors(
    dynamodb_table,
    sample_receipt_word_label,
    mocker,
):
    """
    Tests that listReceiptWordLabels handles pagination errors appropriately:
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
        Exception,
        match="Table not found",
    ):
        client.list_receipt_word_labels()
    mock_query.assert_called_once()

    # Test subsequent query failure
    mock_query.reset_mock()
    mock_query.side_effect = [
        {
            "Items": [sample_receipt_word_label.to_item()],
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
        Exception, match="Could not list receipt word label from DynamoDB"
    ):
        client.list_receipt_word_labels()
    assert mock_query.call_count == 2


# -------------------------------------------------------------------
#                        getReceiptWordLabelsByLabel
# -------------------------------------------------------------------


@pytest.mark.integration
def test_getReceiptWordLabelsByLabel_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_word_label: ReceiptWordLabel,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.add_receipt_word_label(sample_receipt_word_label)

    # Create a second label with the same label type
    second_label = ReceiptWordLabel(
        receipt_id=2,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=20,
        word_id=10,
        label="ITEM",  # Same label as sample_receipt_word_label
        reasoning="This word appears to be an item description",
        timestamp_added=datetime.fromisoformat("2024-03-20T12:00:00+00:00"),
        validation_status="VALID",
    )
    client.add_receipt_word_label(second_label)

    # Act
    labels, last_evaluated_key = client.get_receipt_word_labels_by_label(
        "ITEM"
    )

    # Assert
    assert len(labels) == 2
    assert sample_receipt_word_label in labels
    assert second_label in labels
    assert last_evaluated_key is None


@pytest.mark.integration
def test_getReceiptWordLabelsByLabel_with_limit(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_word_label: ReceiptWordLabel,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.add_receipt_word_label(sample_receipt_word_label)

    # Create a second label with the same label type
    second_label = ReceiptWordLabel(
        receipt_id=2,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=20,
        word_id=10,
        label="ITEM",  # Same label as sample_receipt_word_label
        reasoning="This word appears to be an item description",
        timestamp_added=datetime.fromisoformat("2024-03-20T12:00:00+00:00"),
        validation_status="VALID",
    )
    client.add_receipt_word_label(second_label)

    # Act
    labels, last_evaluated_key = client.get_receipt_word_labels_by_label(
        "ITEM", limit=1
    )

    # Assert
    assert len(labels) == 1
    assert last_evaluated_key is not None


@pytest.mark.integration
def test_getReceiptWordLabelsByLabel_with_last_evaluated_key(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_word_label: ReceiptWordLabel,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.add_receipt_word_label(sample_receipt_word_label)

    # Create a second label with the same label type
    second_label = ReceiptWordLabel(
        receipt_id=2,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=20,
        word_id=10,
        label="ITEM",  # Same label as sample_receipt_word_label
        reasoning="This word appears to be an item description",
        timestamp_added=datetime.fromisoformat("2024-03-20T12:00:00+00:00"),
        validation_status="VALID",
    )
    client.add_receipt_word_label(second_label)

    # Get first page
    first_page, last_evaluated_key = client.get_receipt_word_labels_by_label(
        "ITEM", limit=1
    )
    assert len(first_page) == 1
    assert last_evaluated_key is not None

    # Get second page
    second_page, last_evaluated_key = client.get_receipt_word_labels_by_label(
        "ITEM", limit=1, last_evaluated_key=last_evaluated_key
    )
    assert len(second_page) == 1
    assert last_evaluated_key is None


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (
            {"label": None},
            "label must be a non-empty string",
        ),
        (
            {"label": ""},
            "label must be a non-empty string",
        ),
        (
            {"label": "ITEM", "limit": "not-an-int"},
            "limit must be an integer",
        ),
        (
            {"label": "ITEM", "limit": 0},
            "limit must be greater than 0",
        ),
        (
            {"label": "ITEM", "limit": -1},
            "limit must be greater than 0",
        ),
        (
            {"label": "ITEM", "last_evaluated_key": "not-a-dict"},
            "last_evaluated_key must be a dictionary",
        ),
        (
            {"label": "ITEM", "last_evaluated_key": {}},
            "last_evaluated_key must contain keys: \\{'SK', 'PK'\\}|last_evaluated_key must contain keys: \\{'PK', 'SK'\\}",
        ),
        (
            {
                "label": "ITEM",
                "last_evaluated_key": {
                    "PK": "not-a-dict",
                    "SK": {"S": "value"},
                },
            },
            "last_evaluated_key\\[PK\\] must be a dict containing a key 'S'",
        ),
    ],
)
def test_getReceiptWordLabelsByLabel_invalid_parameters(
    dynamodb_table,
    sample_receipt_word_label,
    mocker,
    invalid_input,
    expected_error,
):
    """
    Tests that getReceiptWordLabelsByLabel raises ValueError for invalid parameters:
    - When label is None or empty string
    - When limit is not an integer
    - When limit is less than or equal to 0
    - When last_evaluated_key is not a dictionary
    - When last_evaluated_key is missing required keys
    - When last_evaluated_key values are not properly formatted
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match=expected_error):
        client.get_receipt_word_labels_by_label(**invalid_input)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_exception",
    [
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
            "Could not get receipt word label",
        ),
    ],
)
def test_getReceiptWordLabelsByLabel_client_errors(
    dynamodb_table,
    sample_receipt_word_label,
    mocker,
    error_code,
    error_message,
    expected_exception,
):
    """
    Tests that getReceiptWordLabelsByLabel handles various client errors appropriately:
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

    # Map error codes to expected exception types
    exception_mapping = {
        "ResourceNotFoundException": DynamoDBError,
        "ProvisionedThroughputExceededException": DynamoDBThroughputError,
        "ValidationException": DynamoDBValidationError,
        "InternalServerError": DynamoDBServerError,
        "AccessDeniedException": DynamoDBAccessError,
        "UnknownError": DynamoDBError,
    }

    exception_type = exception_mapping.get(error_code, DynamoDBError)

    with pytest.raises(exception_type, match=expected_exception):
        client.get_receipt_word_labels_by_label("ITEM")
    mock_query.assert_called_once()


@pytest.mark.integration
def test_getReceiptWordLabelsByLabel_pagination_errors(
    dynamodb_table,
    sample_receipt_word_label,
    mocker,
):
    """
    Tests that getReceiptWordLabelsByLabel handles pagination errors appropriately:
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
        Exception,
        match="Table not found",
    ):
        client.get_receipt_word_labels_by_label("ITEM")
    mock_query.assert_called_once()

    # Test subsequent query failure
    mock_query.reset_mock()
    mock_query.side_effect = [
        {
            "Items": [sample_receipt_word_label.to_item()],
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

    with pytest.raises(Exception, match="Could not get receipt word label"):
        client.get_receipt_word_labels_by_label("ITEM")
    assert mock_query.call_count == 2


# -------------------------------------------------------------------
#                        getReceiptWordLabelsByValidationStatus
# -------------------------------------------------------------------


@pytest.mark.integration
def test_getReceiptWordLabelsByValidationStatus_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_word_label: ReceiptWordLabel,
    mocker,
):
    # Arrange
    client = DynamoClient(dynamodb_table)

    # No need to mock ValidationStatus.values anymore

    client.add_receipt_word_label(sample_receipt_word_label)

    # Act
    labels, last_evaluated_key = (
        client.get_receipt_word_labels_by_validation_status("VALID")
    )

    # Assert
    assert len(labels) == 1
    assert labels[0] == sample_receipt_word_label
    assert last_evaluated_key is None


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (
            {"validation_status": None},
            "validation status must be a non-empty string",
        ),
        (
            {"validation_status": ""},
            "validation status must be a non-empty string",
        ),
        (
            {"validation_status": "VALIDATED"},
            "validation status must be one of the following: "
            + ", ".join([status.value for status in ValidationStatus]),
        ),
        (
            {"validation_status": "VALID", "limit": "not-an-int"},
            "limit must be an integer",
        ),
        (
            {"validation_status": "VALID", "limit": 0},
            "limit must be greater than 0",
        ),
        (
            {"validation_status": "VALID", "limit": -1},
            "limit must be greater than 0",
        ),
        (
            {
                "validation_status": "VALID",
                "last_evaluated_key": "not-a-dict",
            },
            "last_evaluated_key must be a dictionary",
        ),
        (
            {"validation_status": "VALID", "last_evaluated_key": {}},
            "last_evaluated_key must contain keys: \\{'SK', 'PK'\\}|last_evaluated_key must contain keys: \\{'PK', 'SK'\\}",
        ),
        (
            {
                "validation_status": "VALID",
                "last_evaluated_key": {
                    "PK": "not-a-dict",
                    "SK": {"S": "value"},
                },
            },
            "last_evaluated_key\\[PK\\] must be a dict containing a key 'S'",
        ),
    ],
)
def test_getReceiptWordLabelsByValidationStatus_invalid_parameters(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_word_label: ReceiptWordLabel,
    mocker,
    invalid_input: dict,
    expected_error: str,
):
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act & Assert
    with pytest.raises(ValueError, match=expected_error):
        client.get_receipt_word_labels_by_validation_status(**invalid_input)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code, error_message, expected_exception",
    [
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
            "ValidationException",
            "One or more parameters given were invalid",
            "One or more parameters given were invalid",
        ),
        (
            "InternalServerError",
            "An error occurred on the server",
            "An error occurred on the server",
        ),
    ],
)
def test_getReceiptWordLabelsByValidationStatus_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_word_label: ReceiptWordLabel,
    mocker,
    error_code,
    error_message,
    expected_exception,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    mock_query = mocker.patch.object(
        client._client,
        "query",
        side_effect=ClientError(
            {"Error": {"Code": error_code, "Message": error_message}}, "Query"
        ),
    )

    # Map error codes to expected exception types
    exception_mapping = {
        "ResourceNotFoundException": DynamoDBError,
        "ProvisionedThroughputExceededException": DynamoDBThroughputError,
        "ValidationException": DynamoDBValidationError,
        "InternalServerError": DynamoDBServerError,
        "AccessDeniedException": DynamoDBAccessError,
    }

    exception_type = exception_mapping.get(error_code, DynamoDBError)

    with pytest.raises(exception_type, match=expected_exception):
        client.get_receipt_word_labels_by_validation_status("VALID")
    mock_query.assert_called_once()


@pytest.mark.integration
def test_getReceiptWordLabelsByValidationStatus_pagination_midway_failure(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_word_label: ReceiptWordLabel,
    mocker,
):
    client = DynamoClient(dynamodb_table)

    mock_query = mocker.patch.object(client._client, "query")
    mock_query.side_effect = [
        {
            "Items": [sample_receipt_word_label.to_item()],
            "LastEvaluatedKey": {"PK": {"S": "key"}, "SK": {"S": "key"}},
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
        Exception,
        match="Could not get receipt word label",
    ):
        client.get_receipt_word_labels_by_validation_status("VALID")
    assert mock_query.call_count == 2


def test_getReceiptWordLabelsByValidationStatus_multi_page_success(
    dynamodb_table,
    sample_receipt_word_label,
    mocker,
):
    client = DynamoClient(dynamodb_table)

    mock_query = mocker.patch.object(client._client, "query")
    mock_query.side_effect = [
        {
            "Items": [sample_receipt_word_label.to_item()],
            "LastEvaluatedKey": {"PK": {"S": "foo"}, "SK": {"S": "bar"}},
        },
        {
            "Items": [],
        },
    ]

    labels, lek = client.get_receipt_word_labels_by_validation_status("VALID")
    assert len(labels) == 1
    assert lek is None


@pytest.mark.integration
def test_getReceiptWordLabelsByValidationStatus_hits_limit_mid_loop(
    dynamodb_table,
    sample_receipt_word_label,
    mocker,
):
    client = DynamoClient(dynamodb_table)
    mock_query = mocker.patch.object(client._client, "query")

    mock_query.side_effect = [
        {
            "Items": [sample_receipt_word_label.to_item()],  # total = 1
            "LastEvaluatedKey": {"PK": {"S": "k1"}, "SK": {"S": "k1"}},
        },
        {
            "Items": [sample_receipt_word_label.to_item()],  # total = 2
            "LastEvaluatedKey": {"PK": {"S": "k2"}, "SK": {"S": "k2"}},
        },
        {
            "Items": [
                sample_receipt_word_label.to_item()
            ],  # total = 3 (hits limit)
        },
    ]

    labels, lek = client.get_receipt_word_labels_by_validation_status(
        "VALID", limit=3
    )

    assert len(labels) == 1  # With limit, only returns first page
    assert lek == {"PK": {"S": "k1"}, "SK": {"S": "k1"}}  # Has more pages
    assert mock_query.call_count == 1  # Only one query when limit is provided


def test_getReceiptWordLabelsByValidationStatus_limit_updates_mid_loop(
    dynamodb_table,
    sample_receipt_word_label,
    mocker,
):
    client = DynamoClient(dynamodb_table)
    mock_query = mocker.patch.object(client._client, "query")

    mock_query.side_effect = [
        {
            "Items": [sample_receipt_word_label.to_item()],
            "LastEvaluatedKey": {"PK": {"S": "k1"}, "SK": {"S": "k1"}},
        },
        {
            "Items": [
                sample_receipt_word_label.to_item(),
                sample_receipt_word_label.to_item(),
            ],
        },
    ]

    labels, lek = client.get_receipt_word_labels_by_validation_status(
        "VALID", limit=2
    )

    assert len(labels) == 1  # With limit, only returns first page
    assert lek == {"PK": {"S": "k1"}, "SK": {"S": "k1"}}  # Has more pages
    assert mock_query.call_count == 1  # Only one query when limit is provided


@pytest.mark.integration
def test_getReceiptWordLabelsByValidationStatus_triggers_limit_mid_loop(
    dynamodb_table,
    sample_receipt_word_label,
    mocker,
):
    client = DynamoClient(dynamodb_table)
    mock_query = mocker.patch.object(client._client, "query")

    # Mock 3 query pages
    mock_query.side_effect = [
        {
            "Items": [sample_receipt_word_label.to_item()],
            "LastEvaluatedKey": {"PK": {"S": "k1"}, "SK": {"S": "k1"}},
        },
        {
            "Items": [sample_receipt_word_label.to_item()],
            "LastEvaluatedKey": {"PK": {"S": "k2"}, "SK": {"S": "k2"}},
        },
        {
            "Items": [sample_receipt_word_label.to_item()],
        },
    ]

    labels, lek = client.get_receipt_word_labels_by_validation_status(
        "VALID", limit=3
    )

    assert len(labels) == 1  # With limit, only returns first page
    assert lek == {"PK": {"S": "k1"}, "SK": {"S": "k1"}}  # Has more pages
    assert mock_query.call_count == 1  # Only one query when limit is provided
