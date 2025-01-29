from typing import Literal
import pytest
import boto3
from dynamo import Letter, DynamoClient

correct_letter_params = {
    "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
    "line_id": 1,
    "word_id": 1,
    "id": 1,
    "text": "0",
    "bounding_box": {
        "height": 0.022867568333804766,
        "width": 0.08688726243285705,
        "x": 0.4454336178993411,
        "y": 0.9167082877754368,
    },
    "top_right": {"x": 0.5323208803321982, "y": 0.930772983660083},
    "top_left": {"x": 0.44837726707985254, "y": 0.9395758561092415},
    "bottom_right": {"x": 0.5293772311516867, "y": 0.9167082877754368},
    "bottom_left": {"x": 0.4454336178993411, "y": 0.9255111602245953},
    "angle_degrees": -5.986527,
    "angle_radians": -0.1044846,
    "confidence": 1,
}


def test_addLetter(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    letter = Letter(**correct_letter_params)

    # Act
    client.addLetter(letter)

    # Assert
    response = boto3.client("dynamodb", region_name="us-east-1").get_item(
        TableName=dynamodb_table,
        Key=letter.key(),
    )
    assert "Item" in response, f"Item not found. response: {response}"
    assert response["Item"] == letter.to_item()


def test_addLetter_error(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    letter = Letter(**correct_letter_params)

    # Act
    client.addLetter(letter)
    with pytest.raises(ValueError):
        client.addLetter(letter)


def test_addLetters(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    letters = [
        Letter(**correct_letter_params),
        Letter(**{**correct_letter_params, "id": 2, "text": "1"}),
    ]

    # Act
    client.addLetters(letters)

    # Assert
    assert letters[0] == client.getLetter("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, 1, 1)
    assert letters[1] == client.getLetter("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, 1, 2)


def test_deleteLetter(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    letter = Letter(**correct_letter_params)

    # Act
    client.addLetter(letter)
    client.deleteLetter("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, 1, 1)

    # Assert
    with pytest.raises(ValueError):
        client.getLetter("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, 1, 1)


def test_deleteLetter_error(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    letter = Letter(**correct_letter_params)

    # Act
    client.addLetter(letter)
    with pytest.raises(ValueError):
        client.deleteLetter(1, 1, 1, 2)


def test_deleteLettersFromWord(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.addLetters(
        [
            Letter(**correct_letter_params),
            Letter(**{**correct_letter_params, "id": 2, "text": "1"}),
        ]
    )

    # Act
    client.deleteLettersFromWord(1, 1, 1)

    # Assert
    with pytest.raises(ValueError):
        client.getLetter(1, 1, 1, 1)
    with pytest.raises(ValueError):
        client.getLetter(1, 1, 1, 2)


def test_getLetter(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    letter = Letter(**correct_letter_params)

    # Act
    client.addLetter(letter)
    response = client.getLetter("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, 1, 1)

    # Assert
    assert response == letter


def test_getLetter_error(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    letter = Letter(**correct_letter_params)

    # Act
    client.addLetter(letter)
    with pytest.raises(ValueError):
        client.getLetter(1, 1, 1, 2)


def test_listLetters(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    letters = [
        Letter(**correct_letter_params),
        Letter(**{**correct_letter_params, "id": 2, "text": "1"}),
    ]
    client.addLetters(letters)

    # Act
    letters_retrieved = client.listLetters()

    # Assert
    assert letters_retrieved == letters


def test_listLettersFromWord(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    letter1 = Letter(**correct_letter_params)
    letter2_params = correct_letter_params.copy()
    letter2_params["word_id"] = 2
    letter2_params["text"] = "1"
    letter2 = Letter(**letter2_params)

    client.addLetter(letter1)
    client.addLetter(letter2)

    # Act
    letters = client.listLettersFromWord("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, 1)

    # Assert
    assert letter1 in letters
    assert letter2 not in letters
