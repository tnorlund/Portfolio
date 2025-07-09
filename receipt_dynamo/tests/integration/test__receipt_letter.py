from typing import Any, Dict, List, Literal, Optional, Type

import pytest
from botocore.exceptions import ClientError, ParamValidationError

from receipt_dynamo import DynamoClient, ReceiptLetter
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBAccessError,
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    DynamoDBValidationError,
)

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
    client.add_receipt_letter(sample_receipt_letter)

    # Assert
    retrieved_letter = client.get_receipt_letter(
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
    client.add_receipt_letter(sample_receipt_letter)

    # Act & Assert
    with pytest.raises(ValueError, match="already exists"):
        client.add_receipt_letter(sample_receipt_letter)


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "Letter parameter is required and cannot be None."),
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
        client.add_receipt_letter(invalid_input)  # type: ignore


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
            "Table not found for operation add_receipt_letter",
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
            "Unknown error in add_receipt_letter",
        ),
        (
            "ValidationException",
            "One or more parameters were invalid",
            "Validation error in add_receipt_letter",
        ),
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied for add_receipt_letter",
        ),
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
        client.add_receipt_letter(sample_receipt_letter)
    mock_put.assert_called_once()


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
    client.add_receipt_letters(letters)

    # Assert
    retrieved_letter = client.get_receipt_letter(
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
    client.add_receipt_letters(letters)

    # Verify all letters were added
    for letter in letters:
        retrieved_letter = client.get_receipt_letter(
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
    client.add_receipt_letters([sample_receipt_letter])

    # Verify both calls were made
    assert mock_batch.call_count == 2


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "Letters parameter is required and cannot be None."),
        ("not-a-list", "Letters must be provided as a list."),
        (
            ["not-a-receipt-letter"],
            "All items in the letters list must be instances of the ReceiptLetter class.",
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
        client.add_receipt_letters(invalid_input)  # type: ignore


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error_message",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found for operation add_receipt_letters",
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
            "Validation error in add_receipt_letters",
        ),
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied for add_receipt_letters",
        ),
        (
            "UnknownError",
            "Unknown error occurred",
            "Unknown error in add_receipt_letters",
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
    """Test addReceiptLetters handles various DynamoDB client errors
    appropriately."""
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
        client.add_receipt_letters([sample_receipt_letter])
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
    client.add_receipt_letter(sample_receipt_letter)

    # Change the text
    sample_receipt_letter.text = "Z"
    client.update_receipt_letter(sample_receipt_letter)

    # Assert
    retrieved_letter = client.get_receipt_letter(
        sample_receipt_letter.receipt_id,
        sample_receipt_letter.image_id,
        sample_receipt_letter.line_id,
        sample_receipt_letter.word_id,
        sample_receipt_letter.letter_id,
    )
    assert retrieved_letter.text == "Z"


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "Letter parameter is required and cannot be None."),
        (
            "not a ReceiptLetter",
            "letter must be an instance of the ReceiptLetter class.",
        ),
    ],
)
def test_updateReceiptLetter_invalid_parameters(
    dynamodb_table,
    sample_receipt_letter,
    mocker,
    invalid_input,
    expected_error,
):
    """Test handling of ValueError in updateReceiptLetter with invalid
    parameters."""
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match=expected_error):
        client.update_receipt_letter(invalid_input)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error",
    [
        (
            "ConditionalCheckFailedException",
            "Item does not exist",
            "Entity does not exist",
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
            "Table not found for operation update_receipt_letter",
        ),
        (
            "ValidationException",
            "One or more parameters were invalid",
            "Validation error in update_receipt_letter",
        ),
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied for update_receipt_letter",
        ),
        (
            "UnknownError",
            "Unknown error occurred",
            "Unknown error in update_receipt_letter",
        ),
    ],
)
def test_updateReceiptLetter_client_errors(
    dynamodb_table,
    sample_receipt_letter,
    mocker,
    error_code,
    error_message,
    expected_error,
):
    """Test handling of various client errors in updateReceiptLetter."""
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

    with pytest.raises(
        (
            Exception
            if error_code != "ConditionalCheckFailedException"
            else ValueError
        ),
        match=expected_error,
    ):
        client.update_receipt_letter(sample_receipt_letter)
    mock_put.assert_called_once()


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
        client.add_receipt_letter(lt)

    # Modify the letters
    for lt in letters:
        lt.text = "Z"

    # Act
    client.update_receipt_letters(letters)

    # Assert
    for lt in letters:
        retrieved_letter = client.get_receipt_letter(
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
        client.add_receipt_letter(letter)

    # Update all letters
    for letter in letters:
        letter.text = "Z"  # Single character update

    # Should complete successfully
    client.update_receipt_letters(letters)

    # Verify all letters were updated
    for letter in letters:
        retrieved_letter = client.get_receipt_letter(
            letter.receipt_id,
            letter.image_id,
            letter.line_id,
            letter.word_id,
            letter.letter_id,
        )
        assert retrieved_letter.text == "Z"


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "Letters parameter is required and cannot be None"),
        ("not-a-list", "Letters must be provided as a list"),
        (
            [123, "not-a-receipt-letter"],
            "All items in the letters list must be instances of the ReceiptLetter class",
        ),
    ],
)
def test_updateReceiptLetters_invalid_inputs(
    dynamodb_table,
    sample_receipt_letter,
    mocker,
    invalid_input,
    expected_error,
):
    """Test updateReceiptLetters with various invalid inputs.

    Args:
        dynamodb_table: Mock DynamoDB table fixture
        sample_receipt_letter: Sample ReceiptLetter fixture
        mocker: pytest mocker fixture
        invalid_input: The invalid input to test
        expected_error: Expected error message

    This test verifies that updateReceiptLetters properly validates its input
    and raises appropriate ValueError exceptions for:
    - None input
    - Non-list input
    - List with invalid types
    """
    client = DynamoClient(dynamodb_table)

    with pytest.raises(ValueError, match=expected_error):
        client.update_receipt_letters(invalid_input)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error,expected_exception,cancellation_reasons",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found for operation update_receipt_letters",
            DynamoDBError,
            None,
        ),
        (
            "TransactionCanceledException",
            "Transaction canceled due to ConditionalCheckFailed",
            "One or more entities do not exist or conditions failed",
            ValueError,
            [{"Code": "ConditionalCheckFailed"}],
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
            DynamoDBServerError,
            None,
        ),
        (
            "ProvisionedThroughputExceededException",
            "Provisioned throughput exceeded",
            "Provisioned throughput exceeded",
            DynamoDBThroughputError,
            None,
        ),
        (
            "ValidationException",
            "One or more parameters were invalid",
            "Validation error in update_receipt_letters",
            DynamoDBValidationError,
            None,
        ),
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied for update_receipt_letters",
            DynamoDBAccessError,
            None,
        ),
        (
            "UnknownError",
            "Unknown error occurred",
            "Unknown error in update_receipt_letters",
            DynamoDBError,
            None,
        ),
    ],
)
def test_updateReceiptLetters_client_errors(
    dynamodb_table,
    sample_receipt_letter,
    mocker,
    error_code,
    error_message,
    expected_error,
    expected_exception,
    cancellation_reasons,
):
    """Test updateReceiptLetters handling of various DynamoDB client errors.

    Args:
        dynamodb_table: Mock DynamoDB table fixture
        sample_receipt_letter: Sample ReceiptLetter fixture
        mocker: pytest mocker fixture
        error_code: The DynamoDB error code to simulate
        error_message: The error message from DynamoDB
        expected_error: Expected error message in the raised exception
        cancellation_reasons: Optional cancellation reasons for
            TransactionCanceledException

    This test verifies that updateReceiptLetters properly handles various
    DynamoDB client errors including:
    - ResourceNotFoundException
    - TransactionCanceledException
    - InternalServerError
    - ProvisionedThroughputExceededException
    - ValidationException
    - AccessDeniedException
    - Unknown errors
    """
    client = DynamoClient(dynamodb_table)

    error_dict = {
        "Error": {
            "Code": error_code,
            "Message": error_message,
        }
    }

    # Add CancellationReasons for TransactionCanceledException
    if cancellation_reasons:
        error_dict["Error"]["CancellationReasons"] = cancellation_reasons

    mock_transact = mocker.patch.object(
        client._client,
        "transact_write_items",
        side_effect=ClientError(error_dict, "TransactWriteItems"),
    )

    with pytest.raises(expected_exception, match=expected_error):
        client.update_receipt_letters([sample_receipt_letter])

    mock_transact.assert_called_once()


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
    client.add_receipt_letter(sample_receipt_letter)

    # Act
    client.delete_receipt_letter(sample_receipt_letter)

    # Assert
    with pytest.raises(ValueError, match="not found"):
        client.get_receipt_letter(
            sample_receipt_letter.receipt_id,
            sample_receipt_letter.image_id,
            sample_receipt_letter.line_id,
            sample_receipt_letter.word_id,
            sample_receipt_letter.letter_id,
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "Letter parameter is required and cannot be None"),
        (
            "not-a-receipt-letter",
            "letter must be an instance of the ReceiptLetter class",
        ),
    ],
)
def test_deleteReceiptLetter_invalid_parameters(
    dynamodb_table,
    sample_receipt_letter,
    mocker,
    invalid_input,
    expected_error,
):
    """Test deleteReceiptLetter with invalid input parameters.

    Args:
        dynamodb_table: Mock DynamoDB table fixture
        sample_receipt_letter: Sample ReceiptLetter fixture
        mocker: pytest mocker fixture
        invalid_input: The invalid input to test
        expected_error: Expected error message

    This test verifies that deleteReceiptLetter properly validates its input
    parameters and raises appropriate ValueError exceptions for invalid
    inputs.
    """
    # Arrange
    dynamo_client = DynamoClient(dynamodb_table)

    # Act & Assert
    with pytest.raises(ValueError, match=expected_error):
        dynamo_client.delete_receipt_letter(invalid_input)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error",
    [
        (
            "ConditionalCheckFailedException",
            "Item does not exist",
            "Entity does not exist",
        ),
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found for operation delete_receipt_letter",
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
            "Validation error in delete_receipt_letter",
        ),
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied for delete_receipt_letter",
        ),
        (
            "UnknownError",
            "Unknown error occurred",
            "Unknown error in delete_receipt_letter",
        ),
    ],
)
def test_deleteReceiptLetter_client_errors(
    dynamodb_table,
    sample_receipt_letter,
    mocker,
    error_code,
    error_message,
    expected_error,
):
    """Test deleteReceiptLetter handling of various DynamoDB client errors.

    Args:
        dynamodb_table: Mock DynamoDB table fixture
        sample_receipt_letter: Sample ReceiptLetter fixture
        mocker: pytest mocker fixture
        error_code: The DynamoDB error code to simulate
        error_message: The error message from DynamoDB
        expected_error: Expected error message in the raised exception

    This test verifies that deleteReceiptLetter properly handles various
    DynamoDB client errors and raises appropriate exceptions with the expected
    error messages.
    """
    # Arrange
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

    # Act & Assert
    with pytest.raises(Exception, match=expected_error):
        client.delete_receipt_letter(sample_receipt_letter)
    mock_delete.assert_called_once()


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
        client.add_receipt_letter(lt)

    # Act
    client.delete_receipt_letters(letters)

    # Assert
    for lt in letters:
        with pytest.raises(ValueError, match="not found"):
            client.get_receipt_letter(
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
        client.add_receipt_letter(letter)

    # Delete all letters
    client.delete_receipt_letters(letters)

    # Verify all letters were deleted
    for letter in letters:
        with pytest.raises(ValueError, match="not found"):
            client.get_receipt_letter(
                letter.receipt_id,
                letter.image_id,
                letter.line_id,
                letter.word_id,
                letter.letter_id,
            )


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
                {"DeleteRequest": {"Key": sample_receipt_letter.key}}
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
    client.delete_receipt_letters([sample_receipt_letter])

    # Verify both calls were made
    assert mock_batch.call_count == 2


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "Letters parameter is required and cannot be None."),
        ("not a list", "Letters must be provided as a list."),
        (
            [1, 2, 3],
            "All items in the letters list must be instances of the ReceiptLetter class.",
        ),
    ],
)
def test_deleteReceiptLetters_invalid_parameters(
    dynamodb_table,
    mocker,
    invalid_input,
    expected_error,
):
    """Test deleteReceiptLetters with invalid input parameters.

    Args:
        dynamodb_table: Mock DynamoDB table fixture
        mocker: pytest mocker fixture
        invalid_input: The invalid input to test
        expected_error: Expected error message

    This test verifies that deleteReceiptLetters properly validates its input
    parameters and raises appropriate ValueError exceptions for invalid
    inputs:
    - None input
    - Non-list input
    - List with non-ReceiptLetter elements
    """
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act & Assert
    with pytest.raises(ValueError, match=expected_error):
        client.delete_receipt_letters(invalid_input)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found for operation delete_receipt_letters",
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
            "Validation error in delete_receipt_letters",
        ),
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied for delete_receipt_letters",
        ),
        (
            "UnknownError",
            "Unknown error occurred",
            "Unknown error in delete_receipt_letters",
        ),
    ],
)
def test_deleteReceiptLetters_client_errors(
    dynamodb_table,
    sample_receipt_letter,
    mocker,
    error_code,
    error_message,
    expected_error,
):
    """Test deleteReceiptLetters handling of various DynamoDB client errors.

    Args:
        dynamodb_table: Mock DynamoDB table fixture
        sample_receipt_letter: Sample ReceiptLetter fixture
        mocker: pytest mocker fixture
        error_code: The DynamoDB error code to simulate
        error_message: The error message from DynamoDB
        expected_error: Expected error message in the raised exception

    This test verifies that deleteReceiptLetters properly handles various
    DynamoDB client errors and raises appropriate exceptions with the expected
    error messages.
    """
    # Arrange
    client = DynamoClient(dynamodb_table)
    mock_batch = mocker.patch.object(
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

    # Act & Assert
    with pytest.raises(Exception, match=expected_error):
        client.delete_receipt_letters([sample_receipt_letter])
    mock_batch.assert_called_once()


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
    client.add_receipt_letter(sample_receipt_letter)

    # Act
    retrieved_letter = client.get_receipt_letter(
        sample_receipt_letter.receipt_id,
        sample_receipt_letter.image_id,
        sample_receipt_letter.line_id,
        sample_receipt_letter.word_id,
        sample_receipt_letter.letter_id,
    )

    # Assert
    assert retrieved_letter == sample_receipt_letter


@pytest.mark.integration
def test_getReceiptLetter_not_found(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_letter: ReceiptLetter,
):
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act & Assert
    with pytest.raises(ValueError, match="not found"):
        client.get_receipt_letter(
            sample_receipt_letter.receipt_id,
            sample_receipt_letter.image_id,
            sample_receipt_letter.line_id,
            sample_receipt_letter.word_id,
            sample_receipt_letter.letter_id,
        )


@pytest.mark.integration
@pytest.mark.parametrize(
    "param_name,invalid_value,expected_error,sample_override",
    [
        # None value tests
        (
            "receipt_id",
            None,
            "receipt_id parameter is required and cannot be None.",
            {},
        ),
        (
            "image_id",
            None,
            "image_id parameter is required and cannot be None.",
            {},
        ),
        (
            "line_id",
            None,
            "line_id parameter is required and cannot be None.",
            {},
        ),
        (
            "word_id",
            None,
            "word_id parameter is required and cannot be None.",
            {},
        ),
        (
            "letter_id",
            None,
            "letter_id parameter is required and cannot be None.",
            {},
        ),
        # Invalid type tests
        ("receipt_id", "not-an-integer", "receipt_id must be an integer", {}),
        ("image_id", "invalid-uuid", "uuid must be a valid UUID", {}),
        ("line_id", "not-an-integer", "line_id must be an integer", {}),
        ("word_id", "not-an-integer", "word_id must be an integer", {}),
        ("letter_id", "not-an-integer", "letter_id must be an integer", {}),
    ],
)
def test_getReceiptLetter_invalid_parameters(
    dynamodb_table,
    sample_receipt_letter,
    mocker,
    param_name,
    invalid_value,
    expected_error,
    sample_override,
):
    """Test getReceiptLetter with invalid input parameters.

    Args:
        dynamodb_table: Mock DynamoDB table fixture
        sample_receipt_letter: Sample ReceiptLetter fixture
        mocker: pytest mocker fixture
        param_name: Name of the parameter to test
        invalid_value: The invalid value to test
        expected_error: Expected error message
        sample_override: Dictionary of values to override in the sample
            parameters

    This test verifies that getReceiptLetter properly validates its input
    parameters and raises appropriate ValueError exceptions for:
    - None values for required parameters
    - Invalid types (non-integers for IDs, invalid UUID for image_id)
    """
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Prepare parameters with the invalid value
    params = {
        "receipt_id": sample_receipt_letter.receipt_id,
        "image_id": sample_receipt_letter.image_id,
        "line_id": sample_receipt_letter.line_id,
        "word_id": sample_receipt_letter.word_id,
        "letter_id": sample_receipt_letter.letter_id,
    }
    params.update(sample_override)  # Apply any overrides
    params[param_name] = invalid_value  # Insert the invalid value

    # Act & Assert
    with pytest.raises(ValueError, match=expected_error):
        client.get_receipt_letter(**params)


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error",
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
            "Validation error:",
        ),
        ("AccessDeniedException", "Access denied", "Access denied"),
        (
            "ResourceNotFoundException",
            "Table not found",
            "Error getting receipt letter:",
        ),
    ],
)
def test_getReceiptLetter_client_errors(
    dynamodb_table,
    sample_receipt_letter,
    mocker,
    error_code,
    error_message,
    expected_error,
):
    """Test getReceiptLetter handling of various DynamoDB client errors.

    Args:
        dynamodb_table: Mock DynamoDB table fixture
        sample_receipt_letter: Sample ReceiptLetter fixture
        mocker: pytest mocker fixture
        error_code: The DynamoDB error code to simulate
        error_message: The error message from DynamoDB
        expected_error: Expected error message in the raised exception

    This test verifies that getReceiptLetter properly handles various DynamoDB
    client errors and raises appropriate exceptions with the expected error
    messages.
    """
    # Arrange
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.patch.object(client, "_client")
    mock_client.get_item.side_effect = ClientError(
        {
            "Error": {
                "Code": error_code,
                "Message": error_message,
            }
        },
        "GetItem",
    )

    # Act & Assert
    with pytest.raises(Exception, match=expected_error):
        client.get_receipt_letter(
            receipt_id=sample_receipt_letter.receipt_id,
            image_id=sample_receipt_letter.image_id,
            line_id=sample_receipt_letter.line_id,
            word_id=sample_receipt_letter.word_id,
            letter_id=sample_receipt_letter.letter_id,
        )
    mock_client.get_item.assert_called_once()


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
        client.add_receipt_letter(lt)

    # Act
    returned_letters, _ = client.list_receipt_letters()

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
        client.add_receipt_letter(lt)

    # Act - Get first 2 letters
    returned_letters, last_key = client.list_receipt_letters(limit=2)

    # Assert
    assert len(returned_letters) == 2
    assert (
        last_key is not None
    )  # Should have a last evaluated key for pagination
    for lt in returned_letters:
        assert lt in letters

    # Act - Get next batch using last_key
    next_letters, next_last_key = client.list_receipt_letters(
        limit=2, last_evaluated_key=last_key
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
        client.add_receipt_letter(lt)

    # Act
    returned_letters, _ = client.list_receipt_letters()

    # Assert
    assert len(returned_letters) == 9
    for lt in letters:
        assert lt in returned_letters


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
    letters, last_key = client.list_receipt_letters()

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
@pytest.mark.parametrize(
    "param_name,invalid_value,expected_error,expected_exception",
    [
        ("limit", "invalid", "limit must be an integer or None", ValueError),
        (
            "last_evaluated_key",
            "invalid",
            "last_evaluated_key must be a dictionary or None",
            ValueError,
        ),
        ("limit", -1, "Parameter validation failed", ParamValidationError),
        ("limit", 0, "Parameter validation failed", ParamValidationError),
        ("limit", 1.5, "limit must be an integer or None", ValueError),
        (
            "last_evaluated_key",
            [],
            "last_evaluated_key must be a dictionary or None",
            ValueError,
        ),
        (
            "last_evaluated_key",
            123,
            "last_evaluated_key must be a dictionary or None",
            ValueError,
        ),
    ],
)
def test_listReceiptLetters_invalid_parameters(
    dynamodb_table: Literal["MyMockedTable"],
    param_name: str,
    invalid_value: Any,
    expected_error: str,
    expected_exception: Type[Exception],
):
    """Test that listReceiptLetters raises appropriate errors for invalid
    parameters:
    - When limit is not an integer or None
    - When limit is negative or zero (AWS SDK validation)
    - When last_evaluated_key is not a dictionary or None
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(expected_exception, match=expected_error):
        if param_name == "limit":
            client.list_receipt_letters(limit=invalid_value)  # type: ignore
        else:
            client.list_receipt_letters(
                last_evaluated_key=invalid_value,  # type: ignore
            )


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error,cancellation_reasons",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Could not list receipt letters from DynamoDB",
            None,
        ),
        (
            "ValidationException",
            "One or more parameters were invalid",
            "One or more parameters given were invalid",
            None,
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
            None,
        ),
        (
            "ProvisionedThroughputExceededException",
            "Throughput exceeded",
            "Provisioned throughput exceeded",
            None,
        ),
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied",
            None,
        ),
        (
            "UnknownError",
            "Unknown error occurred",
            "Error listing receipt letters",
            None,
        ),
    ],
)
def test_listReceiptLetters_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    mocker,
    error_code: str,
    error_message: str,
    expected_error: str,
    cancellation_reasons: Optional[List[Dict[str, str]]],
):
    """Test that listReceiptLetters handles various DynamoDB client errors
    appropriately:
    - ResourceNotFoundException
    - ValidationException
    - InternalServerError
    - ProvisionedThroughputExceededException
    - AccessDeniedException
    - UnknownError
    """
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.patch.object(
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
    with pytest.raises(Exception, match=expected_error):
        client.list_receipt_letters()
    mock_client.assert_called_once()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error,query_number",
    [
        (
            "ResourceNotFoundException",
            "Resource not found",
            "Could not list receipt letters from DynamoDB",
            1,  # First query fails
        ),
        (
            "ResourceNotFoundException",
            "Table not found during pagination",
            "Could not list receipt letters from DynamoDB",
            2,  # Second query fails
        ),
        (
            "UnknownError",
            "An unexpected error occurred",
            "Error listing receipt letters",
            2,  # Second query fails with unknown error
        ),
    ],
)
def test_listReceiptLetters_pagination_errors(
    dynamodb_table: Literal["MyMockedTable"],
    mocker,
    error_code: str,
    error_message: str,
    expected_error: str,
    query_number: int,
):
    """Test handling of various errors during pagination in listReceiptLetters.
    Tests both first query failures and subsequent query failures.
    """
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.Mock()

    # Successful first query response
    success_response = {
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

    # Set up mock responses based on which query should fail
    if query_number == 1:
        mock_client.query.side_effect = ClientError(
            {
                "Error": {
                    "Code": error_code,
                    "Message": error_message,
                }
            },
            "Query",
        )
    else:
        mock_client.query.side_effect = [
            success_response,
            ClientError(
                {
                    "Error": {
                        "Code": error_code,
                        "Message": error_message,
                    }
                },
                "Query",
            ),
        ]

    client._client = mock_client

    with pytest.raises(Exception, match=expected_error):
        client.list_receipt_letters()

    # Verify the correct number of calls were made
    assert mock_client.query.call_count == query_number

    # Verify call parameters
    calls = mock_client.query.call_args_list
    for call in calls:
        assert call.kwargs["TableName"] == dynamodb_table
        assert call.kwargs["IndexName"] == "GSITYPE"

    # If it's a second query failure, verify the ExclusiveStartKey
    if query_number == 2:
        second_call = calls[1]
        assert second_call.kwargs["ExclusiveStartKey"] == {"key": "value"}


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
        client.add_receipt_letter(lt)

    # Act
    found_letters = client.list_receipt_letters_from_word(
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
@pytest.mark.parametrize(
    "param_name,invalid_value,expected_error",
    [
        # None value tests
        (
            "receipt_id",
            None,
            "receipt_id parameter is required and cannot be None.",
        ),
        (
            "image_id",
            None,
            "image_id parameter is required and cannot be None.",
        ),
        (
            "line_id",
            None,
            "line_id parameter is required and cannot be None.",
        ),
        (
            "word_id",
            None,
            "word_id parameter is required and cannot be None.",
        ),
        # Invalid type tests
        (
            "receipt_id",
            "not_an_integer",
            "receipt_id must be an integer.",
        ),
        (
            "image_id",
            "not_a_valid_uuid",
            "uuid must be a valid UUID.",
        ),
        (
            "line_id",
            "not_an_integer",
            "line_id must be an integer.",
        ),
        (
            "word_id",
            "not_an_integer",
            "word_id must be an integer.",
        ),
    ],
)
def test_listReceiptLettersFromWord_invalid_parameters(
    dynamodb_table: Literal["MyMockedTable"],
    param_name: str,
    invalid_value: Any,
    expected_error: str,
):
    """Test that listReceiptLettersFromWord validates its parameters correctly.

    Tests the following validation cases:
    - Required parameters cannot be None
    - receipt_id must be an integer
    - image_id must be a valid UUID
    - line_id must be an integer
    - word_id must be an integer
    """
    client = DynamoClient(dynamodb_table)

    # Prepare valid base parameters
    params = {
        "receipt_id": 1,
        "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "line_id": 10,
        "word_id": 5,
    }

    # Override the parameter being tested with invalid value
    params[param_name] = invalid_value

    with pytest.raises(ValueError, match=expected_error):
        client.list_receipt_letters_from_word(**params)  # type: ignore[arg-type]


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
    found_letters = client.list_receipt_letters_from_word(
        receipt_id=999,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=999,
        word_id=999,
    )
    assert isinstance(found_letters, list)
    assert len(found_letters) == 0


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
        client.add_receipt_letter(letter)

    # Mock pagination behavior
    first_response = {
        "Items": [letter.to_item() for letter in letters[:3]],
        "LastEvaluatedKey": {"key": "value"},
    }
    second_response = {
        "Items": [letter.to_item() for letter in letters[3:]],
    }
    mock_client = mocker.Mock()
    mock_client.query.side_effect = [first_response, second_response]
    client._client = mock_client

    # Act
    found_letters = client.list_receipt_letters_from_word(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=10,
        word_id=5,
    )

    # Assert
    assert len(found_letters) == len(letters)
    for letter in letters:
        assert letter in found_letters
    assert mock_client.query.call_count == 2


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error",
    [
        (
            "ResourceNotFoundException",
            "Table not found",
            "Could not list ReceiptLetters from the database",
        ),
        (
            "ProvisionedThroughputExceededException",
            "Throughput exceeded",
            "Provisioned throughput exceeded",
        ),
        (
            "ValidationException",
            "Invalid parameters",
            "One or more parameters given were invalid",
        ),
        (
            "InternalServerError",
            "Internal server error",
            "Internal server error",
        ),
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied",
        ),
        (
            "UnknownError",
            "Unknown error occurred",
            "Could not list ReceiptLetters from the database",
        ),
    ],
)
def test_listReceiptLettersFromWord_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_letter: ReceiptLetter,
    mocker,
    error_code: str,
    error_message: str,
    expected_error: str,
):
    """Test that listReceiptLettersFromWord handles various DynamoDB client
    errors appropriately:
    - ResourceNotFoundException
    - ProvisionedThroughputExceededException
    - ValidationException
    - InternalServerError
    - AccessDeniedException
    - UnknownError
    """
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.patch.object(
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

    with pytest.raises(Exception, match=expected_error):
        client.list_receipt_letters_from_word(
            receipt_id=sample_receipt_letter.receipt_id,
            image_id=sample_receipt_letter.image_id,
            line_id=sample_receipt_letter.line_id,
            word_id=sample_receipt_letter.word_id,
        )
    mock_client.assert_called_once()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_error,query_number",
    [
        (
            "ResourceNotFoundException",
            "Table not found during pagination",
            "Could not list ReceiptLetters from the database",
            2,  # Second query fails
        ),
        (
            "ProvisionedThroughputExceededException",
            "Throughput exceeded during pagination",
            "Provisioned throughput exceeded",
            2,  # Second query fails
        ),
        (
            "ValidationException",
            "Invalid parameters in pagination",
            "One or more parameters given were invalid",
            2,  # Second query fails
        ),
        (
            "InternalServerError",
            "Internal server error during pagination",
            "Internal server error",
            2,  # Second query fails
        ),
        (
            "AccessDeniedException",
            "Access denied during pagination",
            "Access denied",
            2,  # Second query fails
        ),
        (
            "UnknownError",
            "Unknown error during pagination",
            "Could not list ReceiptLetters from the database",
            2,  # Second query fails
        ),
    ],
)
def test_listReceiptLettersFromWord_pagination_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_letter: ReceiptLetter,
    mocker,
    error_code: str,
    error_message: str,
    expected_error: str,
    query_number: int,
):
    """Test that listReceiptLettersFromWord handles various DynamoDB client
    errors during pagination:
    - ResourceNotFoundException
    - ProvisionedThroughputExceededException
    - ValidationException
    - InternalServerError
    - AccessDeniedException
    - UnknownError

    The test simulates errors occurring in the second query during pagination.
    """
    client = DynamoClient(dynamodb_table)
    mock_client = mocker.Mock()

    # First query succeeds with a LastEvaluatedKey
    first_response = {
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

    # Second query fails with the specified error
    mock_client.query.side_effect = [
        first_response,
        ClientError(
            {
                "Error": {
                    "Code": error_code,
                    "Message": error_message,
                }
            },
            "Query",
        ),
    ]
    client._client = mock_client

    with pytest.raises(Exception, match=expected_error):
        client.list_receipt_letters_from_word(
            receipt_id=sample_receipt_letter.receipt_id,
            image_id=sample_receipt_letter.image_id,
            line_id=sample_receipt_letter.line_id,
            word_id=sample_receipt_letter.word_id,
        )

    # Verify that both queries were attempted
    assert mock_client.query.call_count == query_number
