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
def test_addReceiptLetter_raises_value_error_receipt_letter_none(
    dynamodb_table, sample_receipt_letter, mocker
):
    """
    Tests that addReceiptLetter raises ValueError when the receipt letter is None.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        ValueError, match="letter parameter is required and cannot be None."
    ):
        client.addReceiptLetter(None)  # type: ignore


@pytest.mark.integration
def test_addReceiptLetter_raises_value_error_receipt_letter_not_instance(
    dynamodb_table, sample_receipt_letter, mocker
):
    """
    Tests that addReceiptLetter raises ValueError when the receipt letter is
    not an instance of ReceiptLetter.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        ValueError,
        match="letter must be an instance of the ReceiptLetter class.",
    ):
        client.addReceiptLetter("not-a-receipt-letter")  # type: ignore


@pytest.mark.integration
def test_addReceiptLetter_raises_conditional_check_failed(
    dynamodb_table, sample_receipt_letter, mocker
):
    """
    Simulate a receipt letter already existing, causing a
    ConditionalCheckFailedException.
    """
    client = DynamoClient(dynamodb_table)
    client.addReceiptLetter(sample_receipt_letter)
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ConditionalCheckFailedException",
                    "Message": "Item already exists",
                }
            },
            "PutItem",
        ),
    )

    with pytest.raises(ValueError, match="already exists"):
        client.addReceiptLetter(sample_receipt_letter)
    mock_put.assert_called_once()


@pytest.mark.integration
def test_addReceiptLetter_raises_resource_not_found(
    dynamodb_table, sample_receipt_letter, mocker
):
    """
    Simulate a ResourceNotFoundException when adding a receipt letter.
    """
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
def test_addReceiptLetter_raises_provisioned_throughput_exceeded(
    dynamodb_table, sample_receipt_letter, mocker
):
    """
    Simulate a ProvisionedThroughputExceededException when adding a receipt letter.
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
        client.addReceiptLetter(sample_receipt_letter)
    mock_put.assert_called_once()


@pytest.mark.integration
def test_addReceiptLetter_raises_internal_server_error(
    dynamodb_table, sample_receipt_letter, mocker
):
    """
    Simulate an InternalServerError when adding a receipt letter.
    """
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
        client.addReceiptLetter(sample_receipt_letter)
    mock_put.assert_called_once()


@pytest.mark.integration
def test_addReceiptLetter_raises_unknown_error(
    dynamodb_table, sample_receipt_letter, mocker
):
    """
    Simulate an unknown error when adding a receipt letter.
    """
    client = DynamoClient(dynamodb_table)
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "UnknownError",
                    "Message": "Unknown error",
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
def test_addReceiptLetters_raises_value_error_letters_none(
    dynamodb_table, sample_receipt_letter, mocker
):
    """
    Tests that addReceiptLetters raises ValueError when the letters are None.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="letters parameter is required and cannot be None."):
        client.addReceiptLetters(None)  # type: ignore


@pytest.mark.integration
def test_addReceiptLetters_raises_value_error_letters_not_list(
    dynamodb_table, sample_receipt_letter, mocker
):
    """
    Tests that addReceiptLetters raises ValueError when the letters are not a list.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="letters must be a list of ReceiptLetter instances."):
        client.addReceiptLetters("not-a-list")  # type: ignore


@pytest.mark.integration
def test_addReceiptLetters_raises_value_error_letters_not_list_of_receipt_letters(
    dynamodb_table, sample_receipt_letter, mocker
):
    """
    Tests that addReceiptLetters raises ValueError when the letters are not a list of ReceiptLetter instances.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="All letters must be instances of the ReceiptLetter class."):
        client.addReceiptLetters(["not-a-receipt-letter"])  # type: ignore


# -------------------------------------------------------------------
#                        updateReceiptLetter
# -------------------------------------------------------------------

@pytest.mark.integration
def test_update_receipt_letter(
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


# -------------------------------------------------------------------
#                        updateReceiptLetters   
# -------------------------------------------------------------------

@pytest.mark.integration
def test_update_receipt_letters(
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
def test_update_receipt_letters_raises_value_error_letters_none(
    dynamodb_table, sample_receipt_letter, mocker
):
    """
    Tests that updateReceiptLetters raises ValueError when the letters are None.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="letters parameter is required and cannot be None."):
        client.updateReceiptLetters(None)  # type: ignore

@pytest.mark.integration
def test_update_receipt_letters_raises_value_error_letters_not_list(
    dynamodb_table, sample_receipt_letter, mocker
):
    """
    Tests that updateReceiptLetters raises ValueError when the letters are not a list.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="letters must be a list of ReceiptLetter instances."):
        client.updateReceiptLetters("not-a-list")  # type: ignore

@pytest.mark.integration
def test_update_receipt_letters_raises_value_error_letters_not_list_of_receipt_letters(
    dynamodb_table, sample_receipt_letter, mocker
):
    """
    Tests that updateReceiptLetters raises ValueError when the letters are not a list of ReceiptLetter instances.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="All letters must be instances of the ReceiptLetter class."):
        client.updateReceiptLetters(["not-a-receipt-letter"])  # type: ignore

@pytest.mark.integration
def test_update_receipt_letters_raises_value_error_letter_not_found(
    dynamodb_table, sample_receipt_letter, mocker
):
    """
    Tests that updateReceiptLetters raises ValueError when a letter doesn't exist.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="One or more ReceiptLetters do not exist"):
        client.updateReceiptLetters([sample_receipt_letter])  # Letter not added first

# -------------------------------------------------------------------
#                        deleteReceiptLetter
# -------------------------------------------------------------------

@pytest.mark.integration
def test_delete_receipt_letter(
    dynamodb_table: Literal["MyMockedTable"],
    sample_receipt_letter: ReceiptLetter,
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.addReceiptLetter(sample_receipt_letter)

    # Act
    client.deleteReceiptLetter(
        sample_receipt_letter
    )

    # Assert
    with pytest.raises(ValueError, match="not found"):
        client.getReceiptLetter(
            sample_receipt_letter.receipt_id,
            sample_receipt_letter.image_id,
            sample_receipt_letter.line_id,
            sample_receipt_letter.word_id,
            sample_receipt_letter.letter_id,
        )

# -------------------------------------------------------------------
#                        deleteReceiptLetters
# -------------------------------------------------------------------

@pytest.mark.integration
def test_delete_receipt_letters(
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
def test_delete_receipt_letters_raises_value_error_letters_none(
    dynamodb_table, sample_receipt_letter, mocker
):
    """
    Tests that deleteReceiptLetters raises ValueError when the letters are None.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="letters parameter is required and cannot be None."):
        client.deleteReceiptLetters(None)  # type: ignore

@pytest.mark.integration
def test_delete_receipt_letters_raises_value_error_letters_not_list(
    dynamodb_table, sample_receipt_letter, mocker
):
    """
    Tests that deleteReceiptLetters raises ValueError when the letters are not a list.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="letters must be a list of ReceiptLetter instances."):
        client.deleteReceiptLetters("not-a-list")  # type: ignore

@pytest.mark.integration
def test_delete_receipt_letters_raises_value_error_letters_not_list_of_receipt_letters(
    dynamodb_table, sample_receipt_letter, mocker
):
    """
    Tests that deleteReceiptLetters raises ValueError when the letters are not a list of ReceiptLetter instances.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="All letters must be instances of the ReceiptLetter class."):
        client.deleteReceiptLetters(["not-a-receipt-letter"])  # type: ignore

# -------------------------------------------------------------------
#                        getReceiptLetter
# -------------------------------------------------------------------

@pytest.mark.integration
def test_get_receipt_letter(
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
def test_get_receipt_letter_not_found(
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
def test_get_receipt_letter_invalid_receipt_id(
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
def test_get_receipt_letter_invalid_image_id(
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
def test_get_receipt_letter_invalid_line_id(
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
def test_get_receipt_letter_invalid_word_id(
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
def test_get_receipt_letter_invalid_letter_id(
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

# -------------------------------------------------------------------
#                        listReceiptLetters
# -------------------------------------------------------------------

@pytest.mark.integration
def test_receipt_letter_list(dynamodb_table: Literal["MyMockedTable"]):
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
def test_receipt_letter_list_with_limit(dynamodb_table: Literal["MyMockedTable"]):
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
    assert last_key is not None  # Should have a last evaluated key for pagination
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
def test_receipt_letter_list_invalid_limit(dynamodb_table: Literal["MyMockedTable"]):
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="limit must be an integer or None"):
        client.listReceiptLetters(limit="invalid")  # type: ignore

@pytest.mark.integration
def test_receipt_letter_list_invalid_last_evaluated_key(
    dynamodb_table: Literal["MyMockedTable"]
):
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="lastEvaluatedKey must be a dictionary or None"):
        client.listReceiptLetters(lastEvaluatedKey="invalid")  # type: ignore

# -------------------------------------------------------------------
#                        listReceiptLettersFromWord
# -------------------------------------------------------------------

@pytest.mark.integration
def test_receipt_letter_list_from_word(
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
