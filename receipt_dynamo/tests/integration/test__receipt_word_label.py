from typing import Any, Dict, List, Literal, Optional, Type

import pytest
from botocore.exceptions import ClientError, ParamValidationError
from receipt_dynamo import DynamoClient, ReceiptWordLabel
from receipt_dynamo.constants import ValidationStatus

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
        timestamp_added="2024-03-20T12:00:00Z",
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
    client.addReceiptWordLabel(sample_receipt_word_label)

    # Assert
    retrieved_label = client.getReceiptWordLabel(
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
    client.addReceiptWordLabel(sample_receipt_word_label)

    # Act & Assert
    with pytest.raises(ValueError, match="already exists"):
        client.addReceiptWordLabel(sample_receipt_word_label)


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "ReceiptWordLabel parameter is required and cannot be None."),
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
        client.addReceiptWordLabel(invalid_input)  # type: ignore


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
            "Could not add receipt word label to DynamoDB",
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
            "One or more parameters were invalid",
            "One or more parameters were invalid",
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

    with pytest.raises(Exception, match=expected_exception):
        client.addReceiptWordLabel(sample_receipt_word_label)
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
            timestamp_added="2024-03-20T12:00:00Z",
        ),
        ReceiptWordLabel(
            receipt_id=2,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=20,
            word_id=10,
            label="PRICE",
            reasoning="This word appears to be a price",
            timestamp_added="2024-03-20T12:00:00Z",
        ),
    ]

    # Act
    client.addReceiptWordLabels(labels)

    # Assert
    for label in labels:
        retrieved_label = client.getReceiptWordLabel(
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
        (None, "ReceiptWordLabels parameter is required and cannot be None."),
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
        client.addReceiptWordLabels(invalid_input)  # type: ignore


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

    with pytest.raises(Exception, match=expected_exception):
        client.addReceiptWordLabels(labels)
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
                        {"PutRequest": {"Item": sample_receipt_word_label.to_item()}}
                    ]
                }
            },
            {},  # Empty response on second call
        ],
    )

    client.addReceiptWordLabels(labels)

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
    client.addReceiptWordLabel(sample_receipt_word_label)

    # Update the label
    updated_label = ReceiptWordLabel(
        receipt_id=sample_receipt_word_label.receipt_id,
        image_id=sample_receipt_word_label.image_id,
        line_id=sample_receipt_word_label.line_id,
        word_id=sample_receipt_word_label.word_id,
        label=sample_receipt_word_label.label,
        reasoning="Updated reasoning",
        timestamp_added=sample_receipt_word_label.timestamp_added,
    )

    # Act
    client.updateReceiptWordLabel(updated_label)

    # Assert
    retrieved_label = client.getReceiptWordLabel(
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
    with pytest.raises(ValueError, match="does not exist"):
        client.updateReceiptWordLabel(sample_receipt_word_label)


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "ReceiptWordLabel parameter is required and cannot be None."),
        (
            "not-a-receipt-word-label",
            "receipt_word_label must be an instance of the ReceiptWordLabel class.",
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
        client.updateReceiptWordLabel(invalid_input)  # type: ignore


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
            "Error updating receipt word label",
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

    with pytest.raises(Exception, match=expected_exception):
        client.updateReceiptWordLabel(sample_receipt_word_label)
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
    client.addReceiptWordLabel(sample_receipt_word_label)

    # Create a second label
    second_label = ReceiptWordLabel(
        receipt_id=2,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=20,
        word_id=10,
        label="PRICE",
        reasoning="This word appears to be a price",
        timestamp_added="2024-03-20T12:00:00Z",
        validation_status="VALID",
    )
    client.addReceiptWordLabel(second_label)

    # Update both labels
    updated_labels = [
        ReceiptWordLabel(
            receipt_id=sample_receipt_word_label.receipt_id,
            image_id=sample_receipt_word_label.image_id,
            line_id=sample_receipt_word_label.line_id,
            word_id=sample_receipt_word_label.word_id,
            label=sample_receipt_word_label.label,
            reasoning="Updated reasoning 1",
            timestamp_added=sample_receipt_word_label.timestamp_added,
            validation_status="VALID",
        ),
        ReceiptWordLabel(
            receipt_id=second_label.receipt_id,
            image_id=second_label.image_id,
            line_id=second_label.line_id,
            word_id=second_label.word_id,
            label=second_label.label,
            reasoning="Updated reasoning 2",
            timestamp_added=second_label.timestamp_added,
            validation_status="VALID",
        ),
    ]

    # Act
    client.updateReceiptWordLabels(updated_labels)

    # Assert
    for label in updated_labels:
        retrieved_label = client.getReceiptWordLabel(
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
        match="Error updating receipt word labels: An error occurred \(TransactionCanceledException\) when calling the TransactWriteItems operation: Transaction cancelled, please refer cancellation reasons for specific reasons \[ConditionalCheckFailed\]",
    ):
        client.updateReceiptWordLabels(labels)


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "ReceiptWordLabels parameter is required and cannot be None."),
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
        client.updateReceiptWordLabels(invalid_input)  # type: ignore


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_exception",
    [
        (
            "ConditionalCheckFailedException",
            "One or more items do not exist",
            "One or more receipt word labels do not exist",
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
            "Error updating receipt word labels",
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

    with pytest.raises(Exception, match=expected_exception):
        client.updateReceiptWordLabels(labels)
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
            timestamp_added="2024-03-20T12:00:00Z",
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

    client.updateReceiptWordLabels(labels)

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
    client.addReceiptWordLabel(sample_receipt_word_label)

    # Act
    client.deleteReceiptWordLabel(sample_receipt_word_label)

    # Assert
    with pytest.raises(ValueError, match="does not exist"):
        client.getReceiptWordLabel(
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
    with pytest.raises(ValueError, match="does not exist"):
        client.deleteReceiptWordLabel(sample_receipt_word_label)


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "ReceiptWordLabel parameter is required and cannot be None."),
        (
            "not-a-receipt-word-label",
            "receipt_word_label must be an instance of the ReceiptWordLabel class.",
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
        client.deleteReceiptWordLabel(invalid_input)  # type: ignore


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
            "Error deleting receipt word label",
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

    with pytest.raises(Exception, match=expected_exception):
        client.deleteReceiptWordLabel(sample_receipt_word_label)
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
    client.addReceiptWordLabel(sample_receipt_word_label)

    # Create a second label
    second_label = ReceiptWordLabel(
        receipt_id=2,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=20,
        word_id=10,
        label="PRICE",
        reasoning="This word appears to be a price",
        timestamp_added="2024-03-20T12:00:00Z",
        validation_status="VALID",
    )
    client.addReceiptWordLabel(second_label)

    labels = [sample_receipt_word_label, second_label]

    # Act
    client.deleteReceiptWordLabels(labels)

    # Assert
    for label in labels:
        with pytest.raises(ValueError, match="does not exist"):
            client.getReceiptWordLabel(
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
        Exception,
        match="Error deleting receipt word labels: An error occurred \(TransactionCanceledException\) when calling the TransactWriteItems operation: Transaction cancelled, please refer cancellation reasons for specific reasons \[ConditionalCheckFailed\]",
    ):
        client.deleteReceiptWordLabels([sample_receipt_word_label])


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "ReceiptWordLabels parameter is required and cannot be None."),
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
        client.deleteReceiptWordLabels(invalid_input)  # type: ignore


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_exception",
    [
        (
            "ConditionalCheckFailedException",
            "One or more items do not exist",
            "One or more receipt word labels do not exist",
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
            "Error deleting receipt word labels",
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

    with pytest.raises(Exception, match=expected_exception):
        client.deleteReceiptWordLabels(labels)
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
            timestamp_added="2024-03-20T12:00:00Z",
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

    client.deleteReceiptWordLabels(labels)

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
    client.addReceiptWordLabel(sample_receipt_word_label)

    # Act
    retrieved_label = client.getReceiptWordLabel(
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
    with pytest.raises(ValueError, match="does not exist"):
        client.getReceiptWordLabel(
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
            "Image ID is required and cannot be None.",
        ),
        (
            ("3f52804b-2fad-4e00-92c8-b593da3a8ed3", None, 10, 5, "ITEM"),
            "Receipt ID is required and cannot be None.",
        ),
        (
            ("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, None, 5, "ITEM"),
            "Line ID is required and cannot be None.",
        ),
        (
            ("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, 10, None, "ITEM"),
            "Word ID is required and cannot be None.",
        ),
        (
            ("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, 10, 5, None),
            "Label is required and cannot be None.",
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
        client.getReceiptWordLabel(*invalid_params)


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
            "Error getting receipt word label",
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

    with pytest.raises(Exception, match=expected_exception):
        client.getReceiptWordLabel(
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
    client.addReceiptWordLabel(sample_receipt_word_label)

    # Create a second label
    second_label = ReceiptWordLabel(
        receipt_id=2,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=20,
        word_id=10,
        label="PRICE",
        reasoning="This word appears to be a price",
        timestamp_added="2024-03-20T12:00:00Z",
        validation_status="VALID",
    )
    client.addReceiptWordLabel(second_label)

    # Act
    labels, last_evaluated_key = client.listReceiptWordLabels()

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
    client.addReceiptWordLabel(sample_receipt_word_label)

    # Create a second label
    second_label = ReceiptWordLabel(
        receipt_id=2,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=20,
        word_id=10,
        label="PRICE",
        reasoning="This word appears to be a price",
        timestamp_added="2024-03-20T12:00:00Z",
        validation_status="VALID",
    )
    client.addReceiptWordLabel(second_label)

    # Act
    labels, last_evaluated_key = client.listReceiptWordLabels(limit=1)

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
    client.addReceiptWordLabel(sample_receipt_word_label)

    # Create a second label
    second_label = ReceiptWordLabel(
        receipt_id=2,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=20,
        word_id=10,
        label="PRICE",
        reasoning="This word appears to be a price",
        timestamp_added="2024-03-20T12:00:00Z",
        validation_status="VALID",
    )
    client.addReceiptWordLabel(second_label)

    # Get first page
    first_page, last_evaluated_key = client.listReceiptWordLabels(limit=1)
    assert len(first_page) == 1
    assert last_evaluated_key is not None

    # Get second page
    second_page, last_evaluated_key = client.listReceiptWordLabels(
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
    - When lastEvaluatedKey is not a dictionary
    - When lastEvaluatedKey is missing required keys
    - When lastEvaluatedKey values are not properly formatted
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match=expected_error):
        client.listReceiptWordLabels(**invalid_input)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_exception",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Could not list receipt word labels from the database",
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
            "Could not list receipt word labels from the database",
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

    with pytest.raises(Exception, match=expected_exception):
        client.listReceiptWordLabels()
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
        Exception, match="Could not list receipt word labels from the database"
    ):
        client.listReceiptWordLabels()
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
        Exception, match="Could not list receipt word labels from the database"
    ):
        client.listReceiptWordLabels()
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
    client.addReceiptWordLabel(sample_receipt_word_label)

    # Create a second label with the same label type
    second_label = ReceiptWordLabel(
        receipt_id=2,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=20,
        word_id=10,
        label="ITEM",  # Same label as sample_receipt_word_label
        reasoning="This word appears to be an item description",
        timestamp_added="2024-03-20T12:00:00Z",
        validation_status="VALID",
    )
    client.addReceiptWordLabel(second_label)

    # Act
    labels, last_evaluated_key = client.getReceiptWordLabelsByLabel("ITEM")

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
    client.addReceiptWordLabel(sample_receipt_word_label)

    # Create a second label with the same label type
    second_label = ReceiptWordLabel(
        receipt_id=2,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=20,
        word_id=10,
        label="ITEM",  # Same label as sample_receipt_word_label
        reasoning="This word appears to be an item description",
        timestamp_added="2024-03-20T12:00:00Z",
        validation_status="VALID",
    )
    client.addReceiptWordLabel(second_label)

    # Act
    labels, last_evaluated_key = client.getReceiptWordLabelsByLabel("ITEM", limit=1)

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
    client.addReceiptWordLabel(sample_receipt_word_label)

    # Create a second label with the same label type
    second_label = ReceiptWordLabel(
        receipt_id=2,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=20,
        word_id=10,
        label="ITEM",  # Same label as sample_receipt_word_label
        reasoning="This word appears to be an item description",
        timestamp_added="2024-03-20T12:00:00Z",
        validation_status="VALID",
    )
    client.addReceiptWordLabel(second_label)

    # Get first page
    first_page, last_evaluated_key = client.getReceiptWordLabelsByLabel("ITEM", limit=1)
    assert len(first_page) == 1
    assert last_evaluated_key is not None

    # Get second page
    second_page, last_evaluated_key = client.getReceiptWordLabelsByLabel(
        "ITEM", limit=1, lastEvaluatedKey=last_evaluated_key
    )
    assert len(second_page) == 1
    assert last_evaluated_key is None


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (
            {"label": None},
            "Label must be a non-empty string",
        ),
        (
            {"label": ""},
            "Label must be a non-empty string",
        ),
        (
            {"label": "ITEM", "limit": "not-an-int"},
            "Limit must be an integer",
        ),
        (
            {"label": "ITEM", "limit": 0},
            "Limit must be greater than 0",
        ),
        (
            {"label": "ITEM", "limit": -1},
            "Limit must be greater than 0",
        ),
        (
            {"label": "ITEM", "lastEvaluatedKey": "not-a-dict"},
            "LastEvaluatedKey must be a dictionary",
        ),
        (
            {"label": "ITEM", "lastEvaluatedKey": {}},
            "LastEvaluatedKey must contain keys: \\{['PK', 'SK']|['SK', 'PK']\\}",
        ),
        (
            {
                "label": "ITEM",
                "lastEvaluatedKey": {"PK": "not-a-dict", "SK": {"S": "value"}},
            },
            "LastEvaluatedKey\\[PK\\] must be a dict containing a key 'S'",
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
    - When lastEvaluatedKey is not a dictionary
    - When lastEvaluatedKey is missing required keys
    - When lastEvaluatedKey values are not properly formatted
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match=expected_error):
        client.getReceiptWordLabelsByLabel(**invalid_input)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_exception",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Could not list receipt word labels by label type",
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
            "Could not list receipt word labels by label type",
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

    with pytest.raises(Exception, match=expected_exception):
        client.getReceiptWordLabelsByLabel("ITEM")
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
        Exception, match="Could not list receipt word labels by label type"
    ):
        client.getReceiptWordLabelsByLabel("ITEM")
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
        Exception, match="Could not list receipt word labels by label type"
    ):
        client.getReceiptWordLabelsByLabel("ITEM")
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

    client.addReceiptWordLabel(sample_receipt_word_label)

    # Act
    labels, last_evaluated_key = client.getReceiptWordLabelsByValidationStatus("VALID")

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
            "Validation status must be a non-empty string",
        ),
        (
            {"validation_status": ""},
            "Validation status must be a non-empty string",
        ),
        (
            {"validation_status": "VALIDATED"},
            "Validation status must be one of the following: "
            + ", ".join([status.value for status in ValidationStatus]),
        ),
        (
            {"validation_status": "VALID", "limit": "not-an-int"},
            "Limit must be an integer",
        ),
        (
            {"validation_status": "VALID", "limit": 0},
            "Limit must be greater than 0",
        ),
        (
            {"validation_status": "VALID", "limit": -1},
            "Limit must be greater than 0",
        ),
        (
            {
                "validation_status": "VALID",
                "lastEvaluatedKey": "not-a-dict",
            },
            "LastEvaluatedKey must be a dictionary",
        ),
        (
            {"validation_status": "VALID", "lastEvaluatedKey": {}},
            "LastEvaluatedKey must contain keys: \\{['PK', 'SK']|['SK', 'PK']\\}",
        ),
        (
            {
                "validation_status": "VALID",
                "lastEvaluatedKey": {"PK": "not-a-dict", "SK": {"S": "value"}},
            },
            "LastEvaluatedKey\\[PK\\] must be a dict containing a key 'S'",
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
        client.getReceiptWordLabelsByValidationStatus(**invalid_input)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code, error_message, expected_exception",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Could not list receipt word labels by validation status",
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

    with pytest.raises(Exception, match=expected_exception):
        client.getReceiptWordLabelsByValidationStatus("VALID")
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
        match="Could not list receipt word labels by validation status",
    ):
        client.getReceiptWordLabelsByValidationStatus("VALID")
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

    labels, lek = client.getReceiptWordLabelsByValidationStatus("VALID")
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
            "Items": [sample_receipt_word_label.to_item()],  # total = 3 (hits limit)
        },
    ]

    labels, lek = client.getReceiptWordLabelsByValidationStatus("VALID", limit=3)

    assert len(labels) == 3
    assert lek is None
    assert mock_query.call_count == 3  # ensures we looped and reassigned limit


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

    labels, lek = client.getReceiptWordLabelsByValidationStatus("VALID", limit=2)

    assert len(labels) == 2
    assert lek is None
    assert mock_query.call_count == 2


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

    labels, lek = client.getReceiptWordLabelsByValidationStatus("VALID", limit=3)

    assert len(labels) == 3
    assert lek is None  # loop completed
    assert mock_query.call_count == 3
