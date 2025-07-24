from typing import Any, Dict, Literal

import pytest
from botocore.exceptions import ClientError, ParamValidationError

from receipt_dynamo import DynamoClient, ReceiptWord
from receipt_dynamo.constants import EmbeddingStatus
from receipt_dynamo.data.shared_exceptions import EntityAlreadyExistsError

# -------------------------------------------------------------------
#                        FIXTURES
# -------------------------------------------------------------------


@pytest.fixture
def sample_receipt_word():
    return ReceiptWord(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=10,
        word_id=5,
        text="Sample receipt word",
        bounding_box={"x": 0.1, "y": 0.2, "width": 0.2, "height": 0.03},
        top_left={"x": 0.1, "y": 0.2},
        top_right={"x": 0.3, "y": 0.2},
        bottom_left={"x": 0.1, "y": 0.23},
        bottom_right={"x": 0.3, "y": 0.23},
        angle_degrees=2.0,
        angle_radians=0.0349066,
        confidence=0.95,
    )


# -------------------------------------------------------------------
#                        addReceiptWord
# -------------------------------------------------------------------


@pytest.mark.integration
def test_addReceiptWord_success(
    dynamodb_table: Literal["MyMockedTable"], sample_receipt_word: ReceiptWord
):
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act
    client.add_receipt_word(sample_receipt_word)

    # Assert
    retrieved_word = client.get_receipt_word(
        sample_receipt_word.receipt_id,
        sample_receipt_word.image_id,
        sample_receipt_word.line_id,
        sample_receipt_word.word_id,
    )
    assert retrieved_word == sample_receipt_word


@pytest.mark.integration
def test_add_receipt_word_duplicate_raises(
    dynamodb_table: Literal["MyMockedTable"], sample_receipt_word: ReceiptWord
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.add_receipt_word(sample_receipt_word)

    # Act & Assert
    with pytest.raises(EntityAlreadyExistsError, match="already exists"):
        client.add_receipt_word(sample_receipt_word)


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "Word parameter is required and cannot be None."),
        (
            "not-a-receipt-word",
            "Word must be an instance of the ReceiptWord class.",
        ),
    ],
)
def test_addReceiptWord_invalid_parameters(
    dynamodb_table,
    sample_receipt_word,
    invalid_input,
    expected_error,
):
    """
    Tests that addReceiptWord raises ValueError for invalid parameters:
    - When receipt word is None
    - When receipt word is not an instance of ReceiptWord
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match=expected_error):
        client.add_receipt_word(invalid_input)  # type: ignore


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,error_message,expected_exception",
    [
        (
            "ConditionalCheckFailedException",
            "ReceiptWord already exists",
            "already exists",
        ),
        (
            "ResourceNotFoundException",
            "Table not found",
            "Table not found for operation add_receipt_word",
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
            "One or more parameters were invalid",
        ),
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied",
        ),
        (
            "UnknownError",
            "Unknown error",
            "Unknown error in add_receipt_word",
        ),
    ],
)
def test_addReceiptWord_client_errors(
    dynamodb_table,
    sample_receipt_word,
    mocker,
    error_code,
    error_message,
    expected_exception,
):
    """
    Tests that addReceiptWord handles various client errors appropriately:
    - ConditionalCheckFailedException when item already exists
    - ResourceNotFoundException when table not found
    - ProvisionedThroughputExceededException when throughput exceeded
    - InternalServerError for server-side errors
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
        client.add_receipt_word(sample_receipt_word)
    mock_put.assert_called_once()


# -------------------------------------------------------------------
#                        addReceiptWords
# -------------------------------------------------------------------


@pytest.mark.integration
def test_addReceiptWords_success(
    dynamodb_table: Literal["MyMockedTable"], sample_receipt_word: ReceiptWord
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    words = [sample_receipt_word]

    # Act
    client.add_receipt_words(words)

    # Assert
    retrieved_word = client.get_receipt_word(
        sample_receipt_word.receipt_id,
        sample_receipt_word.image_id,
        sample_receipt_word.line_id,
        sample_receipt_word.word_id,
    )
    assert retrieved_word == sample_receipt_word


@pytest.mark.integration
def test_addReceiptWords_large_batch(
    dynamodb_table: Literal["MyMockedTable"], sample_receipt_word: ReceiptWord
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    # Create 25 words (max batch size for DynamoDB)
    words = []
    for i in range(25):
        word = ReceiptWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=10,
            word_id=i,
            text=f"Word{i}",
            bounding_box={"x": 0.1, "y": 0.2, "width": 0.2, "height": 0.03},
            top_left={"x": 0.1, "y": 0.2},
            top_right={"x": 0.3, "y": 0.2},
            bottom_left={"x": 0.1, "y": 0.23},
            bottom_right={"x": 0.3, "y": 0.23},
            angle_degrees=2.0,
            angle_radians=0.0349066,
            confidence=0.95,
        )
        words.append(word)

    # Act
    client.add_receipt_words(words)

    # Assert
    for word in words:
        retrieved_word = client.get_receipt_word(
            word.receipt_id,
            word.image_id,
            word.line_id,
            word.word_id,
        )
        assert retrieved_word == word


@pytest.mark.integration
def test_addReceiptWords_with_unprocessed_items_retries(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_word: ReceiptWord,
    mocker,
):
    """Test that addReceiptWords retries unprocessed items."""
    client = DynamoClient(dynamodb_table)

    # First response has unprocessed items
    first_response = {
        "UnprocessedItems": {
            dynamodb_table: [
                {"PutRequest": {"Item": sample_receipt_word.to_item()}}
            ]
        }
    }
    # Second response has no unprocessed items
    second_response: Dict[str, Any] = {"UnprocessedItems": {}}

    mock_batch = mocker.patch.object(
        client._client,
        "batch_write_item",
        side_effect=[first_response, second_response],
    )

    # Should complete successfully after retrying
    client.add_receipt_words([sample_receipt_word])

    # Verify both calls were made
    assert mock_batch.call_count == 2


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_error",
    [
        (None, "Words parameter is required and cannot be None."),
        (
            "not-a-receipt-word",
            "Words must be provided as a list.",
        ),
        (
            ["not-a-receipt-word"],
            "All words must be instances of the ReceiptWord class.",
        ),
    ],
)
def test_addReceiptWords_invalid_parameters(
    dynamodb_table,
    sample_receipt_word,
    invalid_input,
    expected_error,
):
    """
    Tests that addReceiptWords raises ValueError for invalid parameters:
    - When words is None
    - When words is not a list
    - When words contains non-ReceiptWord instances
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match=expected_error):
        client.add_receipt_words(invalid_input)  # type: ignore


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
            "Table not found for operation add_receipt_words",
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
            "One or more parameters were invalid",
        ),
        (
            "AccessDeniedException",
            "Access denied",
            "Access denied",
        ),
        (
            "UnknownError",
            "Unknown error",
            "Unknown error in add_receipt_words",
        ),
    ],
)
def test_addReceiptWords_client_errors(
    dynamodb_table,
    sample_receipt_word,
    mocker,
    error_code,
    error_message,
    expected_exception,
):
    """
    Tests that addReceiptWords handles various client errors appropriately:
    - ConditionalCheckFailedException when item already exists
    - ResourceNotFoundException when table not found
    - ProvisionedThroughputExceededException when throughput exceeded
    - InternalServerError for server-side errors
    - ValidationException for invalid parameters
    - AccessDeniedException for access denied errors
    """
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
    with pytest.raises(Exception, match=expected_exception):
        client.add_receipt_words([sample_receipt_word])
    mock_batch.assert_called_once()


# -------------------------------------------------------------------
#                        updateReceiptWord
# -------------------------------------------------------------------
def test_update_receipt_word(
    dynamodb_table: Literal["MyMockedTable"], sample_receipt_word: ReceiptWord
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.add_receipt_word(sample_receipt_word)

    # Change text
    sample_receipt_word.text = "Updated receipt word"
    client.update_receipt_word(sample_receipt_word)

    # Assert
    retrieved_word = client.get_receipt_word(
        sample_receipt_word.receipt_id,
        sample_receipt_word.image_id,
        sample_receipt_word.line_id,
        sample_receipt_word.word_id,
    )
    assert retrieved_word.text == "Updated receipt word"


@pytest.mark.integration
def test_delete_receipt_word(
    dynamodb_table: Literal["MyMockedTable"], sample_receipt_word: ReceiptWord
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.add_receipt_word(sample_receipt_word)

    # Act
    client.delete_receipt_word(sample_receipt_word)

    # Assert
    with pytest.raises(ValueError, match="not found"):
        client.get_receipt_word(
            sample_receipt_word.receipt_id,
            sample_receipt_word.image_id,
            sample_receipt_word.line_id,
            sample_receipt_word.word_id,
        )


@pytest.mark.integration
def test_receipt_word_list(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    words = [
        ReceiptWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=5,
            word_id=i,
            text=f"Word{i}",
            bounding_box={"x": 0.0, "y": 0.0, "width": 0.1, "height": 0.02},
            top_left={"x": 0.0, "y": 0.0},
            top_right={"x": 0.1, "y": 0.0},
            bottom_left={"x": 0.0, "y": 0.02},
            bottom_right={"x": 0.1, "y": 0.02},
            angle_degrees=0,
            angle_radians=0,
            confidence=1.0,
        )
        for i in range(1, 4)
    ]
    for w in words:
        client.add_receipt_word(w)

    # Act
    returned_words, _ = client.list_receipt_words()

    # Assert
    for w in words:
        assert w in returned_words


@pytest.mark.integration
def test_receipt_word_list_from_line(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)

    words_same_line = [
        ReceiptWord(
            receipt_id=1,
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=10,
            word_id=i,
            text=f"LineWord{i}",
            bounding_box={"x": 0.0, "y": 0.0, "width": 0.1, "height": 0.02},
            top_left={"x": 0.0, "y": 0.0},
            top_right={"x": 0.1, "y": 0.0},
            bottom_left={"x": 0.0, "y": 0.02},
            bottom_right={"x": 0.1, "y": 0.02},
            angle_degrees=0,
            angle_radians=0,
            confidence=1.0,
        )
        for i in range(1, 3)
    ]
    # Another word in a different line
    another_word = ReceiptWord(
        receipt_id=1,
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=99,
        word_id=999,
        text="Different line word",
        bounding_box={"x": 0.2, "y": 0.3, "width": 0.1, "height": 0.01},
        top_left={"x": 0.2, "y": 0.3},
        top_right={"x": 0.3, "y": 0.3},
        bottom_left={"x": 0.2, "y": 0.31},
        bottom_right={"x": 0.3, "y": 0.31},
        angle_degrees=10,
        angle_radians=0.17453,
        confidence=0.9,
    )
    for w in words_same_line + [another_word]:
        client.add_receipt_word(w)

    # Act
    found_words = client.list_receipt_words_from_line(
        1, "3f52804b-2fad-4e00-92c8-b593da3a8ed3", 10
    )

    # Assert
    assert len(found_words) == 2
    for w in words_same_line:
        assert w in found_words
    assert another_word not in found_words


# -------------------------------------------------------------------
#                        listReceiptWordsByEmbeddingStatus
# -------------------------------------------------------------------


@pytest.mark.integration
def test_list_receipt_words_by_embedding_status(
    dynamodb_table: Literal["MyMockedTable"], sample_receipt_word: ReceiptWord
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    # Add a word with NONE status
    word_none = sample_receipt_word
    client.add_receipt_word(word_none)
    # Create and add a separate word with PENDING status
    word_pending = ReceiptWord(
        receipt_id=sample_receipt_word.receipt_id,
        image_id=sample_receipt_word.image_id,
        line_id=99,
        word_id=sample_receipt_word.word_id,
        text=sample_receipt_word.text,
        bounding_box=sample_receipt_word.bounding_box,
        top_left=sample_receipt_word.top_left,
        top_right=sample_receipt_word.top_right,
        bottom_left=sample_receipt_word.bottom_left,
        bottom_right=sample_receipt_word.bottom_right,
        angle_degrees=sample_receipt_word.angle_degrees,
        angle_radians=sample_receipt_word.angle_radians,
        confidence=sample_receipt_word.confidence,
        embedding_status=EmbeddingStatus.PENDING,
    )
    client.add_receipt_word(word_pending)
    # Act
    found_words = client.list_receipt_words_by_embedding_status(
        EmbeddingStatus.NONE
    )

    # Assert
    assert len(found_words) == 1
    assert found_words[0] == word_none
