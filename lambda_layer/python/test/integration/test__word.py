from typing import Literal
import pytest
import boto3
from dynamo import Word, DynamoClient


def test_addWord_no_tags(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    word = Word(
        1,
        1,
        1,
        "06\/27\/2024",
        0.1495695452950324,
        0.8868912353567051,
        0.08727867372574347,
        0.024234482472679675,
        7.7517295,
        1,
    )

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
    word = Word(
        1,
        1,
        1,
        "06\/27\/2024",
        0.1495695452950324,
        0.8868912353567051,
        0.08727867372574347,
        0.024234482472679675,
        7.7517295,
        1,
        ["tag1", "tag2"],
    )

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
    word = Word(
        1,
        1,
        1,
        "06\/27\/2024",
        0.1495695452950324,
        0.8868912353567051,
        0.08727867372574347,
        0.024234482472679675,
        7.7517295,
        1,
    )

    # Act
    client.addWord(word)
    with pytest.raises(ValueError):
        client.addWord(word)

def test_addWords(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    word1 = Word(
        1,
        1,
        1,
        "06\/27\/2024",
        0.1495695452950324,
        0.8868912353567051,
        0.08727867372574347,
        0.024234482472679675,
        7.7517295,
        1,
    )
    word2 = Word(
        1,
        1,
        2,
        "06\/27\/2024",
        0.1495695452950324,
        0.8868912353567051,
        0.08727867372574347,
        0.024234482472679675,
        7.7517295,
        1,
    )

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
    word = Word(
        1,
        1,
        1,
        "06\/27\/2024",
        0.1495695452950324,
        0.8868912353567051,
        0.08727867372574347,
        0.024234482472679675,
        7.7517295,
        1,
    )
    client.addWord(word)

    # Act
    client.deleteWord(1, 1, 1)

    # Assert
    with pytest.raises(ValueError):
        client.getWord(1, 1, 1)

def test_deleteWord_error(dynamodb_table: Literal["MyMockedTable"]):
    """Raises exception when word is not found"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act
    with pytest.raises(ValueError):
        client.deleteWord(1, 1, 1)

def test_deleteWordsFromLine(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    word1 = Word(
        1,
        1,
        1,
        "06\/27\/2024",
        0.1495695452950324,
        0.8868912353567051,
        0.08727867372574347,
        0.024234482472679675,
        7.7517295,
        1,
    )
    word2 = Word(
        1,
        1,
        2,
        "06\/27\/2024",
        0.1495695452950324,
        0.8868912353567051,
        0.08727867372574347,
        0.024234482472679675,
        7.7517295,
        1,
    )
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
    word = Word(
        1,
        1,
        1,
        "06\/27\/2024",
        0.1495695452950324,
        0.8868912353567051,
        0.08727867372574347,
        0.024234482472679675,
        7.7517295,
        1,
    )
    client.addWord(word)

    # Act
    retrieved_word = client.getWord(1, 1, 1)

    # Assert
    assert retrieved_word == word

def test_getWord_error(dynamodb_table: Literal["MyMockedTable"]):
    """Raises exception when word is not found"""
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act
    with pytest.raises(ValueError):
        client.getWord(1, 1, 2)

def test_listWords(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    word1 = Word(
        1,
        1,
        1,
        "06\/27\/2024",
        0.1495695452950324,
        0.8868912353567051,
        0.08727867372574347,
        0.024234482472679675,
        7.7517295,
        1,
    )
    word2 = Word(
        1,
        1,
        2,
        "06\/27\/2024",
        0.1495695452950324,
        0.8868912353567051,
        0.08727867372574347,
        0.024234482472679675,
        7.7517295,
        1,
    )
    client.addWord(word1)
    client.addWord(word2)

    # Act
    words = client.listWords()

    # Assert
    assert word1 in words
    assert word2 in words

def test_listWordsFromLine(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    word1 = Word(
        1,
        1,
        1,
        "06\/27\/2024",
        0.1495695452950324,
        0.8868912353567051,
        0.08727867372574347,
        0.024234482472679675,
        7.7517295,
        1,
    )
    word2 = Word(
        1,
        1,
        2,
        "06\/27\/2024",
        0.1495695452950324,
        0.8868912353567051,
        0.08727867372574347,
        0.024234482472679675,
        7.7517295,
        1,
    )
    word3 = Word(
        1,
        2,
        1,
        "06\/27\/2024",
        0.1495695452950324,
        0.8868912353567051,
        0.08727867372574347,
        0.024234482472679675,
        7.7517295,
        1,
    )
    client.addWord(word1)
    client.addWord(word2)
    client.addWord(word3)

    # Act
    words = client.listWordsFromLine(1, 1)

    # Assert
    assert word1 in words
    assert word2 in words
    assert word3 not in words
