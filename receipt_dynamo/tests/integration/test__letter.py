from typing import Any, Dict, Literal

import boto3
import pytest

from receipt_dynamo import DynamoClient, Letter
from receipt_dynamo.data.shared_exceptions import EntityAlreadyExistsError

correct_letter_params: Dict[str, Any] = {
    "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
    "line_id": 1,
    "word_id": 1,
    "letter_id": 1,
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


@pytest.fixture
def example_letter():
    return Letter(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=1,
        word_id=1,
        letter_id=1,
        text="0",
        bounding_box={
            "height": 0.022867568333804766,
            "width": 0.08688726243285705,
            "x": 0.4454336178993411,
            "y": 0.9167082877754368,
        },
        top_right={"x": 0.5323208803321982, "y": 0.930772983660083},
        top_left={"x": 0.44837726707985254, "y": 0.9395758561092415},
        bottom_right={"x": 0.5293772311516867, "y": 0.9167082877754368},
        bottom_left={"x": 0.4454336178993411, "y": 0.9255111602245953},
        angle_degrees=-5.986527,
        angle_radians=-0.1044846,
        confidence=1,
    )


@pytest.mark.integration
def test_letter_add(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    letter = Letter(**correct_letter_params)

    # Act
    client.add_letter(letter)

    # Assert
    response = boto3.client("dynamodb", region_name="us-east-1").get_item(
        TableName=dynamodb_table,
        Key=letter.key,
    )
    assert "Item" in response, f"Item not found. response: {response}"
    assert response["Item"] == letter.to_item()


@pytest.mark.integration
def test_letter_add_error(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    letter = Letter(**correct_letter_params)

    # Act
    client.add_letter(letter)
    with pytest.raises(EntityAlreadyExistsError):
        client.add_letter(letter)


@pytest.mark.integration
def test_letter_add_all(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    letters = [
        Letter(**correct_letter_params),
        Letter(**{**correct_letter_params, "letter_id": 2, "text": "1"}),
    ]

    # Act
    client.add_letters(letters)

    # Assert
    assert letters[0] == client.get_letter(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, 1, 1
    )
    assert letters[1] == client.get_letter(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, 1, 2
    )


@pytest.mark.integration
def test_letter_delete(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    letter = Letter(**correct_letter_params)

    # Act
    client.add_letter(letter)
    client.delete_letter("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, 1, 1)

    # Assert
    with pytest.raises(ValueError):
        client.get_letter("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, 1, 1)


@pytest.mark.integration
def test_letter_delete_error(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    letter = Letter(**correct_letter_params)

    # Act
    client.add_letter(letter)
    with pytest.raises(ValueError):
        client.delete_letter("invalid-uuid", 1, 1, 2)


@pytest.mark.integration
def test_letter_delete_from_word(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    client.add_letters(
        [
            Letter(**correct_letter_params),
            Letter(**{**correct_letter_params, "letter_id": 2, "text": "1"}),
        ]
    )

    # Act
    client.delete_letters_from_word(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, 1
    )

    # Assert
    with pytest.raises(ValueError):
        client.get_letter("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, 1, 1)
    with pytest.raises(ValueError):
        client.get_letter("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, 1, 2)


@pytest.mark.integration
def test_letter_get(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    letter = Letter(**correct_letter_params)

    # Act
    client.add_letter(letter)
    response = client.get_letter(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, 1, 1
    )

    # Assert
    assert response == letter


@pytest.mark.integration
def test_letter_get_error(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    letter = Letter(**correct_letter_params)

    # Act
    client.add_letter(letter)
    with pytest.raises(ValueError):
        client.get_letter("1", 1, 1, 2)


@pytest.mark.integration
def test_letter_list(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    letters = [
        Letter(**correct_letter_params),
        Letter(**{**correct_letter_params, "letter_id": 2, "text": "1"}),
    ]
    client.add_letters(letters)

    # Act
    letters_retrieved, _ = client.list_letters()

    # Assert
    assert letters_retrieved == letters


@pytest.mark.integration
def test_letter_list_from_word(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    letter1 = Letter(**correct_letter_params)
    letter2_params = correct_letter_params.copy()
    letter2_params["word_id"] = 2
    letter2_params["text"] = "1"
    letter2 = Letter(**letter2_params)

    client.add_letter(letter1)
    client.add_letter(letter2)

    # Act
    letters = client.list_letters_from_word(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, 1
    )

    # Assert
    assert letter1 in letters
    assert letter2 not in letters
