from typing import Literal
import pytest
import boto3
from dynamo import Word, DynamoClient

correct_word_params = {
    "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
    "line_id": 2,
    "id": 3,
    "text": "test_string",
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


@pytest.mark.integration
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


@pytest.mark.integration
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


@pytest.mark.integration
def test_add_word_error(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    word = Word(**correct_word_params)

    # Act
    client.addWord(word)
    with pytest.raises(ValueError):
        client.addWord(word)


@pytest.mark.integration
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


@pytest.mark.integration
def test_deleteWord(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    word = Word(**correct_word_params)
    client.addWord(word)

    # Act
    client.deleteWord("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 2, 3)

    # Assert
    with pytest.raises(ValueError):
        client.getWord("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 2, 3)


@pytest.mark.integration
def test_deleteWord_error(dynamodb_table: Literal["MyMockedTable"]):
    """Raises exception when word is not found"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act
    with pytest.raises(ValueError):
        client.deleteWord(1, 2, 3)


@pytest.mark.integration
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


@pytest.mark.integration
def test_getWord(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    word = Word(**correct_word_params)
    client.addWord(word)

    # Act
    retrieved_word = client.getWord("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 2, 3)

    # Assert
    assert retrieved_word == word


@pytest.mark.integration
def test_getWord_error(dynamodb_table: Literal["MyMockedTable"]):
    """Raises exception when word is not found"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act
    with pytest.raises(ValueError):
        client.getWord(1, 2, 3)


@pytest.mark.integration
def test_getWords(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    words = [
        Word(**correct_word_params),
        Word(**{**correct_word_params, "id": 4}),
    ]
    client.addWords(words)

    # Act
    words_retrieved = client.getWords([words[0].key(), words[1].key()])

    # Assert
    assert words_retrieved == words


@pytest.mark.integration
def test_getWords_invalid_keys(dynamodb_table: Literal["MyMockedTable"]):
    """
    Shows how to test for invalid keys. We expect ValueError when PK or SK is invalid.
    """
    client = DynamoClient(dynamodb_table)

    # A key missing 'PK'
    bad_keys_missing_pk = [{"SK": {"S": "LINE#00002#WORD#00003"}}]
    with pytest.raises(ValueError, match="Keys must contain 'PK' and 'SK'"):
        client.getWords(bad_keys_missing_pk)

    # A key with PK not starting with 'IMAGE#'
    bad_keys_wrong_prefix = [
        {
            "PK": {"S": "FOO#00001"},
            "SK": {"S": "LINE#00002#WORD#00003"},
        }
    ]
    with pytest.raises(ValueError, match="PK must start with 'IMAGE#'"):
        client.getWords(bad_keys_wrong_prefix)

    # A key with SK missing 'WORD'
    bad_keys_no_word = [
        {
            "PK": {"S": "IMAGE#00001"},
            "SK": {"S": "LINE#00002#FOO#00003"},
        }
    ]
    with pytest.raises(ValueError, match="SK must contain 'WORD'"):
        client.getWords(bad_keys_no_word)


@pytest.mark.integration
def test_listWords(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    words = [
        Word(**correct_word_params),
        Word(**{**correct_word_params, "id": 4}),
    ]
    client.addWords(words)

    # Act
    words_retrieved = client.listWords()

    # Assert
    assert words_retrieved == words


@pytest.mark.integration
def test_listWordsFromLine(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    words = [
        Word(**correct_word_params),
        Word(**{**correct_word_params, "id": 1}),
        Word(**{**correct_word_params, "id": 2}),
    ]
    client.addWords(words)
    # sort words by id
    words = sorted(words, key=lambda x: x.id)

    # Act
    response = client.listWordsFromLine("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 2)

    # Assert
    assert words == response
