from typing import Literal

import pytest
from botocore.exceptions import ClientError
from receipt_dynamo import DynamoClient, ReceiptLetter

# -------------------------------------------------------------------
#                        FIXTURES
# -------------------------------------------------------------------


@pytest.fixture
def sample_receipt_letter():
    return ReceiptLetter(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=10,
        word_id=5,
        letter_id=2,
        text="A",
        bounding_box={"x": 0.15, "y": 0.20, "width": 0.02, "height": 0.02},
        top_left={"x": 0.15, "y": 0.20},
        top_right={"x": 0.17, "y": 0.20},
        bottom_left={"x": 0.15, "y": 0.22},
        bottom_right={"x": 0.17, "y": 0.22},
        angle_degrees=1.5,
        angle_radians=0.0261799,
        confidence=0.97,
    )


# -------------------------------------------------------------------
#                        addReceiptLetter
# -------------------------------------------------------------------


@pytest.mark.integration
def test_addReceiptLetter_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_letter: ReceiptLetter,
):
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act
    client.addReceiptLetter(sample_receipt_letter)

    # Assert
    retrieved_letter = client.getReceiptLetter(
        sample_receipt_letter.receipt_id,
        sample_receipt_letter.image_id,
        sample_receipt_letter.line_id,
        sample_receipt_letter.word_id,
        sample_receipt_letter.letter_id,
    )
    assert retrieved_letter == sample_receipt_letter


@pytest.mark.integration
def test_addReceiptLetter_duplicate_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_letter: ReceiptLetter,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.addReceiptLetter(sample_receipt_letter)

    # Act & Assert
    with pytest.raises(ValueError, match="already exists"):
        client.addReceiptLetter(sample_receipt_letter)


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "letter parameter is required and cannot be None."),
        (
            "not-a-receipt-letter",
            "letter must be an instance of the ReceiptLetter class.",
        ),
    ],
)
def test_addReceiptLetter_invalid_parameters(
    dynamodb_table,
    sample_receipt_letter,
    mocker,
    invalid_input,
    expected_error,
):
    """
    Tests that addReceiptLetter raises ValueError for invalid parameters:
    - When receipt letter is None
    - When receipt letter is not an instance of ReceiptLetter
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match=expected_error):
        client.addReceiptLetter(invalid_input)  # type: ignore


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
            "Could not add receipt letter to DynamoDB",
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
            "Could not add receipt letter to DynamoDB",
        ),
        (
            "ValidationException",
            "One or more parameters were invalid",
            "One or more parameters given were invalid",
        ),
        ("AccessDeniedException", "Access denied", "Access denied"),
    ],
)
def test_addReceiptLetter_client_errors(
    dynamodb_table,
    sample_receipt_letter,
    mocker,
    error_code,
    error_message,
    expected_exception,
):
    """
    Tests that addReceiptLetter handles various client errors appropriately:
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
        client.addReceiptLetter(sample_receipt_letter)
    mock_put.assert_called_once()


@pytest.mark.integration
def test_addReceiptLetter_raises_resource_not_found_with_invalid_table(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test handling of ResourceNotFoundException with invalid table name."""
    client = DynamoClient(dynamodb_table)
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ResourceNotFoundException",
                    "Message": "Table not found",
                }
            },
            "PutItem",
        ),
    )

    with pytest.raises(
        Exception, match="Could not add receipt letter to DynamoDB"
    ):
        client.addReceiptLetter(sample_receipt_letter)
    mock_put.assert_called_once()


@pytest.mark.integration
def test_addReceiptLetter_raises_validation_error(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test addReceiptLetter raises ValidationException."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.patch.object(client, "_client")
    mock_client.put_item.side_effect = ClientError(
        {
            "Error": {
                "Code": "ValidationException",
                "Message": "One or more parameters were invalid",
            }
        },
        "PutItem",
    )

    # Act & Assert
    with pytest.raises(
        Exception, match="One or more parameters given were invalid"
    ):
        client.addReceiptLetter(sample_receipt_letter)
    mock_client.put_item.assert_called_once()


@pytest.mark.integration
def test_addReceiptLetter_raises_access_denied(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test addReceiptLetter raises AccessDeniedException."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.patch.object(client, "_client")
    mock_client.put_item.side_effect = ClientError(
        {
            "Error": {
                "Code": "AccessDeniedException",
                "Message": "Access denied",
            }
        },
        "PutItem",
    )

    # Act & Assert
    with pytest.raises(Exception, match="Access denied"):
        client.addReceiptLetter(sample_receipt_letter)
    mock_client.put_item.assert_called_once()


# -------------------------------------------------------------------
#                        addReceiptLetters
# -------------------------------------------------------------------


@pytest.mark.integration
def test_addReceiptLetters_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_letter: ReceiptLetter,
):
    """
    Tests that addReceiptLetters adds multiple receipt letters successfully.
    """
    # Arrange
    client = DynamoClient(dynamodb_table)
    letters = [sample_receipt_letter]

    # Act
    client.addReceiptLetters(letters)

    # Assert
    retrieved_letter = client.getReceiptLetter(
        sample_receipt_letter.receipt_id,
        sample_receipt_letter.image_id,
        sample_receipt_letter.line_id,
        sample_receipt_letter.word_id,
        sample_receipt_letter.letter_id,
    )
    assert retrieved_letter == sample_receipt_letter


@pytest.mark.integration
def test_addReceiptLetters_with_large_batch(
    dynamodb_table, sample_receipt_letter
):
    """Test that addReceiptLetters handles batches larger than 25 items."""
    client = DynamoClient(dynamodb_table)

    # Create 30 letters (will require 2 batches)
    letters = []
    for i in range(30):
        letter = ReceiptLetter(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=10,
            word_id=5,
            letter_id=i,
            text=chr(65 + (i % 26)),  # A-Z using modulus
            bounding_box={"x": 0.0, "y": 0.0, "width": 0.01, "height": 0.02},
            top_left={"x": 0.0, "y": 0.0},
            top_right={"x": 0.01, "y": 0.0},
            bottom_left={"x": 0.0, "y": 0.02},
            bottom_right={"x": 0.01, "y": 0.02},
            angle_degrees=0,
            angle_radians=0,
            confidence=1.0,
        )
        letters.append(letter)

    # Should complete successfully
    client.addReceiptLetters(letters)

    # Verify all letters were added
    for letter in letters:
        retrieved_letter = client.getReceiptLetter(
            letter.receipt_id,
            letter.image_id,
            letter.line_id,
            letter.word_id,
            letter.letter_id,
        )
        assert retrieved_letter == letter


@pytest.mark.integration
def test_addReceiptLetters_with_unprocessed_items_retries(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test that addReceiptLetters retries unprocessed items."""
    client = DynamoClient(dynamodb_table)

    # First response has unprocessed items
    first_response = {
        "UnprocessedItems": {
            dynamodb_table: [
                {"PutRequest": {"Item": sample_receipt_letter.to_item()}}
            ]
        }
    }
    # Second response has no unprocessed items
    second_response = {"UnprocessedItems": {}}

    mock_batch = mocker.patch.object(
        client._client,
        "batch_write_item",
        side_effect=[first_response, second_response],
    )

    # Should complete successfully after retrying
    client.addReceiptLetters([sample_receipt_letter])

    # Verify both calls were made
    assert mock_batch.call_count == 2


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "letters parameter is required and cannot be None."),
        ("not-a-list", "letters must be a list of ReceiptLetter instances."),
        (
            ["not-a-receipt-letter"],
            "All letters must be instances of the ReceiptLetter class.",
        ),
    ],
)
def test_addReceiptLetters_invalid_parameters(
    dynamodb_table,
    sample_receipt_letter,
    mocker,
    invalid_input,
    expected_error,
):
    """Tests that addReceiptLetters raises ValueError for invalid parameters:
    - When letters is None
    - When letters is not a list
    - When letters contains non-ReceiptLetter instances
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match=expected_error):
        client.addReceiptLetters(invalid_input)  # type: ignore


@pytest.mark.integration
def test_addReceiptLetters_raises_resource_not_found_with_invalid_table(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test handling of ResourceNotFoundException with invalid table name."""
    client = DynamoClient(dynamodb_table)
    mock_batch = mocker.patch.object(
        client._client,
        "batch_write_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ResourceNotFoundException",
                    "Message": "Table not found",
                }
            },
            "BatchWriteItem",
        ),
    )

    with pytest.raises(
        Exception, match="Could not add ReceiptLetters to the database"
    ):
        client.addReceiptLetters([sample_receipt_letter])
    mock_batch.assert_called_once()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error_message",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Could not add ReceiptLetters to the database",
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
            "One or more parameters were invalid",
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
            "Could not add ReceiptLetters to the database",
        ),
    ],
)
def test_addReceiptLetters_client_errors(
    dynamodb_table,
    sample_receipt_letter,
    mocker,
    error_code,
    error_message,
    expected_error_message,
):
    """Test addReceiptLetters handles various DynamoDB client errors appropriately."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.patch.object(client, "_client")
    mock_client.batch_write_item.side_effect = ClientError(
        {
            "Error": {
                "Code": error_code,
                "Message": error_message,
            }
        },
        "BatchWriteItem",
    )

    # Act & Assert
    with pytest.raises(Exception, match=expected_error_message):
        client.addReceiptLetters([sample_receipt_letter])
    mock_client.batch_write_item.assert_called_once()


# -------------------------------------------------------------------
#                        updateReceiptLetter
# -------------------------------------------------------------------


@pytest.mark.integration
def test_updateReceiptLetter_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_letter: ReceiptLetter,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.addReceiptLetter(sample_receipt_letter)

    # Change the text
    sample_receipt_letter.text = "Z"
    client.updateReceiptLetter(sample_receipt_letter)

    # Assert
    retrieved_letter = client.getReceiptLetter(
        sample_receipt_letter.receipt_id,
        sample_receipt_letter.image_id,
        sample_receipt_letter.line_id,
        sample_receipt_letter.word_id,
        sample_receipt_letter.letter_id,
    )
    assert retrieved_letter.text == "Z"


@pytest.mark.integration
def test_updateReceiptLetter_raises_value_error_receipt_letter_none(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test handling of ValueError in updateReceiptLetter."""
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        ValueError, match="letter parameter is required and cannot be None."
    ):
        client.updateReceiptLetter(None)


@pytest.mark.integration
def test_updateReceiptLetter_raises_value_error_receipt_letter_not_instance(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test handling of ValueError in updateReceiptLetter."""
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        ValueError,
        match="letter must be an instance of the ReceiptLetter class.",
    ):
        client.updateReceiptLetter("not a ReceiptLetter")


@pytest.mark.integration
def test_updateReceiptLetter_raises_conditional_check_failed(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test handling of ConditionalCheckFailedException in
    updateReceiptLetter."""
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="ReceiptLetter with ID"):
        client.updateReceiptLetter(sample_receipt_letter)


@pytest.mark.integration
def test_updateReceiptLetter_raises_provisioned_throughput_exceeded(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test handling of ProvisionedThroughputExceededException in
    updateReceiptLetter.

    Verifies that the method raises an Exception when DynamoDB returns a
    ProvisionedThroughputExceededException.
    """
    client = DynamoClient(dynamodb_table)
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ProvisionedThroughputExceededException",
                    "Message": "Provisioned throughput exceeded",
                }
            },
            "PutItem",
        ),
    )
    with pytest.raises(Exception, match="Provisioned throughput exceeded"):
        client.updateReceiptLetter(sample_receipt_letter)
    mock_put.assert_called_once()


@pytest.mark.integration
def test_updateReceiptLetter_raises_internal_server_error(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test handling of InternalServerError in updateReceiptLetter."""
    client = DynamoClient(dynamodb_table)
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "InternalServerError",
                    "Message": "Internal server error",
                }
            },
            "PutItem",
        ),
    )
    with pytest.raises(Exception, match="Internal server error"):
        client.updateReceiptLetter(sample_receipt_letter)
    mock_put.assert_called_once()


@pytest.mark.integration
def test_updateReceiptLetter_raises_resource_not_found(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test handling of ResourceNotFoundException in updateReceiptLetter."""
    client = DynamoClient(dynamodb_table)
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ResourceNotFoundException",
                    "Message": "Table not found",
                }
            },
            "PutItem",
        ),
    )

    with pytest.raises(
        Exception, match="Could not update ReceiptLetter in the database"
    ):
        client.updateReceiptLetter(sample_receipt_letter)
    mock_put.assert_called_once()


@pytest.mark.integration
def test_updateReceiptLetter_raises_validation_error(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test updateReceiptLetter raises ValidationException."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.patch.object(client, "_client")
    mock_client.put_item.side_effect = ClientError(
        {
            "Error": {
                "Code": "ValidationException",
                "Message": "One or more parameters were invalid",
            }
        },
        "PutItem",
    )

    # Act & Assert
    with pytest.raises(
        Exception, match="One or more parameters given were invalid"
    ):
        client.updateReceiptLetter(sample_receipt_letter)
    mock_client.put_item.assert_called_once()


@pytest.mark.integration
def test_updateReceiptLetter_raises_access_denied(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test updateReceiptLetter raises AccessDeniedException."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.patch.object(client, "_client")
    mock_client.put_item.side_effect = ClientError(
        {
            "Error": {
                "Code": "AccessDeniedException",
                "Message": "Access denied",
            }
        },
        "PutItem",
    )

    # Act & Assert
    with pytest.raises(Exception, match="Access denied"):
        client.updateReceiptLetter(sample_receipt_letter)
    mock_client.put_item.assert_called_once()


@pytest.mark.integration
def test_updateReceiptLetter_raises_unknown_error(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test updateReceiptLetter raises unknown error."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.patch.object(client, "_client")
    mock_client.put_item.side_effect = ClientError(
        {
            "Error": {
                "Code": "UnknownError",
                "Message": "Unknown error occurred",
            }
        },
        "PutItem",
    )

    # Act & Assert
    with pytest.raises(
        Exception, match="Could not update ReceiptLetter in the database"
    ):
        client.updateReceiptLetter(sample_receipt_letter)
    mock_client.put_item.assert_called_once()


# -------------------------------------------------------------------
#                        updateReceiptLetters
# -------------------------------------------------------------------


@pytest.mark.integration
def test_updateReceiptLetters_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_letter: ReceiptLetter,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    letters = [
        ReceiptLetter(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=10,
            word_id=5,
            letter_id=i,
            text=str(i),
            bounding_box={"x": 0.0, "y": 0.0, "width": 0.01, "height": 0.02},
            top_left={"x": 0.0, "y": 0.0},
            top_right={"x": 0.01, "y": 0.0},
            bottom_left={"x": 0.0, "y": 0.02},
            bottom_right={"x": 0.01, "y": 0.02},
            angle_degrees=0,
            angle_radians=0,
            confidence=1.0,
        )
        for i in range(1, 4)
    ]
    # Add initial letters
    for lt in letters:
        client.addReceiptLetter(lt)

    # Modify the letters
    for lt in letters:
        lt.text = "Z"

    # Act
    client.updateReceiptLetters(letters)

    # Assert
    for lt in letters:
        retrieved_letter = client.getReceiptLetter(
            lt.receipt_id,
            lt.image_id,
            lt.line_id,
            lt.word_id,
            lt.letter_id,
        )
        assert retrieved_letter.text == "Z"


@pytest.mark.integration
def test_updateReceiptLetters_with_large_batch(
    dynamodb_table, sample_receipt_letter
):
    """Test that updateReceiptLetters handles batches larger than 25 items."""
    client = DynamoClient(dynamodb_table)

    # Create and add 30 letters (will require 2 batches)
    letters = []
    for i in range(30):
        letter = ReceiptLetter(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=10,
            word_id=5,
            letter_id=i,
            text=chr(65 + (i % 26)),  # A-Z using modulus
            bounding_box={"x": 0.0, "y": 0.0, "width": 0.01, "height": 0.02},
            top_left={"x": 0.0, "y": 0.0},
            top_right={"x": 0.01, "y": 0.0},
            bottom_left={"x": 0.0, "y": 0.02},
            bottom_right={"x": 0.01, "y": 0.02},
            angle_degrees=0,
            angle_radians=0,
            confidence=1.0,
        )
        letters.append(letter)
        client.addReceiptLetter(letter)

    # Update all letters
    for letter in letters:
        letter.text = "Z"  # Single character update

    # Should complete successfully
    client.updateReceiptLetters(letters)

    # Verify all letters were updated
    for letter in letters:
        retrieved_letter = client.getReceiptLetter(
            letter.receipt_id,
            letter.image_id,
            letter.line_id,
            letter.word_id,
            letter.letter_id,
        )
        assert retrieved_letter.text == "Z"


@pytest.mark.integration
def test_updateReceiptLetters_raises_resource_not_found(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test handling of ResourceNotFoundException in updateReceiptLetters."""
    client = DynamoClient(dynamodb_table)
    mock_transact = mocker.patch.object(
        client._client,
        "transact_write_items",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ResourceNotFoundException",
                    "Message": "Table not found",
                }
            },
            "TransactWriteItems",
        ),
    )

    with pytest.raises(
        Exception, match="Could not update ReceiptLetters in the database"
    ):
        client.updateReceiptLetters([sample_receipt_letter])
    mock_transact.assert_called_once()


@pytest.mark.integration
def test_updateReceiptLetters_raises_transaction_canceled(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test handling of TransactionCanceledException in
    updateReceiptLetters."""
    client = DynamoClient(dynamodb_table)
    mock_transact = mocker.patch.object(
        client._client,
        "transact_write_items",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "TransactionCanceledException",
                    "Message": "Transaction canceled due to "
                    "ConditionalCheckFailed",
                    "CancellationReasons": [
                        {"Code": "ConditionalCheckFailed"}
                    ],
                }
            },
            "TransactWriteItems",
        ),
    )

    with pytest.raises(
        ValueError, match="One or more ReceiptLetters do not exist"
    ):
        client.updateReceiptLetters([sample_receipt_letter])
    mock_transact.assert_called_once()


@pytest.mark.integration
def test_updateReceiptLetters_raises_value_error_letters_none(
    dynamodb_table, sample_receipt_letter, mocker
):
    """
    Tests that updateReceiptLetters raises ValueError when the letters are
    None.

    Verifies that the method raises a ValueError when the letters parameter
    is None.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        ValueError, match="letters parameter is required and cannot be None."
    ):
        client.updateReceiptLetters(None)  # type: ignore


@pytest.mark.integration
def test_updateReceiptLetters_raises_value_error_letters_not_list(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test ValueError when letters parameter is not a list.

    Verifies that updateReceiptLetters raises ValueError when the letters
    parameter has an invalid type.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        ValueError, match="letters must be a list of ReceiptLetter instances."
    ):
        client.updateReceiptLetters("not-a-list")  # type: ignore


@pytest.mark.integration
def test_updateReceiptLetters_error_invalid_letter_types(
    dynamodb_table, sample_receipt_letter, mocker
):
    """
    Tests that updateReceiptLetters raises ValueError when the letters are not
    a list of ReceiptLetter instances.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        ValueError,
        match="All letters must be instances of the ReceiptLetter class.",
    ):
        client.updateReceiptLetters(["not-a-receipt-letter"])  # type: ignore


@pytest.mark.integration
def test_updateReceiptLetters_raises_value_error_letter_not_found(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test ValueError when letter is not found.

    Verifies that updateReceiptLetters raises ValueError when attempting to
    update a non-existent letter.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        ValueError, match="One or more ReceiptLetters do not exist"
    ):
        client.updateReceiptLetters(
            [sample_receipt_letter]
        )  # Letter not added first


@pytest.mark.integration
def test_updateReceiptLetters_raises_internal_server_error(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test handling of InternalServerError in updateReceiptLetters."""
    client = DynamoClient(dynamodb_table)
    mock_transact = mocker.patch.object(
        client._client,
        "transact_write_items",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "InternalServerError",
                    "Message": "Internal server error",
                }
            },
            "TransactWriteItems",
        ),
    )

    with pytest.raises(Exception, match="Internal server error"):
        client.updateReceiptLetters([sample_receipt_letter])
    mock_transact.assert_called_once()


@pytest.mark.integration
def test_updateReceiptLetters_raises_provisioned_throughput_exceeded(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test handling of ProvisionedThroughputExceededException in
    updateReceiptLetters.

    Verifies that the method raises an Exception when DynamoDB returns a
    ProvisionedThroughputExceededException.
    """
    client = DynamoClient(dynamodb_table)
    mock_transact = mocker.patch.object(
        client._client,
        "transact_write_items",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ProvisionedThroughputExceededException",
                    "Message": "Provisioned throughput exceeded",
                }
            },
            "TransactWriteItems",
        ),
    )
    with pytest.raises(Exception, match="Provisioned throughput exceeded"):
        client.updateReceiptLetters([sample_receipt_letter])
    mock_transact.assert_called_once()


@pytest.mark.integration
def test_updateReceiptLetters_raises_validation_error(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test updateReceiptLetters raises ValidationException."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.patch.object(client, "_client")
    mock_client.transact_write_items.side_effect = ClientError(
        {
            "Error": {
                "Code": "ValidationException",
                "Message": "One or more parameters were invalid",
            }
        },
        "TransactWriteItems",
    )

    # Act & Assert
    with pytest.raises(
        Exception, match="One or more parameters given were invalid"
    ):
        client.updateReceiptLetters([sample_receipt_letter])
    mock_client.transact_write_items.assert_called_once()


@pytest.mark.integration
def test_updateReceiptLetters_raises_access_denied(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test updateReceiptLetters raises AccessDeniedException."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.patch.object(client, "_client")
    mock_client.transact_write_items.side_effect = ClientError(
        {
            "Error": {
                "Code": "AccessDeniedException",
                "Message": "Access denied",
            }
        },
        "TransactWriteItems",
    )

    # Act & Assert
    with pytest.raises(Exception, match="Access denied"):
        client.updateReceiptLetters([sample_receipt_letter])
    mock_client.transact_write_items.assert_called_once()


@pytest.mark.integration
def test_updateReceiptLetters_raises_unknown_error(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test updateReceiptLetters raises unknown error."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.patch.object(client, "_client")
    mock_client.transact_write_items.side_effect = ClientError(
        {
            "Error": {
                "Code": "UnknownError",
                "Message": "Unknown error occurred",
            }
        },
        "TransactWriteItems",
    )

    # Act & Assert
    with pytest.raises(
        Exception, match="Could not update ReceiptLetters in the database"
    ):
        client.updateReceiptLetters([sample_receipt_letter])
    mock_client.transact_write_items.assert_called_once()


# -------------------------------------------------------------------
#                        deleteReceiptLetter
# -------------------------------------------------------------------


@pytest.mark.integration
def test_deleteReceiptLetter_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_letter: ReceiptLetter,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.addReceiptLetter(sample_receipt_letter)

    # Act
    client.deleteReceiptLetter(sample_receipt_letter)

    # Assert
    with pytest.raises(ValueError, match="not found"):
        client.getReceiptLetter(
            sample_receipt_letter.receipt_id,
            sample_receipt_letter.image_id,
            sample_receipt_letter.line_id,
            sample_receipt_letter.word_id,
            sample_receipt_letter.letter_id,
        )


@pytest.mark.integration
def test_deleteReceiptLetter_raises_value_error_receipt_letter_none(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test handling of ValueError in deleteReceiptLetter."""
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        ValueError, match="letter parameter is required and cannot be None."
    ):
        client.deleteReceiptLetter(None)


@pytest.mark.integration
def test_deleteReceiptLetter_raises_value_error_receipt_letter_not_instance(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test handling of ValueError in deleteReceiptLetter."""
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        ValueError,
        match="letter must be an instance of the ReceiptLetter class.",
    ):
        client.deleteReceiptLetter("not a ReceiptLetter")


@pytest.mark.integration
def test_deleteReceiptLetter_raises_conditional_check_failed(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test handling of ConditionalCheckFailedException in
    deleteReceiptLetter.

    Verifies that the method raises a ValueError when DynamoDB returns a
    ConditionalCheckFailedException.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="ReceiptLetter with ID"):
        client.deleteReceiptLetter(sample_receipt_letter)


@pytest.mark.integration
def test_deleteReceiptLetter_raises_resource_not_found(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test handling of ResourceNotFoundException in deleteReceiptLetter."""
    client = DynamoClient(dynamodb_table)
    mock_delete = mocker.patch.object(
        client._client,
        "delete_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ResourceNotFoundException",
                    "Message": "Table not found",
                }
            },
            "DeleteItem",
        ),
    )

    with pytest.raises(
        Exception, match="Could not delete ReceiptLetter from the database"
    ):
        client.deleteReceiptLetter(sample_receipt_letter)
    mock_delete.assert_called_once()


@pytest.mark.integration
def test_deleteReceiptLetter_raises_provisioned_throughput_exceeded(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test handling of ProvisionedThroughputExceededException in
    deleteReceiptLetter.

    Verifies that the method raises an Exception when DynamoDB returns a
    ProvisionedThroughputExceededException.
    """
    client = DynamoClient(dynamodb_table)
    mock_delete = mocker.patch.object(
        client._client,
        "delete_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ProvisionedThroughputExceededException",
                    "Message": "Provisioned throughput exceeded",
                }
            },
            "DeleteItem",
        ),
    )
    with pytest.raises(Exception, match="Provisioned throughput exceeded"):
        client.deleteReceiptLetter(sample_receipt_letter)
    mock_delete.assert_called_once()


@pytest.mark.integration
def test_deleteReceiptLetter_raises_internal_server_error(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test handling of InternalServerError in deleteReceiptLetter."""
    client = DynamoClient(dynamodb_table)
    mock_delete = mocker.patch.object(
        client._client,
        "delete_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "InternalServerError",
                    "Message": "Internal server error",
                }
            },
            "DeleteItem",
        ),
    )
    with pytest.raises(Exception, match="Internal server error"):
        client.deleteReceiptLetter(sample_receipt_letter)
    mock_delete.assert_called_once()


@pytest.mark.integration
def test_deleteReceiptLetter_raises_validation_error(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test deleteReceiptLetter raises ValidationException."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.patch.object(client, "_client")
    mock_client.delete_item.side_effect = ClientError(
        {
            "Error": {
                "Code": "ValidationException",
                "Message": "One or more parameters were invalid",
            }
        },
        "DeleteItem",
    )

    # Act & Assert
    with pytest.raises(
        Exception, match="One or more parameters given were invalid"
    ):
        client.deleteReceiptLetter(sample_receipt_letter)
    mock_client.delete_item.assert_called_once()


@pytest.mark.integration
def test_deleteReceiptLetter_raises_access_denied(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test deleteReceiptLetter raises AccessDeniedException."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.patch.object(client, "_client")
    mock_client.delete_item.side_effect = ClientError(
        {
            "Error": {
                "Code": "AccessDeniedException",
                "Message": "Access denied",
            }
        },
        "DeleteItem",
    )

    # Act & Assert
    with pytest.raises(Exception, match="Access denied"):
        client.deleteReceiptLetter(sample_receipt_letter)
    mock_client.delete_item.assert_called_once()


@pytest.mark.integration
def test_deleteReceiptLetter_raises_unknown_error(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test deleteReceiptLetter raises unknown error."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.patch.object(client, "_client")
    mock_client.delete_item.side_effect = ClientError(
        {
            "Error": {
                "Code": "UnknownError",
                "Message": "Unknown error occurred",
            }
        },
        "DeleteItem",
    )

    # Act & Assert
    with pytest.raises(
        Exception, match="Could not delete ReceiptLetter from the database"
    ):
        client.deleteReceiptLetter(sample_receipt_letter)
    mock_client.delete_item.assert_called_once()


# -------------------------------------------------------------------
#                        deleteReceiptLetters
# -------------------------------------------------------------------


@pytest.mark.integration
def test_deleteReceiptLetters_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_letter: ReceiptLetter,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    letters = [
        ReceiptLetter(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=10,
            word_id=5,
            letter_id=i,
            text=str(i),
            bounding_box={"x": 0.0, "y": 0.0, "width": 0.01, "height": 0.02},
            top_left={"x": 0.0, "y": 0.0},
            top_right={"x": 0.01, "y": 0.0},
            bottom_left={"x": 0.0, "y": 0.02},
            bottom_right={"x": 0.01, "y": 0.02},
            angle_degrees=0,
            angle_radians=0,
            confidence=1.0,
        )
        for i in range(1, 4)
    ]
    # Add initial letters
    for lt in letters:
        client.addReceiptLetter(lt)

    # Act
    client.deleteReceiptLetters(letters)

    # Assert
    for lt in letters:
        with pytest.raises(ValueError, match="not found"):
            client.getReceiptLetter(
                lt.receipt_id,
                lt.image_id,
                lt.line_id,
                lt.word_id,
                lt.letter_id,
            )


@pytest.mark.integration
def test_deleteReceiptLetters_with_large_batch(dynamodb_table):
    """Test that deleteReceiptLetters handles batches larger than 25 items."""
    client = DynamoClient(dynamodb_table)

    # Create and add 30 letters (will require 2 batches)
    letters = []
    for i in range(30):
        letter = ReceiptLetter(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=10,
            word_id=5,
            letter_id=i,
            text=chr(65 + (i % 26)),  # A-Z using modulus
            bounding_box={"x": 0.0, "y": 0.0, "width": 0.01, "height": 0.02},
            top_left={"x": 0.0, "y": 0.0},
            top_right={"x": 0.01, "y": 0.0},
            bottom_left={"x": 0.0, "y": 0.02},
            bottom_right={"x": 0.01, "y": 0.02},
            angle_degrees=0,
            angle_radians=0,
            confidence=1.0,
        )
        letters.append(letter)
        client.addReceiptLetter(letter)

    # Delete all letters
    client.deleteReceiptLetters(letters)

    # Verify all letters were deleted
    for letter in letters:
        with pytest.raises(ValueError, match="not found"):
            client.getReceiptLetter(
                letter.receipt_id,
                letter.image_id,
                letter.line_id,
                letter.word_id,
                letter.letter_id,
            )


@pytest.mark.integration
def test_deleteReceiptLetters_raises_value_error_letters_none(
    dynamodb_table, mocker
):
    """Test handling of ValueError in deleteReceiptLetters."""
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        ValueError, match="letters parameter is required and cannot be None."
    ):
        client.deleteReceiptLetters(None)


@pytest.mark.integration
def test_deleteReceiptLetters_raises_value_error_letters_not_list(
    dynamodb_table, mocker
):
    """Test handling of ValueError in deleteReceiptLetters."""
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        ValueError, match="letters must be a list of ReceiptLetter instances."
    ):
        client.deleteReceiptLetters("not a list")


@pytest.mark.integration
def test_deleteReceiptLetters_error_invalid_letter_types(
    dynamodb_table, mocker
):
    """Test handling of ValueError in deleteReceiptLetters.

    Verifies that the method raises a ValueError when the letters parameter
    is not a list of ReceiptLetter instances.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        ValueError,
        match="All letters must be instances of the ReceiptLetter class.",
    ):
        client.deleteReceiptLetters([1, 2, 3])


@pytest.mark.integration
def test_deleteReceiptLetters_raises_resource_not_found(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test handling of ResourceNotFoundException in deleteReceiptLetters."""
    client = DynamoClient(dynamodb_table)
    mock_batch = mocker.patch.object(
        client._client,
        "batch_write_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ResourceNotFoundException",
                    "Message": "Table not found",
                }
            },
            "BatchWriteItem",
        ),
    )

    with pytest.raises(
        Exception, match="Could not delete ReceiptLetters from the database"
    ):
        client.deleteReceiptLetters([sample_receipt_letter])
    mock_batch.assert_called_once()


@pytest.mark.integration
def test_deleteReceiptLetters_with_unprocessed_items(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test that deleteReceiptLetters retries unprocessed items."""
    client = DynamoClient(dynamodb_table)

    # First response has unprocessed items
    first_response = {
        "UnprocessedItems": {
            dynamodb_table: [
                {"DeleteRequest": {"Key": sample_receipt_letter.key()}}
            ]
        }
    }
    # Second response has no unprocessed items
    second_response = {"UnprocessedItems": {}}

    mock_batch = mocker.patch.object(
        client._client,
        "batch_write_item",
        side_effect=[first_response, second_response],
    )

    # Should complete successfully after retrying
    client.deleteReceiptLetters([sample_receipt_letter])

    # Verify both calls were made
    assert mock_batch.call_count == 2


@pytest.mark.integration
def test_deleteReceiptLetters_raises_provisioned_throughput_exceeded(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test handling of ProvisionedThroughputExceededException in
    deleteReceiptLetters.

    Verifies that the method raises an Exception when DynamoDB returns a
    ProvisionedThroughputExceededException.
    """
    client = DynamoClient(dynamodb_table)
    mock_batch = mocker.patch.object(
        client._client,
        "batch_write_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ProvisionedThroughputExceededException",
                    "Message": "Provisioned throughput exceeded",
                }
            },
            "BatchWriteItem",
        ),
    )
    with pytest.raises(Exception, match="Provisioned throughput exceeded"):
        client.deleteReceiptLetters([sample_receipt_letter])
    mock_batch.assert_called_once()


@pytest.mark.integration
def test_deleteReceiptLetters_raises_internal_server_error(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test handling of InternalServerError in deleteReceiptLetters."""
    client = DynamoClient(dynamodb_table)
    mock_batch = mocker.patch.object(
        client._client,
        "batch_write_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "InternalServerError",
                    "Message": "Internal server error",
                }
            },
            "BatchWriteItem",
        ),
    )
    with pytest.raises(Exception, match="Internal server error"):
        client.deleteReceiptLetters([sample_receipt_letter])
    mock_batch.assert_called_once()


@pytest.mark.integration
def test_deleteReceiptLetters_raises_validation_error(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test deleteReceiptLetters raises ValidationException."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.patch.object(client, "_client")
    mock_client.batch_write_item.side_effect = ClientError(
        {
            "Error": {
                "Code": "ValidationException",
                "Message": "One or more parameters were invalid",
            }
        },
        "BatchWriteItem",
    )

    # Act & Assert
    with pytest.raises(
        ValueError, match="One or more parameters given were invalid"
    ):
        client.deleteReceiptLetters([sample_receipt_letter])
    mock_client.batch_write_item.assert_called_once()


@pytest.mark.integration
def test_deleteReceiptLetters_raises_access_denied(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test deleteReceiptLetters raises AccessDeniedException."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.patch.object(client, "_client")
    mock_client.batch_write_item.side_effect = ClientError(
        {
            "Error": {
                "Code": "AccessDeniedException",
                "Message": "Access denied",
            }
        },
        "BatchWriteItem",
    )

    # Act & Assert
    with pytest.raises(Exception, match="Access denied"):
        client.deleteReceiptLetters([sample_receipt_letter])
    mock_client.batch_write_item.assert_called_once()


@pytest.mark.integration
def test_deleteReceiptLetters_raises_unknown_error(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test deleteReceiptLetters raises unknown error."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.patch.object(client, "_client")
    mock_client.batch_write_item.side_effect = ClientError(
        {
            "Error": {
                "Code": "UnknownError",
                "Message": "Unknown error occurred",
            }
        },
        "BatchWriteItem",
    )

    # Act & Assert
    with pytest.raises(
        Exception, match="Could not delete ReceiptLetters from the database"
    ):
        client.deleteReceiptLetters([sample_receipt_letter])
    mock_client.batch_write_item.assert_called_once()


# -------------------------------------------------------------------
#                        getReceiptLetter
# -------------------------------------------------------------------


@pytest.mark.integration
def test_getReceiptLetter_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_letter: ReceiptLetter,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.addReceiptLetter(sample_receipt_letter)

    # Act
    retrieved_letter = client.getReceiptLetter(
        sample_receipt_letter.receipt_id,
        sample_receipt_letter.image_id,
        sample_receipt_letter.line_id,
        sample_receipt_letter.word_id,
        sample_receipt_letter.letter_id,
    )

    # Assert
    assert retrieved_letter == sample_receipt_letter


@pytest.mark.integration
def test_getReceiptLetter_raises_value_error_receipt_id_none(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test handling of ValueError in getReceiptLetter when receipt_id is
    None.

    Verifies that the method raises a ValueError when the receipt_id parameter
    is None.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        ValueError,
        match="receipt_id parameter is required and cannot be None.",
    ):
        client.getReceiptLetter(
            None,  # type: ignore
            sample_receipt_letter.image_id,
            sample_receipt_letter.line_id,
            sample_receipt_letter.word_id,
            sample_receipt_letter.letter_id,
        )


@pytest.mark.integration
def test_getReceiptLetter_raises_value_error_receipt_id_not_integer(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test handling of ValueError in getReceiptLetter when receipt_id is not
    an integer.

    Verifies that the method raises a ValueError when the receipt_id parameter
    is not an integer.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="receipt_id must be an integer."):
        client.getReceiptLetter(
            "not an integer",  # type: ignore
            sample_receipt_letter.image_id,
            sample_receipt_letter.line_id,
            sample_receipt_letter.word_id,
            sample_receipt_letter.letter_id,
        )


@pytest.mark.integration
def test_getReceiptLetter_raises_value_error_image_id_none(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test handling of ValueError in getReceiptLetter when image_id is None.

    Verifies that the method raises a ValueError when the image_id parameter
    is None.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        ValueError, match="image_id parameter is required and cannot be None."
    ):
        client.getReceiptLetter(
            sample_receipt_letter.receipt_id,
            None,  # type: ignore
            sample_receipt_letter.line_id,
            sample_receipt_letter.word_id,
            sample_receipt_letter.letter_id,
        )


@pytest.mark.integration
def test_getReceiptLetter_raises_value_error_image_id_not_uuid(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test handling of ValueError in getReceiptLetter when image_id is not a
    valid UUID.

    Verifies that the method raises a ValueError when the image_id parameter
    is not a valid UUID.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="uuid must be a valid UUID"):
        client.getReceiptLetter(
            sample_receipt_letter.receipt_id,
            "not a valid UUID",  # type: ignore
            sample_receipt_letter.line_id,
            sample_receipt_letter.word_id,
            sample_receipt_letter.letter_id,
        )


@pytest.mark.integration
def test_getReceiptLetter_raises_value_error_line_id_none(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test handling of ValueError in getReceiptLetter when line_id is None."""
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        ValueError, match="line_id parameter is required and cannot be None."
    ):
        client.getReceiptLetter(
            sample_receipt_letter.receipt_id,
            sample_receipt_letter.image_id,
            None,  # type: ignore
            sample_receipt_letter.word_id,
            sample_receipt_letter.letter_id,
        )


@pytest.mark.integration
def test_getReceiptLetter_raises_value_error_line_id_not_integer(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test handling of ValueError in getReceiptLetter when line_id is not an
    integer.

    Verifies that the method raises a ValueError when the line_id parameter
    is not an integer.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="line_id must be an integer."):
        client.getReceiptLetter(
            sample_receipt_letter.receipt_id,
            sample_receipt_letter.image_id,
            "not an integer",  # type: ignore
            sample_receipt_letter.word_id,
            sample_receipt_letter.letter_id,
        )


@pytest.mark.integration
def test_getReceiptLetter_raises_value_error_word_id_none(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test handling of ValueError in getReceiptLetter when word_id is None."""
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        ValueError, match="word_id parameter is required and cannot be None."
    ):
        client.getReceiptLetter(
            sample_receipt_letter.receipt_id,
            sample_receipt_letter.image_id,
            sample_receipt_letter.line_id,
            None,  # type: ignore
            sample_receipt_letter.letter_id,
        )


@pytest.mark.integration
def test_getReceiptLetter_raises_value_error_word_id_not_integer(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test handling of ValueError in getReceiptLetter when word_id is not an
    integer.

    Verifies that the method raises a ValueError when the word_id parameter
    is not an integer.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="word_id must be an integer."):
        client.getReceiptLetter(
            sample_receipt_letter.receipt_id,
            sample_receipt_letter.image_id,
            sample_receipt_letter.line_id,
            "not an integer",  # type: ignore
            sample_receipt_letter.letter_id,
        )


@pytest.mark.integration
def test_getReceiptLetter_raises_value_error_letter_id_none(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test handling of ValueError in getReceiptLetter when letter_id is None.

    Verifies that the method raises a ValueError when the letter_id parameter
    is None.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        ValueError, match="letter_id parameter is required and cannot be None."
    ):
        client.getReceiptLetter(
            sample_receipt_letter.receipt_id,
            sample_receipt_letter.image_id,
            sample_receipt_letter.line_id,
            sample_receipt_letter.word_id,
            None,  # type: ignore
        )


@pytest.mark.integration
def test_getReceiptLetter_raises_value_error_letter_id_not_integer(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test handling of ValueError in getReceiptLetter when letter_id is not an
    integer.

    Verifies that the method raises a ValueError when the letter_id parameter
    is not an integer.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="letter_id must be an integer."):
        client.getReceiptLetter(
            sample_receipt_letter.receipt_id,
            sample_receipt_letter.image_id,
            sample_receipt_letter.line_id,
            sample_receipt_letter.word_id,
            "not an integer",  # type: ignore
        )


@pytest.mark.integration
def test_getReceiptLetter_not_found(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_letter: ReceiptLetter,
):
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act & Assert
    with pytest.raises(ValueError, match="not found"):
        client.getReceiptLetter(
            sample_receipt_letter.receipt_id,
            sample_receipt_letter.image_id,
            sample_receipt_letter.line_id,
            sample_receipt_letter.word_id,
            sample_receipt_letter.letter_id,
        )


@pytest.mark.integration
def test_getReceiptLetter_invalid_receipt_id(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_letter: ReceiptLetter,
):
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="receipt_id must be an integer"):
        client.getReceiptLetter(
            "invalid",  # type: ignore
            sample_receipt_letter.image_id,
            sample_receipt_letter.line_id,
            sample_receipt_letter.word_id,
            sample_receipt_letter.letter_id,
        )


@pytest.mark.integration
def test_getReceiptLetter_invalid_image_id(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_letter: ReceiptLetter,
):
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="uuid must be a valid UUID"):
        client.getReceiptLetter(
            sample_receipt_letter.receipt_id,
            "invalid-uuid",
            sample_receipt_letter.line_id,
            sample_receipt_letter.word_id,
            sample_receipt_letter.letter_id,
        )


@pytest.mark.integration
def test_getReceiptLetter_invalid_line_id(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_letter: ReceiptLetter,
):
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="line_id must be an integer"):
        client.getReceiptLetter(
            sample_receipt_letter.receipt_id,
            sample_receipt_letter.image_id,
            "invalid",  # type: ignore
            sample_receipt_letter.word_id,
            sample_receipt_letter.letter_id,
        )


@pytest.mark.integration
def test_getReceiptLetter_invalid_word_id(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_letter: ReceiptLetter,
):
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="word_id must be an integer"):
        client.getReceiptLetter(
            sample_receipt_letter.receipt_id,
            sample_receipt_letter.image_id,
            sample_receipt_letter.line_id,
            "invalid",  # type: ignore
            sample_receipt_letter.letter_id,
        )


@pytest.mark.integration
def test_getReceiptLetter_invalid_letter_id(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_letter: ReceiptLetter,
):
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="letter_id must be an integer"):
        client.getReceiptLetter(
            sample_receipt_letter.receipt_id,
            sample_receipt_letter.image_id,
            sample_receipt_letter.line_id,
            sample_receipt_letter.word_id,
            "invalid",  # type: ignore
        )


@pytest.mark.integration
def test_getReceiptLetter_raises_provisioned_throughput_exceeded_exception(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test getReceiptLetter raises ProvisionedThroughputExceededException."""
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.patch.object(client, "_client")
    mock_client.get_item.side_effect = ClientError(
        {
            "Error": {
                "Code": "ProvisionedThroughputExceededException",
                "Message": "Provisioned throughput exceeded",
            }
        },
        "GetItem",
    )

    # Act & Assert
    with pytest.raises(Exception, match="Provisioned throughput exceeded"):
        client.getReceiptLetter(
            receipt_id=sample_receipt_letter.receipt_id,
            image_id=sample_receipt_letter.image_id,
            line_id=sample_receipt_letter.line_id,
            word_id=sample_receipt_letter.word_id,
            letter_id=sample_receipt_letter.letter_id,
        )
    mock_client.get_item.assert_called_once()


@pytest.mark.integration
def test_getReceiptLetter_raises_internal_server_error(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test getReceiptLetter raises InternalServerError."""
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.patch.object(client, "_client")
    mock_client.get_item.side_effect = ClientError(
        {
            "Error": {
                "Code": "InternalServerError",
                "Message": "Internal server error",
            }
        },
        "GetItem",
    )

    # Act & Assert
    with pytest.raises(Exception, match="Internal server error"):
        client.getReceiptLetter(
            receipt_id=sample_receipt_letter.receipt_id,
            image_id=sample_receipt_letter.image_id,
            line_id=sample_receipt_letter.line_id,
            word_id=sample_receipt_letter.word_id,
            letter_id=sample_receipt_letter.letter_id,
        )
    mock_client.get_item.assert_called_once()


@pytest.mark.integration
def test_getReceiptLetter_raises_validation_error(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test getReceiptLetter raises ValidationException."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.patch.object(client, "_client")
    mock_client.get_item.side_effect = ClientError(
        {
            "Error": {
                "Code": "ValidationException",
                "Message": "One or more parameters were invalid",
            }
        },
        "GetItem",
    )

    # Act & Assert
    with pytest.raises(Exception, match="Validation error"):
        client.getReceiptLetter(
            receipt_id=sample_receipt_letter.receipt_id,
            image_id=sample_receipt_letter.image_id,
            line_id=sample_receipt_letter.line_id,
            word_id=sample_receipt_letter.word_id,
            letter_id=sample_receipt_letter.letter_id,
        )
    mock_client.get_item.assert_called_once()


@pytest.mark.integration
def test_getReceiptLetter_raises_access_denied(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test that getReceiptLetter raises an exception when access is denied.

    Verifies that the method raises an Exception when DynamoDB returns an
    AccessDeniedException.
    """
    # Arrange
    mock_client = mocker.Mock()
    mock_client.get_item.side_effect = ClientError(
        {
            "Error": {
                "Code": "AccessDeniedException",
                "Message": "User is not authorized to perform this operation",
            }
        },
        "GetItem",
    )
    client = DynamoClient(dynamodb_table)
    client._client = mock_client

    # Act & Assert
    with pytest.raises(Exception, match="^Access denied: .*"):
        client.getReceiptLetter(
            receipt_id=sample_receipt_letter.receipt_id,
            image_id=sample_receipt_letter.image_id,
            line_id=sample_receipt_letter.line_id,
            word_id=sample_receipt_letter.word_id,
            letter_id=sample_receipt_letter.letter_id,
        )
    mock_client.get_item.assert_called_once_with(
        TableName=dynamodb_table,
        Key={
            "PK": {"S": f"IMAGE#{sample_receipt_letter.image_id}"},
            "SK": {
                "S": (
                    f"RECEIPT#{sample_receipt_letter.receipt_id:05d}#"
                    f"LINE#{sample_receipt_letter.line_id:05d}#"
                    f"WORD#{sample_receipt_letter.word_id:05d}#"
                    f"LETTER#{sample_receipt_letter.letter_id:05d}"
                )
            },
        },
    )


@pytest.mark.integration
def test_getReceiptLetter_raises_resource_not_found(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test that getReceiptLetter raises an exception when resource is not
    found.

    Verifies that the method raises an Exception when DynamoDB returns a
    ResourceNotFoundException.
    """
    # Arrange
    mock_client = mocker.Mock()
    mock_client.get_item.side_effect = ClientError(
        {
            "Error": {
                "Code": "ResourceNotFoundException",
                "Message": "The table does not exist or is not active",
            }
        },
        "GetItem",
    )
    client = DynamoClient(dynamodb_table)
    client._client = mock_client

    # Act & Assert
    with pytest.raises(Exception, match="^Error getting receipt letter: .*"):
        client.getReceiptLetter(
            receipt_id=sample_receipt_letter.receipt_id,
            image_id=sample_receipt_letter.image_id,
            line_id=sample_receipt_letter.line_id,
            word_id=sample_receipt_letter.word_id,
            letter_id=sample_receipt_letter.letter_id,
        )
    mock_client.get_item.assert_called_once_with(
        TableName=dynamodb_table,
        Key={
            "PK": {"S": f"IMAGE#{sample_receipt_letter.image_id}"},
            "SK": {
                "S": (
                    f"RECEIPT#{sample_receipt_letter.receipt_id:05d}#"
                    f"LINE#{sample_receipt_letter.line_id:05d}#"
                    f"WORD#{sample_receipt_letter.word_id:05d}#"
                    f"LETTER#{sample_receipt_letter.letter_id:05d}"
                )
            },
        },
    )


# -------------------------------------------------------------------
#                        listReceiptLetters
# -------------------------------------------------------------------


@pytest.mark.integration
def test_listReceiptLetters_success(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    letters = [
        ReceiptLetter(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=10,
            word_id=5,
            letter_id=i,
            text=str(i),
            bounding_box={"x": 0.0, "y": 0.0, "width": 0.01, "height": 0.02},
            top_left={"x": 0.0, "y": 0.0},
            top_right={"x": 0.01, "y": 0.0},
            bottom_left={"x": 0.0, "y": 0.02},
            bottom_right={"x": 0.01, "y": 0.02},
            angle_degrees=0,
            angle_radians=0,
            confidence=1.0,
        )
        for i in range(1, 4)
    ]
    for lt in letters:
        client.addReceiptLetter(lt)

    # Act
    returned_letters, _ = client.listReceiptLetters()

    # Assert
    for lt in letters:
        assert lt in returned_letters


@pytest.mark.integration
def test_listReceiptLetters_with_limit(
    dynamodb_table: Literal["MyMockedTable"],
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    letters = [
        ReceiptLetter(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=10,
            word_id=5,
            letter_id=i,
            text=str(i),
            bounding_box={"x": 0.0, "y": 0.0, "width": 0.01, "height": 0.02},
            top_left={"x": 0.0, "y": 0.0},
            top_right={"x": 0.01, "y": 0.0},
            bottom_left={"x": 0.0, "y": 0.02},
            bottom_right={"x": 0.01, "y": 0.02},
            angle_degrees=0,
            angle_radians=0,
            confidence=1.0,
        )
        for i in range(1, 6)  # Create 5 letters
    ]
    for lt in letters:
        client.addReceiptLetter(lt)

    # Act - Get first 2 letters
    returned_letters, last_key = client.listReceiptLetters(limit=2)

    # Assert
    assert len(returned_letters) == 2
    assert (
        last_key is not None
    )  # Should have a last evaluated key for pagination
    for lt in returned_letters:
        assert lt in letters

    # Act - Get next batch using last_key
    next_letters, next_last_key = client.listReceiptLetters(
        limit=2, lastEvaluatedKey=last_key
    )

    # Assert
    assert len(next_letters) == 2
    assert next_last_key is not None
    for lt in next_letters:
        assert lt in letters
    # Verify we got different letters
    assert set(lt.letter_id for lt in next_letters).isdisjoint(
        set(lt.letter_id for lt in returned_letters)
    )


@pytest.mark.integration
def test_listReceiptLetters_invalid_limit(
    dynamodb_table: Literal["MyMockedTable"],
):
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="limit must be an integer or None"):
        client.listReceiptLetters(limit="invalid")  # type: ignore


@pytest.mark.integration
def test_listReceiptLetters_invalid_last_evaluated_key(
    dynamodb_table: Literal["MyMockedTable"],
):
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        ValueError, match="lastEvaluatedKey must be a dictionary or None"
    ):
        client.listReceiptLetters(lastEvaluatedKey="invalid")  # type: ignore


@pytest.mark.integration
def test_listReceiptLetters_raises_resource_not_found_exception(
    dynamodb_table, mocker
):
    """Test handling of ResourceNotFoundException in listReceiptLetters."""
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.patch.object(
        client._client,
        "query",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ResourceNotFoundException",
                    "Message": "Resource not found",
                }
            },
            "Query",
        ),
    )
    with pytest.raises(Exception, match="Resource not found"):
        client.listReceiptLetters()
    mock_client.assert_called_once()


@pytest.mark.integration
def test_listReceiptLetters_raises_validation_error(dynamodb_table, mocker):
    """Test handling of ValidationException in listReceiptLetters."""
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.patch.object(
        client._client,
        "query",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ValidationException",
                    "Message": "Invalid parameters",
                }
            },
            "Query",
        ),
    )
    with pytest.raises(
        ValueError, match="One or more parameters given were invalid"
    ):
        client.listReceiptLetters()
    mock_client.assert_called_once()


@pytest.mark.integration
def test_listReceiptLetters_raises_internal_server_error(
    dynamodb_table, mocker
):
    """Test handling of InternalServerError in listReceiptLetters."""
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.patch.object(
        client._client,
        "query",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "InternalServerError",
                    "Message": "Internal server error",
                }
            },
            "Query",
        ),
    )
    with pytest.raises(Exception, match="Internal server error"):
        client.listReceiptLetters()
    mock_client.assert_called_once()


@pytest.mark.integration
def test_listReceiptLetters_raises_provisioned_throughput_exceeded(
    dynamodb_table, mocker
):
    """Test handling of ProvisionedThroughputExceededException in
    listReceiptLetters.

    Verifies that the method raises an Exception when DynamoDB returns a
    ProvisionedThroughputExceededException.
    """
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.patch.object(
        client._client,
        "query",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ProvisionedThroughputExceededException",
                    "Message": "Throughput exceeded",
                }
            },
            "Query",
        ),
    )
    with pytest.raises(Exception, match="Provisioned throughput exceeded"):
        client.listReceiptLetters()
    mock_client.assert_called_once()


@pytest.mark.integration
def test_listReceiptLetters_with_pagination(
    dynamodb_table: Literal["MyMockedTable"],
):
    """Test pagination in listReceiptLetters with more than 25 items."""
    # Arrange
    client = DynamoClient(dynamodb_table)
    letters = [
        ReceiptLetter(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=10,
            word_id=5,
            letter_id=i,
            text=str(i),
            bounding_box={"x": 0.0, "y": 0.0, "width": 0.01, "height": 0.02},
            top_left={"x": 0.0, "y": 0.0},
            top_right={"x": 0.01, "y": 0.0},
            bottom_left={"x": 0.0, "y": 0.02},
            bottom_right={"x": 0.01, "y": 0.02},
            angle_degrees=0,
            angle_radians=0,
            confidence=1.0,
        )
        for i in range(1, 10)  # Create 10 letters to force pagination
    ]
    for lt in letters:
        client.addReceiptLetter(lt)

    # Act
    returned_letters, _ = client.listReceiptLetters()

    # Assert
    assert len(returned_letters) == 9
    for lt in letters:
        assert lt in returned_letters


# -------------------------------------------------------------------
#                        listReceiptLettersFromWord
# -------------------------------------------------------------------


@pytest.mark.integration
def test_listReceiptLettersFromWord_success(
    dynamodb_table: Literal["MyMockedTable"],
):
    # Arrange
    client = DynamoClient(dynamodb_table)

    letters_same_word = [
        ReceiptLetter(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=10,
            word_id=2,
            letter_id=i,
            text=f"{i}",
            bounding_box={"x": 0.0, "y": 0.0, "width": 0.01, "height": 0.01},
            top_left={"x": 0.0, "y": 0.0},
            top_right={"x": 0.01, "y": 0.0},
            bottom_left={"x": 0.0, "y": 0.01},
            bottom_right={"x": 0.01, "y": 0.01},
            angle_degrees=0,
            angle_radians=0,
            confidence=1.0,
        )
        for i in range(1, 4)
    ]
    # A letter in a different word
    different_word_letter = ReceiptLetter(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=10,
        word_id=99,
        letter_id=999,
        text="x",
        bounding_box={"x": 0.2, "y": 0.2, "width": 0.01, "height": 0.01},
        top_left={"x": 0.2, "y": 0.2},
        top_right={"x": 0.21, "y": 0.2},
        bottom_left={"x": 0.2, "y": 0.21},
        bottom_right={"x": 0.21, "y": 0.21},
        angle_degrees=5,
        angle_radians=0.0872665,
        confidence=0.9,
    )

    for lt in letters_same_word + [different_word_letter]:
        client.addReceiptLetter(lt)

    # Act
    found_letters = client.listReceiptLettersFromWord(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=10,
        word_id=2,
    )

    # Assert
    assert len(found_letters) == 3
    for lt in letters_same_word:
        assert lt in found_letters
    assert different_word_letter not in found_letters


@pytest.mark.integration
def test_listReceiptLettersFromWord_receipt_id_is_none(
    dynamodb_table: Literal["MyMockedTable"],
):
    """Test that listReceiptLettersFromWord raises ValueError when receipt_id
    is None.

    Verifies that the method raises a ValueError when the receipt_id parameter
    is None.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        ValueError,
        match="receipt_id parameter is required and cannot be None.",
    ):
        client.listReceiptLettersFromWord(
            receipt_id=None,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=10,
            word_id=2,
        )


@pytest.mark.integration
def test_listReceiptLettersFromWord_receipt_id_is_not_int(
    dynamodb_table: Literal["MyMockedTable"],
):
    """Test that listReceiptLettersFromWord raises ValueError when receipt_id
    is not an integer.

    Verifies that the method raises a ValueError when the receipt_id parameter
    is not an integer.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="receipt_id must be an integer."):
        client.listReceiptLettersFromWord(
            receipt_id="not_an_integer",
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=10,
            word_id=2,
        )


@pytest.mark.integration
def test_listReceiptLettersFromWord_image_id_is_none(
    dynamodb_table: Literal["MyMockedTable"],
):
    """Test that listReceiptLettersFromWord raises ValueError when image_id
    is None.

    Verifies that the method raises a ValueError when the image_id parameter
    is None.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        ValueError, match="image_id parameter is required and cannot be None."
    ):
        client.listReceiptLettersFromWord(
            receipt_id=1,
            image_id=None,
            line_id=10,
            word_id=2,
        )


@pytest.mark.integration
def test_listReceiptLettersFromWord_image_id_is_not_valid_uuid(
    dynamodb_table: Literal["MyMockedTable"],
):
    """
    Test that listReceiptLettersFromWord raises ValueError when image_id is
    not a valid UUID.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="uuid must be a valid UUID."):
        client.listReceiptLettersFromWord(
            receipt_id=1,
            image_id="not_a_valid_uuid",
            line_id=10,
            word_id=2,
        )


@pytest.mark.integration
def test_listReceiptLettersFromWord_line_id_is_none(
    dynamodb_table: Literal["MyMockedTable"],
):
    """Test that listReceiptLettersFromWord raises ValueError when line_id
    is None.

    Verifies that the method raises a ValueError when the line_id parameter
    is None.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        ValueError, match="line_id parameter is required and cannot be None."
    ):
        client.listReceiptLettersFromWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=None,
            word_id=2,
        )


@pytest.mark.integration
def test_listReceiptLettersFromWord_line_id_is_not_int(
    dynamodb_table: Literal["MyMockedTable"],
):
    """Test that listReceiptLettersFromWord raises ValueError when line_id
    is not an integer.

    Verifies that the method raises a ValueError when the line_id parameter
    is not an integer.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="line_id must be an integer."):
        client.listReceiptLettersFromWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id="not_an_integer",
            word_id=2,
        )


@pytest.mark.integration
def test_listReceiptLettersFromWord_word_id_is_none(
    dynamodb_table: Literal["MyMockedTable"],
):
    """Test that listReceiptLettersFromWord raises ValueError when word_id
    is None.

    Verifies that the method raises a ValueError when the word_id parameter
    is None.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        ValueError, match="word_id parameter is required and cannot be None."
    ):
        client.listReceiptLettersFromWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=10,
            word_id=None,
        )


@pytest.mark.integration
def test_listReceiptLettersFromWord_word_id_is_not_int(
    dynamodb_table: Literal["MyMockedTable"],
):
    """Test that listReceiptLettersFromWord raises ValueError when word_id
    is not an integer.

    Verifies that the method raises a ValueError when the word_id parameter
    is not an integer.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="word_id must be an integer."):
        client.listReceiptLettersFromWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=10,
            word_id="not_an_integer",
        )


@pytest.mark.integration
def test_listReceiptLettersFromWord_returns_empty_list_when_not_found(
    dynamodb_table: Literal["MyMockedTable"],
):
    """Test that listReceiptLettersFromWord returns empty list when no letters
    exist.

    Verifies that the method returns an empty list when no letters are found
    for the given parameters.
    """
    client = DynamoClient(dynamodb_table)
    found_letters = client.listReceiptLettersFromWord(
        receipt_id=999,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=999,
        word_id=999,
    )
    assert isinstance(found_letters, list)
    assert len(found_letters) == 0


@pytest.mark.integration
def test_listReceiptLettersFromWord_raises_client_error(
    dynamodb_table: Literal["MyMockedTable"], mocker
):
    """Test that listReceiptLettersFromWord raises Exception.

    Verifies that the method raises an Exception when DynamoDB returns an
    internal server error.
    """
    client = DynamoClient(dynamodb_table)

    # Mock the query method to raise an InternalServerError
    mock_client = mocker.patch.object(client._client, "query")
    mock_client.side_effect = ClientError(
        {
            "Error": {
                "Code": "InternalServerError",
                "Message": "Internal error",
            }
        },
        "Query",
    )

    with pytest.raises(
        Exception,
        match=(
            "Internal server error: An error occurred "
            "\\(InternalServerError\\) when calling the Query operation: "
            "Internal error"
        ),
    ):
        client.listReceiptLettersFromWord(
            receipt_id=999,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=999,
            word_id=999,
        )


@pytest.mark.integration
def test_listReceiptLettersFromWord_raises_provisioned_throughput_exceeded(
    dynamodb_table: Literal["MyMockedTable"], mocker
):
    """Test that listReceiptLettersFromWord raises Exception on throughput
    exceeded."""
    client = DynamoClient(dynamodb_table)

    # Mock the query method to raise a ProvisionedThroughputExceededException
    mock_client = mocker.patch.object(client._client, "query")
    mock_client.side_effect = ClientError(
        {
            "Error": {
                "Code": "ProvisionedThroughputExceededException",
                "Message": "Throughput exceeded",
            }
        },
        "Query",
    )

    with pytest.raises(
        Exception,
        match=(
            "Provisioned throughput exceeded: An error occurred "
            "\\(ProvisionedThroughputExceededException\\) when calling "
            "the Query operation: Throughput exceeded"
        ),
    ):
        client.listReceiptLettersFromWord(
            receipt_id=999,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=999,
            word_id=999,
        )


@pytest.mark.integration
def test_listReceiptLettersFromWord_raises_validation_error(
    dynamodb_table: Literal["MyMockedTable"], mocker
):
    """Test that listReceiptLettersFromWord raises Exception on validation
    error.

    Verifies that the method raises an Exception when DynamoDB returns a
    ValidationException.
    """
    client = DynamoClient(dynamodb_table)

    # Mock the query method to raise a ValidationException
    mock_client = mocker.patch.object(client._client, "query")
    mock_client.side_effect = ClientError(
        {
            "Error": {
                "Code": "ValidationException",
                "Message": "Invalid parameters",
            }
        },
        "Query",
    )

    error_msg = "One or more parameters given were invalid:"
    with pytest.raises(Exception, match=error_msg):
        client.listReceiptLettersFromWord(
            receipt_id=999,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=999,
            word_id=999,
        )


@pytest.mark.integration
def test_listReceiptLettersFromWord_with_pagination(
    dynamodb_table: Literal["MyMockedTable"], mocker
):
    """Test listReceiptLettersFromWord with pagination."""
    client = DynamoClient(dynamodb_table)

    # Create and add multiple letters for the same word
    letters = []
    for i in range(5):
        letter = ReceiptLetter(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=10,
            word_id=5,
            letter_id=i,
            text=chr(65 + i),  # A, B, C, D, E
            bounding_box={"x": 0.0, "y": 0.0, "width": 0.01, "height": 0.02},
            top_left={"x": 0.0, "y": 0.0},
            top_right={"x": 0.01, "y": 0.0},
            bottom_left={"x": 0.0, "y": 0.02},
            bottom_right={"x": 0.01, "y": 0.02},
            angle_degrees=0,
            angle_radians=0,
            confidence=1.0,
        )
        letters.append(letter)
        client.addReceiptLetter(letter)

    # Mock pagination behavior
    first_response = {
        "Items": [letter.to_item() for letter in letters[:3]],
        "LastEvaluatedKey": {"PK": {"S": "some-key"}},
    }
    second_response = {
        "Items": [letter.to_item() for letter in letters[3:]],
    }

    mocker.patch.object(
        client._client, "query", side_effect=[first_response, second_response]
    )

    result = client.listReceiptLettersFromWord(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=10,
        word_id=5,
    )

    # Verify all letters were retrieved
    assert len(result) == 5
    for letter in letters:
        assert letter in result


@pytest.mark.integration
def test_listReceiptLettersFromWord_raises_provisioned_throughput_exceeded(
    dynamodb_table: Literal["MyMockedTable"], mocker
):
    """Test handling of ProvisionedThroughputExceededException in
    listReceiptLettersFromWord.

    Verifies that the method raises an Exception when DynamoDB returns a
    ProvisionedThroughputExceededException.
    """
    client = DynamoClient(dynamodb_table)
    mock_query = mocker.patch.object(
        client._client,
        "query",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ProvisionedThroughputExceededException",
                    "Message": "Throughput exceeded",
                }
            },
            "Query",
        ),
    )

    with pytest.raises(Exception, match="Provisioned throughput exceeded"):
        client.listReceiptLettersFromWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=10,
            word_id=5,
        )
    mock_query.assert_called_once()


@pytest.mark.integration
def test_listReceiptLettersFromWord_raises_validation_error(
    dynamodb_table: Literal["MyMockedTable"], mocker
):
    """Test handling of ValidationException in listReceiptLettersFromWord."""
    client = DynamoClient(dynamodb_table)
    mock_query = mocker.patch.object(
        client._client,
        "query",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ValidationException",
                    "Message": "Invalid parameters",
                }
            },
            "Query",
        ),
    )

    with pytest.raises(
        Exception, match="One or more parameters given were invalid"
    ):
        client.listReceiptLettersFromWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=10,
            word_id=5,
        )
    mock_query.assert_called_once()


@pytest.mark.integration
def test_listReceiptLettersFromWord_raises_internal_server_error(
    dynamodb_table: Literal["MyMockedTable"], mocker
):
    """Test handling of InternalServerError in listReceiptLettersFromWord."""
    client = DynamoClient(dynamodb_table)
    mock_query = mocker.patch.object(
        client._client,
        "query",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "InternalServerError",
                    "Message": "Internal server error",
                }
            },
            "Query",
        ),
    )

    with pytest.raises(Exception, match="Internal server error"):
        client.listReceiptLettersFromWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=10,
            word_id=5,
        )
    mock_query.assert_called_once()


@pytest.mark.integration
def test_listReceiptLettersFromWord_raises_unknown_error(
    dynamodb_table: Literal["MyMockedTable"], mocker
):
    """Test handling of unknown error in listReceiptLettersFromWord."""
    client = DynamoClient(dynamodb_table)
    mock_query = mocker.patch.object(
        client._client,
        "query",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "UnknownError",
                    "Message": "Unknown error",
                }
            },
            "Query",
        ),
    )

    with pytest.raises(
        Exception, match="Could not list ReceiptLetters from the database"
    ):
        client.listReceiptLettersFromWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=10,
            word_id=5,
        )
    mock_query.assert_called_once()


@pytest.mark.integration
def test_listReceiptLetters_pagination_resource_not_found(
    dynamodb_table, mocker
):
    """Test handling of ResourceNotFoundException during pagination in
    listReceiptLetters.

    Verifies that we get a partial result before the exception occurs.
    """
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.Mock()
    mock_client.query.side_effect = [
        {
            "Items": [
                {
                    "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
                    "SK": {
                        "S": "RECEIPT#00001#LINE#00001#WORD#00001#LETTER#00001"
                    },
                    "TYPE": {"S": "RECEIPT_LETTER"},
                    "text": {"S": "A"},
                    "bounding_box": {
                        "M": {
                            "x": {"N": "0.1"},
                            "y": {"N": "0.2"},
                            "width": {"N": "0.05"},
                            "height": {"N": "0.05"},
                        }
                    },
                    "top_right": {
                        "M": {"x": {"N": "0.15"}, "y": {"N": "0.25"}}
                    },
                    "top_left": {"M": {"x": {"N": "0.1"}, "y": {"N": "0.25"}}},
                    "bottom_right": {
                        "M": {"x": {"N": "0.15"}, "y": {"N": "0.2"}}
                    },
                    "bottom_left": {
                        "M": {"x": {"N": "0.1"}, "y": {"N": "0.2"}}
                    },
                    "angle_degrees": {"N": "0.0"},
                    "angle_radians": {"N": "0.0"},
                    "confidence": {"N": "0.98"},
                }
            ],
            "LastEvaluatedKey": {"key": "value"},
        },
        ClientError(
            {
                "Error": {
                    "Code": "ResourceNotFoundException",
                    "Message": "Table does not exist or is not active",
                }
            },
            "Query",
        ),
    ]
    client._client = mock_client

    with pytest.raises(
        Exception,
        match="Could not list receipt letters from DynamoDB: An error "
        "occurred",
    ):
        client.listReceiptLetters()

    assert mock_client.query.call_count == 2
    # Verify first call parameters
    first_call = mock_client.query.call_args_list[0]
    assert first_call.kwargs["TableName"] == dynamodb_table
    assert first_call.kwargs["IndexName"] == "GSITYPE"
    # Verify second call parameters
    second_call = mock_client.query.call_args_list[1]
    assert second_call.kwargs["TableName"] == dynamodb_table
    assert second_call.kwargs["IndexName"] == "GSITYPE"
    assert second_call.kwargs["ExclusiveStartKey"] == {"key": "value"}


@pytest.mark.integration
def test_listReceiptLetters_pagination_unexpected_error(
    dynamodb_table, mocker
):
    """Test handling of unexpected error during pagination in
    listReceiptLetters.

    Verifies that we get a partial result before the exception occurs.
    """
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.Mock()
    mock_client.query.side_effect = [
        {
            "Items": [
                {
                    "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
                    "SK": {
                        "S": "RECEIPT#00001#LINE#00001#WORD#00001#LETTER#00001"
                    },
                    "TYPE": {"S": "RECEIPT_LETTER"},
                    "text": {"S": "A"},
                    "bounding_box": {
                        "M": {
                            "x": {"N": "0.1"},
                            "y": {"N": "0.2"},
                            "width": {"N": "0.05"},
                            "height": {"N": "0.05"},
                        }
                    },
                    "top_right": {
                        "M": {"x": {"N": "0.15"}, "y": {"N": "0.25"}}
                    },
                    "top_left": {"M": {"x": {"N": "0.1"}, "y": {"N": "0.25"}}},
                    "bottom_right": {
                        "M": {"x": {"N": "0.15"}, "y": {"N": "0.2"}}
                    },
                    "bottom_left": {
                        "M": {"x": {"N": "0.1"}, "y": {"N": "0.2"}}
                    },
                    "angle_degrees": {"N": "0.0"},
                    "angle_radians": {"N": "0.0"},
                    "confidence": {"N": "0.98"},
                }
            ],
            "LastEvaluatedKey": {"key": "value"},
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
    client._client = mock_client

    with pytest.raises(
        Exception, match="Error listing receipt letters: An error occurred"
    ):
        client.listReceiptLetters()

    assert mock_client.query.call_count == 2
    # Verify first call parameters
    first_call = mock_client.query.call_args_list[0]
    assert first_call.kwargs["TableName"] == dynamodb_table
    assert first_call.kwargs["IndexName"] == "GSITYPE"
    # Verify second call parameters
    second_call = mock_client.query.call_args_list[1]
    assert second_call.kwargs["TableName"] == dynamodb_table
    assert second_call.kwargs["IndexName"] == "GSITYPE"
    assert second_call.kwargs["ExclusiveStartKey"] == {"key": "value"}


@pytest.mark.integration
def test_listReceiptLettersFromWord_validation_error(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test ValidationException in first query of
    listReceiptLettersFromWord."""
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.Mock()
    mock_client.query.side_effect = ClientError(
        {
            "Error": {
                "Code": "ValidationException",
                "Message": "Invalid query parameters",
            }
        },
        "Query",
    )
    client._client = mock_client

    with pytest.raises(
        Exception, match="One or more parameters given were invalid"
    ):
        client.listReceiptLettersFromWord(
            receipt_id=sample_receipt_letter.receipt_id,
            image_id=sample_receipt_letter.image_id,
            line_id=sample_receipt_letter.line_id,
            word_id=sample_receipt_letter.word_id,
        )

    mock_client.query.assert_called_once_with(
        TableName=dynamodb_table,
        KeyConditionExpression="PK = :pkVal AND begins_with(SK, :skPrefix)",
        ExpressionAttributeValues={
            ":pkVal": {"S": f"IMAGE#{sample_receipt_letter.image_id}"},
            ":skPrefix": {
                "S": (
                    f"RECEIPT#{sample_receipt_letter.receipt_id:05d}"
                    f"#LINE#{sample_receipt_letter.line_id:05d}"
                    f"#WORD#{sample_receipt_letter.word_id:05d}"
                    f"#LETTER#"
                )
            },
        },
    )


@pytest.mark.integration
def test_listReceiptLettersFromWord_internal_server_error(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test InternalServerError in first query of
    listReceiptLettersFromWord."""
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.Mock()
    mock_client.query.side_effect = ClientError(
        {
            "Error": {
                "Code": "InternalServerError",
                "Message": "Internal server error occurred",
            }
        },
        "Query",
    )
    client._client = mock_client

    with pytest.raises(Exception, match="Internal server error"):
        client.listReceiptLettersFromWord(
            receipt_id=sample_receipt_letter.receipt_id,
            image_id=sample_receipt_letter.image_id,
            line_id=sample_receipt_letter.line_id,
            word_id=sample_receipt_letter.word_id,
        )


@pytest.mark.integration
def test_listReceiptLettersFromWord_pagination_access_denied(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test AccessDeniedException during pagination."""
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.Mock()
    mock_client.query.side_effect = [
        {
            "Items": [
                {
                    "PK": {"S": f"IMAGE#{sample_receipt_letter.image_id}"},
                    "SK": {
                        "S": f"RECEIPT#{sample_receipt_letter.receipt_id:05d}#"
                        f"LINE#{sample_receipt_letter.line_id:05d}#"
                        f"WORD#{sample_receipt_letter.word_id:05d}#"
                        f"LETTER#00001"
                    },
                    "TYPE": {"S": "RECEIPT_LETTER"},
                    "text": {"S": "A"},
                    "bounding_box": {
                        "M": {
                            "x": {"N": "0.1"},
                            "y": {"N": "0.2"},
                            "width": {"N": "0.05"},
                            "height": {"N": "0.05"},
                        }
                    },
                    "top_right": {
                        "M": {"x": {"N": "0.15"}, "y": {"N": "0.25"}}
                    },
                    "top_left": {"M": {"x": {"N": "0.1"}, "y": {"N": "0.25"}}},
                    "bottom_right": {
                        "M": {"x": {"N": "0.15"}, "y": {"N": "0.2"}}
                    },
                    "bottom_left": {
                        "M": {"x": {"N": "0.1"}, "y": {"N": "0.2"}}
                    },
                    "angle_degrees": {"N": "0.0"},
                    "angle_radians": {"N": "0.0"},
                    "confidence": {"N": "0.98"},
                }
            ],
            "LastEvaluatedKey": {"key": "value"},
        },
        ClientError(
            {
                "Error": {
                    "Code": "AccessDeniedException",
                    "Message": "User is not authorized to perform this "
                    "operation",
                }
            },
            "Query",
        ),
    ]
    client._client = mock_client

    with pytest.raises(Exception, match="Access denied"):
        client.listReceiptLettersFromWord(
            receipt_id=sample_receipt_letter.receipt_id,
            image_id=sample_receipt_letter.image_id,
            line_id=sample_receipt_letter.line_id,
            word_id=sample_receipt_letter.word_id,
        )


@pytest.mark.integration
def test_listReceiptLettersFromWord_pagination_validation(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test ValidationException during pagination."""
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.Mock()
    mock_client.query.side_effect = [
        {
            "Items": [
                {
                    "PK": {"S": f"IMAGE#{sample_receipt_letter.image_id}"},
                    "SK": {
                        "S": f"RECEIPT#{sample_receipt_letter.receipt_id:05d}#"
                        f"LINE#{sample_receipt_letter.line_id:05d}#"
                        f"WORD#{sample_receipt_letter.word_id:05d}#"
                        f"LETTER#00001"
                    },
                    "TYPE": {"S": "RECEIPT_LETTER"},
                    "text": {"S": "A"},
                    "bounding_box": {
                        "M": {
                            "x": {"N": "0.1"},
                            "y": {"N": "0.2"},
                            "width": {"N": "0.05"},
                            "height": {"N": "0.05"},
                        }
                    },
                    "top_right": {
                        "M": {"x": {"N": "0.15"}, "y": {"N": "0.25"}}
                    },
                    "top_left": {"M": {"x": {"N": "0.1"}, "y": {"N": "0.25"}}},
                    "bottom_right": {
                        "M": {"x": {"N": "0.15"}, "y": {"N": "0.2"}}
                    },
                    "bottom_left": {
                        "M": {"x": {"N": "0.1"}, "y": {"N": "0.2"}}
                    },
                    "angle_degrees": {"N": "0.0"},
                    "angle_radians": {"N": "0.0"},
                    "confidence": {"N": "0.98"},
                }
            ],
            "LastEvaluatedKey": {"key": "value"},
        },
        ClientError(
            {
                "Error": {
                    "Code": "ValidationException",
                    "Message": "Invalid parameter in pagination request",
                }
            },
            "Query",
        ),
    ]
    client._client = mock_client

    with pytest.raises(
        Exception, match="One or more parameters given were invalid"
    ):
        client.listReceiptLettersFromWord(
            receipt_id=sample_receipt_letter.receipt_id,
            image_id=sample_receipt_letter.image_id,
            line_id=sample_receipt_letter.line_id,
            word_id=sample_receipt_letter.word_id,
        )


@pytest.mark.integration
def test_listReceiptLettersFromWord_pagination_internal_server_error(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test InternalServerError during pagination."""
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.Mock()
    mock_client.query.side_effect = [
        {
            "Items": [
                {
                    "PK": {"S": f"IMAGE#{sample_receipt_letter.image_id}"},
                    "SK": {
                        "S": f"RECEIPT#{sample_receipt_letter.receipt_id:05d}#"
                        f"LINE#{sample_receipt_letter.line_id:05d}#"
                        f"WORD#{sample_receipt_letter.word_id:05d}#"
                        f"LETTER#00001"
                    },
                    "TYPE": {"S": "RECEIPT_LETTER"},
                    "text": {"S": "A"},
                    "bounding_box": {
                        "M": {
                            "x": {"N": "0.1"},
                            "y": {"N": "0.2"},
                            "width": {"N": "0.05"},
                            "height": {"N": "0.05"},
                        }
                    },
                    "top_right": {
                        "M": {"x": {"N": "0.15"}, "y": {"N": "0.25"}}
                    },
                    "top_left": {"M": {"x": {"N": "0.1"}, "y": {"N": "0.25"}}},
                    "bottom_right": {
                        "M": {"x": {"N": "0.15"}, "y": {"N": "0.2"}}
                    },
                    "bottom_left": {
                        "M": {"x": {"N": "0.1"}, "y": {"N": "0.2"}}
                    },
                    "angle_degrees": {"N": "0.0"},
                    "angle_radians": {"N": "0.0"},
                    "confidence": {"N": "0.98"},
                }
            ],
            "LastEvaluatedKey": {"key": "value"},
        },
        ClientError(
            {
                "Error": {
                    "Code": "InternalServerError",
                    "Message": "Internal server error during pagination",
                }
            },
            "Query",
        ),
    ]
    client._client = mock_client

    with pytest.raises(Exception, match="Internal server error"):
        client.listReceiptLettersFromWord(
            receipt_id=sample_receipt_letter.receipt_id,
            image_id=sample_receipt_letter.image_id,
            line_id=sample_receipt_letter.line_id,
            word_id=sample_receipt_letter.word_id,
        )


@pytest.mark.integration
def test_listReceiptLettersFromWord_pagination_throughput_exceeded(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test ProvisionedThroughputExceededException during pagination."""
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.Mock()
    mock_client.query.side_effect = [
        {
            "Items": [
                {
                    "PK": {"S": f"IMAGE#{sample_receipt_letter.image_id}"},
                    "SK": {
                        "S": f"RECEIPT#{sample_receipt_letter.receipt_id:05d}#"
                        f"LINE#{sample_receipt_letter.line_id:05d}#"
                        f"WORD#{sample_receipt_letter.word_id:05d}#"
                        f"LETTER#00001"
                    },
                    "TYPE": {"S": "RECEIPT_LETTER"},
                    "text": {"S": "A"},
                    "bounding_box": {
                        "M": {
                            "x": {"N": "0.1"},
                            "y": {"N": "0.2"},
                            "width": {"N": "0.05"},
                            "height": {"N": "0.05"},
                        }
                    },
                    "top_right": {
                        "M": {"x": {"N": "0.15"}, "y": {"N": "0.25"}}
                    },
                    "top_left": {"M": {"x": {"N": "0.1"}, "y": {"N": "0.25"}}},
                    "bottom_right": {
                        "M": {"x": {"N": "0.15"}, "y": {"N": "0.2"}}
                    },
                    "bottom_left": {
                        "M": {"x": {"N": "0.1"}, "y": {"N": "0.2"}}
                    },
                    "angle_degrees": {"N": "0.0"},
                    "angle_radians": {"N": "0.0"},
                    "confidence": {"N": "0.98"},
                }
            ],
            "LastEvaluatedKey": {"key": "value"},
        },
        ClientError(
            {
                "Error": {
                    "Code": "ProvisionedThroughputExceededException",
                    "Message": "Throughput exceeded during pagination",
                }
            },
            "Query",
        ),
    ]
    client._client = mock_client

    with pytest.raises(Exception, match="Provisioned throughput exceeded"):
        client.listReceiptLettersFromWord(
            receipt_id=sample_receipt_letter.receipt_id,
            image_id=sample_receipt_letter.image_id,
            line_id=sample_receipt_letter.line_id,
            word_id=sample_receipt_letter.word_id,
        )


@pytest.mark.integration
def test_listReceiptLettersFromWord_second_query_validation_error(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test ValidationException in second query of
    listReceiptLettersFromWord."""
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.Mock()
    mock_client.query.side_effect = [
        {
            "Items": [
                {
                    "PK": {"S": f"IMAGE#{sample_receipt_letter.image_id}"},
                    "SK": {
                        "S": f"RECEIPT#{sample_receipt_letter.receipt_id:05d}#"
                        f"LINE#{sample_receipt_letter.line_id:05d}#"
                        f"WORD#{sample_receipt_letter.word_id:05d}#"
                        f"LETTER#00001"
                    },
                    "TYPE": {"S": "RECEIPT_LETTER"},
                    "text": {"S": "A"},
                    "bounding_box": {
                        "M": {
                            "x": {"N": "0.1"},
                            "y": {"N": "0.2"},
                            "width": {"N": "0.05"},
                            "height": {"N": "0.05"},
                        }
                    },
                    "top_right": {
                        "M": {"x": {"N": "0.15"}, "y": {"N": "0.25"}}
                    },
                    "top_left": {"M": {"x": {"N": "0.1"}, "y": {"N": "0.25"}}},
                    "bottom_right": {
                        "M": {"x": {"N": "0.15"}, "y": {"N": "0.2"}}
                    },
                    "bottom_left": {
                        "M": {"x": {"N": "0.1"}, "y": {"N": "0.2"}}
                    },
                    "angle_degrees": {"N": "0.0"},
                    "angle_radians": {"N": "0.0"},
                    "confidence": {"N": "0.98"},
                }
            ],
            "LastEvaluatedKey": {"key": "value"},
        },
        ClientError(
            {
                "Error": {
                    "Code": "ValidationException",
                    "Message": "Invalid parameter in second query",
                }
            },
            "Query",
        ),
    ]
    client._client = mock_client

    with pytest.raises(
        Exception, match="One or more parameters given were invalid"
    ):
        client.listReceiptLettersFromWord(
            receipt_id=sample_receipt_letter.receipt_id,
            image_id=sample_receipt_letter.image_id,
            line_id=sample_receipt_letter.line_id,
            word_id=sample_receipt_letter.word_id,
        )


@pytest.mark.integration
def test_listReceiptLettersFromWord_second_query_internal_server_error(
    dynamodb_table, sample_receipt_letter, mocker
):
    """Test InternalServerError in second query of
    listReceiptLettersFromWord."""
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.Mock()
    mock_client.query.side_effect = [
        {
            "Items": [
                {
                    "PK": {"S": f"IMAGE#{sample_receipt_letter.image_id}"},
                    "SK": {
                        "S": f"RECEIPT#{sample_receipt_letter.receipt_id:05d}#"
                        f"LINE#{sample_receipt_letter.line_id:05d}#"
                        f"WORD#{sample_receipt_letter.word_id:05d}#"
                        f"LETTER#00001"
                    },
                    "TYPE": {"S": "RECEIPT_LETTER"},
                    "text": {"S": "A"},
                    "bounding_box": {
                        "M": {
                            "x": {"N": "0.1"},
                            "y": {"N": "0.2"},
                            "width": {"N": "0.05"},
                            "height": {"N": "0.05"},
                        }
                    },
                    "top_right": {
                        "M": {"x": {"N": "0.15"}, "y": {"N": "0.25"}}
                    },
                    "top_left": {"M": {"x": {"N": "0.1"}, "y": {"N": "0.25"}}},
                    "bottom_right": {
                        "M": {"x": {"N": "0.15"}, "y": {"N": "0.2"}}
                    },
                    "bottom_left": {
                        "M": {"x": {"N": "0.1"}, "y": {"N": "0.2"}}
                    },
                    "angle_degrees": {"N": "0.0"},
                    "angle_radians": {"N": "0.0"},
                    "confidence": {"N": "0.98"},
                }
            ],
            "LastEvaluatedKey": {"key": "value"},
        },
        ClientError(
            {
                "Error": {
                    "Code": "InternalServerError",
                    "Message": "Internal server error in second query",
                }
            },
            "Query",
        ),
    ]
    client._client = mock_client

    with pytest.raises(Exception, match="Internal server error"):
        client.listReceiptLettersFromWord(
            receipt_id=sample_receipt_letter.receipt_id,
            image_id=sample_receipt_letter.image_id,
            line_id=sample_receipt_letter.line_id,
            word_id=sample_receipt_letter.word_id,
        )


@pytest.mark.integration
def test_listReceiptLetters_pagination_resource_not_found_second_query(
    dynamodb_table, mocker
):
    """Test handling of ResourceNotFoundException during pagination in
    listReceiptLetters.

    Verifies that we get a partial result before the exception occurs.
    """
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.Mock()

    # First query succeeds and returns a page with LastEvaluatedKey
    first_response = {
        "Items": [
            {
                "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
                "SK": {
                    "S": "RECEIPT#00001#LINE#00001#WORD#00001#LETTER#00001"
                },
                "TYPE": {"S": "RECEIPT_LETTER"},
                "text": {"S": "A"},
                "bounding_box": {
                    "M": {
                        "x": {"N": "0.1"},
                        "y": {"N": "0.2"},
                        "width": {"N": "0.05"},
                        "height": {"N": "0.05"},
                    }
                },
                "top_right": {"M": {"x": {"N": "0.15"}, "y": {"N": "0.25"}}},
                "top_left": {"M": {"x": {"N": "0.1"}, "y": {"N": "0.25"}}},
                "bottom_right": {"M": {"x": {"N": "0.15"}, "y": {"N": "0.2"}}},
                "bottom_left": {"M": {"x": {"N": "0.1"}, "y": {"N": "0.2"}}},
                "angle_degrees": {"N": "0.0"},
                "angle_radians": {"N": "0.0"},
                "confidence": {"N": "0.98"},
            }
        ],
        "LastEvaluatedKey": {"key": "value"},
    }

    # Second query fails with ResourceNotFoundException
    mock_client.query.side_effect = [
        first_response,
        ClientError(
            {
                "Error": {
                    "Code": "ResourceNotFoundException",
                    "Message": "Table not found during pagination",
                }
            },
            "Query",
        ),
    ]
    client._client = mock_client

    with pytest.raises(
        Exception, match="Could not list receipt letters from DynamoDB"
    ):
        letters, last_key = client.listReceiptLetters()

    # Verify the second query was made with the correct parameters
    assert mock_client.query.call_count == 2
    second_call = mock_client.query.call_args_list[1]
    assert second_call.kwargs["TableName"] == dynamodb_table
    assert second_call.kwargs["IndexName"] == "GSITYPE"
    assert second_call.kwargs["ExclusiveStartKey"] == {"key": "value"}


@pytest.mark.integration
def test_listReceiptLetters_multiple_pages(dynamodb_table, mocker):
    """Test successful pagination through multiple pages in
    listReceiptLetters."""
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.Mock()

    # Set up mock to return two pages of results
    mock_client.query.side_effect = [
        {
            "Items": [
                {
                    "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
                    "SK": {
                        "S": "RECEIPT#00001#LINE#00001#WORD#00001#LETTER#00001"
                    },
                    "TYPE": {"S": "RECEIPT_LETTER"},
                    "text": {"S": "A"},
                    "bounding_box": {
                        "M": {
                            "x": {"N": "0.1"},
                            "y": {"N": "0.2"},
                            "width": {"N": "0.05"},
                            "height": {"N": "0.05"},
                        }
                    },
                    "top_right": {
                        "M": {"x": {"N": "0.15"}, "y": {"N": "0.25"}}
                    },
                    "top_left": {"M": {"x": {"N": "0.1"}, "y": {"N": "0.25"}}},
                    "bottom_right": {
                        "M": {"x": {"N": "0.15"}, "y": {"N": "0.2"}}
                    },
                    "bottom_left": {
                        "M": {"x": {"N": "0.1"}, "y": {"N": "0.2"}}
                    },
                    "angle_degrees": {"N": "0.0"},
                    "angle_radians": {"N": "0.0"},
                    "confidence": {"N": "0.98"},
                }
            ],
            "LastEvaluatedKey": {"key": "value"},
        },
        {
            "Items": [
                {
                    "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
                    "SK": {
                        "S": "RECEIPT#00001#LINE#00001#WORD#00001#LETTER#00002"
                    },
                    "TYPE": {"S": "RECEIPT_LETTER"},
                    "text": {"S": "B"},
                    "bounding_box": {
                        "M": {
                            "x": {"N": "0.2"},
                            "y": {"N": "0.2"},
                            "width": {"N": "0.05"},
                            "height": {"N": "0.05"},
                        }
                    },
                    "top_right": {
                        "M": {"x": {"N": "0.25"}, "y": {"N": "0.25"}}
                    },
                    "top_left": {"M": {"x": {"N": "0.2"}, "y": {"N": "0.25"}}},
                    "bottom_right": {
                        "M": {"x": {"N": "0.25"}, "y": {"N": "0.2"}}
                    },
                    "bottom_left": {
                        "M": {"x": {"N": "0.2"}, "y": {"N": "0.2"}}
                    },
                    "angle_degrees": {"N": "0.0"},
                    "angle_radians": {"N": "0.0"},
                    "confidence": {"N": "0.98"},
                }
            ]
        },
    ]
    client._client = mock_client

    # Call listReceiptLetters with no limit to force pagination
    letters, last_key = client.listReceiptLetters()

    # Verify we got both pages of results
    assert len(letters) == 2
    assert letters[0].text == "A"
    assert letters[1].text == "B"
    assert last_key is None

    # Verify both queries were made
    assert mock_client.query.call_count == 2
    first_call = mock_client.query.call_args_list[0]
    second_call = mock_client.query.call_args_list[1]

    # Verify first call parameters
    assert first_call.kwargs["TableName"] == dynamodb_table
    assert first_call.kwargs["IndexName"] == "GSITYPE"

    # Verify second call parameters
    assert second_call.kwargs["TableName"] == dynamodb_table
    assert second_call.kwargs["IndexName"] == "GSITYPE"
    assert second_call.kwargs["ExclusiveStartKey"] == {"key": "value"}


@pytest.mark.integration
def test_listReceiptLetters_raises_internal_server_error_first_query(
    dynamodb_table, mocker
):
    """Test handling of InternalServerError in listReceiptLetters first
    query."""
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.patch.object(
        client._client,
        "query",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "InternalServerError",
                    "Message": "Internal server error",
                }
            },
            "Query",
        ),
    )
    with pytest.raises(Exception, match="Internal server error"):
        client.listReceiptLetters()
    mock_client.assert_called_once()


@pytest.mark.integration
def test_listReceiptLetters_raises_validation_error_first_query(
    dynamodb_table, mocker
):
    """Test handling of ValidationException in listReceiptLetters first
    query."""
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.patch.object(
        client._client,
        "query",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ValidationException",
                    "Message": "Invalid parameters",
                }
            },
            "Query",
        ),
    )
    with pytest.raises(
        ValueError, match="One or more parameters given were invalid"
    ):
        client.listReceiptLetters()
    mock_client.assert_called_once()
