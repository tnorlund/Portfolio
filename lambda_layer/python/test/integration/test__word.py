from typing import Literal
import pytest
import boto3
from dynamo import Word, DynamoClient

correct_word_params = {
    "image_id": 1,
    "line_id": 2,
    "id": 3,
    "text": "07\/03\/2024",
    "bounding_box": {
        "y": 0.9167082878750482,
        "width": 0.08690182470506236,
        "x": 0.4454263367632384,
        "height": 0.022867568134581906,
    },
    "top_right": {"y": 0.9307722198001792, "x": 0.5323281614683008},
    "top_left": {"x": 0.44837726658954413, "y": 0.9395758560096301},
    "bottom_right": {"y": 0.9167082878750482, "x": 0.529377231641995},
    "bottom_left": {"x": 0.4454263367632384, "y": 0.9255119240844992},
    "angle_degrees": -5.986527,
    "angle_radians": -0.10448461,
    "confidence": 1,
}


def test_addWord_no_tags(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    word = Word(**correct_word_params)

    # Act
    client.addWord(word)

    # Assert
    response = boto3.client("dynamodb", region_name="us-east-1").get_item(
        TableName=dynamodb_table,
        Key=word.key(),
    )
    assert "Item" in response, f"Item not found. response: {response}"
    assert response["Item"] == word.to_item()


def test_addWord_with_tags(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    word = Word(**correct_word_params, tags=["tag1", "tag2"])

    # Act
    client.addWord(word)

    # Assert
    response = boto3.client("dynamodb", region_name="us-east-1").get_item(
        TableName=dynamodb_table,
        Key=word.key(),
    )
    assert "Item" in response, f"Item not found. response: {response}"
    assert response["Item"] == word.to_item()


def test_add_word_error(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    word = Word(**correct_word_params)

    # Act
    client.addWord(word)
    with pytest.raises(ValueError):
        client.addWord(word)


def test_addWords(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    word1 = Word(**correct_word_params)
    word2_params = correct_word_params.copy()
    word2_params["id"] = 4
    word2 = Word(**word2_params)

    # Act
    client.addWords([word1, word2])

    # Assert
    response = boto3.client("dynamodb", region_name="us-east-1").get_item(
        TableName=dynamodb_table,
        Key=word1.key(),
    )
    assert "Item" in response, f"Item not found. response: {response}"
    assert response["Item"] == word1.to_item()

    response = boto3.client("dynamodb", region_name="us-east-1").get_item(
        TableName=dynamodb_table,
        Key=word2.key(),
    )
    assert "Item" in response, f"Item not found. response: {response}"
    assert response["Item"] == word2.to_item()


def test_deleteWord(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    word = Word(**correct_word_params)
    client.addWord(word)

    # Act
    client.deleteWord(1, 2, 3)

    # Assert
    with pytest.raises(ValueError):
        client.getWord(1, 2, 3)


def test_deleteWord_error(dynamodb_table: Literal["MyMockedTable"]):
    """Raises exception when word is not found"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act
    with pytest.raises(ValueError):
        client.deleteWord(1, 2, 3)


def test_deleteWordsFromLine(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    word1 = Word(**correct_word_params)
    word2_params = correct_word_params.copy()
    word2_params["id"] = 4
    word2 = Word(**word2_params)
    client.addWord(word1)
    client.addWord(word2)

    # Act
    client.deleteWordsFromLine(1, 1)

    # Assert
    with pytest.raises(ValueError):
        client.getWord(1, 1, 1)
    with pytest.raises(ValueError):
        client.getWord(1, 1, 2)


def test_getWord(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    word = Word(**correct_word_params)
    client.addWord(word)

    # Act
    retrieved_word = client.getWord(1, 2, 3)

    # Assert
    assert retrieved_word == word


def test_getWord_error(dynamodb_table: Literal["MyMockedTable"]):
    """Raises exception when word is not found"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act
    with pytest.raises(ValueError):
        client.getWord(1, 2, 3)


def test_listWords(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    words = [
        Word(**correct_word_params),
        Word(**{**correct_word_params, 'id': 4}),
    ]
    client.addWords(words)

    # Act
    words_retrieved = client.listWords()

    # Assert
    assert words_retrieved == words


def test_listWordsFromLine(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    word1 = Word(**correct_word_params)
    word2_params = correct_word_params.copy()
    word2_params["id"] = 4
    word2 = Word(**word2_params)
    word3_params = correct_word_params.copy()
    word3_params["id"] = 5
    word3_params["line_id"] = 3
    word3 = Word(**word3_params)
    client.addWord(word1)
    client.addWord(word2)
    client.addWord(word3)

    # Act
    words = client.listWordsFromLine(1, 2)

    # Assert
    assert word1 in words
    assert word2 in words
    assert word3 not in words
