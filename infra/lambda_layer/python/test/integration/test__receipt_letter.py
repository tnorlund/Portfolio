import pytest
from typing import Literal
from dynamo import ReceiptLetter, DynamoClient


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


@pytest.mark.integration
def test_add_receipt_letter(
    dynamodb_table: Literal["MyMockedTable"], sample_receipt_letter: ReceiptLetter
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
def test_add_receipt_letter_duplicate_raises(
    dynamodb_table: Literal["MyMockedTable"], sample_receipt_letter: ReceiptLetter
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.addReceiptLetter(sample_receipt_letter)

    # Act & Assert
    with pytest.raises(ValueError, match="already exists"):
        client.addReceiptLetter(sample_receipt_letter)


@pytest.mark.integration
def test_update_receipt_letter(
    dynamodb_table: Literal["MyMockedTable"], sample_receipt_letter: ReceiptLetter
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
def test_delete_receipt_letter(
    dynamodb_table: Literal["MyMockedTable"], sample_receipt_letter: ReceiptLetter
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.addReceiptLetter(sample_receipt_letter)

    # Act
    client.deleteReceiptLetter(
        sample_receipt_letter.receipt_id,
        sample_receipt_letter.image_id,
        sample_receipt_letter.line_id,
        sample_receipt_letter.word_id,
        sample_receipt_letter.letter_id,
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
    returned_letters = client.listReceiptLetters()

    # Assert
    for lt in letters:
        assert lt in returned_letters


@pytest.mark.integration
def test_receipt_letter_list_from_word(dynamodb_table: Literal["MyMockedTable"]):
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
