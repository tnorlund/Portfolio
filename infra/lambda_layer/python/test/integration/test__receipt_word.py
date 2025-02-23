import pytest
from typing import Literal
from dynamo import ReceiptWord, DynamoClient


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
        tags=["tag1", "tag2"],
    )


@pytest.mark.integration
def test_add_receipt_word(
    dynamodb_table: Literal["MyMockedTable"], sample_receipt_word: ReceiptWord
):
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act
    client.addReceiptWord(sample_receipt_word)

    # Assert
    retrieved_word = client.getReceiptWord(
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
    client.addReceiptWord(sample_receipt_word)

    # Act & Assert
    with pytest.raises(ValueError, match="already exists"):
        client.addReceiptWord(sample_receipt_word)


@pytest.mark.integration
def test_update_receipt_word(
    dynamodb_table: Literal["MyMockedTable"], sample_receipt_word: ReceiptWord
):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.addReceiptWord(sample_receipt_word)

    # Change text
    sample_receipt_word.text = "Updated receipt word"
    client.updateReceiptWord(sample_receipt_word)

    # Assert
    retrieved_word = client.getReceiptWord(
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
    client.addReceiptWord(sample_receipt_word)

    # Act
    client.deleteReceiptWord(
        sample_receipt_word.receipt_id,
        sample_receipt_word.image_id,
        sample_receipt_word.line_id,
        sample_receipt_word.word_id,
    )

    # Assert
    with pytest.raises(ValueError, match="not found"):
        client.getReceiptWord(
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
        client.addReceiptWord(w)

    # Act
    returned_words, _ = client.listReceiptWords()

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
        client.addReceiptWord(w)

    # Act
    found_words = client.listReceiptWordsFromLine(
        1, "3f52804b-2fad-4e00-92c8-b593da3a8ed3", 10
    )

    # Assert
    assert len(found_words) == 2
    for w in words_same_line:
        assert w in found_words
    assert another_word not in found_words
