from typing import Literal
import pytest
import boto3
from dynamo import Letter, DynamoClient

def test_addLetter(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    letter = Letter(
        1,
        1,
        1,
        1,
        "0",
        0.1495695452950324,
        0.8868912353567051,
        0.08727867372574347,
        0.024234482472679675,
        7.7517295,
        1,
    )

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
    letter = Letter(
        1,
        1,
        1,
        1,
        "0",
        0.1495695452950324,
        0.8868912353567051,
        0.08727867372574347,
        0.024234482472679675,
        7.7517295,
        1,
    )

    # Act
    client.addLetter(letter)
    with pytest.raises(ValueError):
        client.addLetter(letter)

def test_addLetters(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    letter1 = Letter(
        1,
        1,
        1,
        1,
        "0",
        0.1495695452950324,
        0.8868912353567051,
        0.08727867372574347,
        0.024234482472679675,
        7.7517295,
        1,
    )
    letter2 = Letter(
        1,
        1,
        1,
        2,
        "0",
        0.1495695452950324,
        0.8868912353567051,
        0.08727867372574347,
        0.024234482472679675,
        7.7517295,
        1,
    )

    # Act
    client.addLetters([letter1, letter2])

    # Assert
    response = boto3.client("dynamodb", region_name="us-east-1").get_item(
        TableName=dynamodb_table,
        Key=letter1.key(),
    )
    assert "Item" in response, f"Item not found. response: {response}"
    assert response["Item"] == letter1.to_item()

    response = boto3.client("dynamodb", region_name="us-east-1").get_item(
        TableName=dynamodb_table,
        Key=letter2.key(),
    )
    assert "Item" in response, f"Item not found. response: {response}"
    assert response["Item"] == letter2.to_item()

def test_getLetter(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    letter = Letter(
        1,
        1,
        1,
        1,
        "0",
        0.1495695452950324,
        0.8868912353567051,
        0.08727867372574347,
        0.024234482472679675,
        7.7517295,
        1,
    )

    # Act
    client.addLetter(letter)
    response = client.getLetter(1, 1, 1, 1)

    # Assert
    assert response == letter

def test_getLetter_error(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    letter = Letter(
        1,
        1,
        1,
        1,
        "0",
        0.1495695452950324,
        0.8868912353567051,
        0.08727867372574347,
        0.024234482472679675,
        7.7517295,
        1,
    )

    # Act
    client.addLetter(letter)
    with pytest.raises(ValueError):
        client.getLetter(1, 1, 1, 2)

def test_listLetters(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    letter1 = Letter(
        1,
        1,
        1,
        1,
        "0",
        0.1495695452950324,
        0.8868912353567051,
        0.08727867372574347,
        0.024234482472679675,
        7.7517295,
        1,
    )
    letter2 = Letter(
        1,
        1,
        1,
        2,
        "0",
        0.1495695452950324,
        0.8868912353567051,
        0.08727867372574347,
        0.024234482472679675,
        7.7517295,
        1,
    )
    client.addLetter(letter1)
    client.addLetter(letter2)

    # Act
    letters = client.listLetters()

    # Assert
    assert letter1 in letters
    assert letter2 in letters

def test_listLettersFromWord(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    letter1 = Letter(
        1,
        1,
        1,
        1,
        "0",
        0.1495695452950324,
        0.8868912353567051,
        0.08727867372574347,
        0.024234482472679675,
        7.7517295,
        1,
    )
    letter2 = Letter(
        1,
        1,
        2,
        1,
        "0",
        0.1495695452950324,
        0.8868912353567051,
        0.08727867372574347,
        0.024234482472679675,
        7.7517295,
        1,
    )
    client.addLetter(letter1)
    client.addLetter(letter2)

    # Act
    letters = client.listLettersFromWord(1, 1, 1)

    # Assert
    assert letter1 in letters
    assert letter2 not in letters