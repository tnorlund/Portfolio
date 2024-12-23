from typing import Literal
import pytest
import boto3
from dynamo import Line, DynamoClient


def test_add_line(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    line = Line(
        1,
        1,
        "06\/27\/2024",
        0.14956954529503239,
        0.8868912353567051,
        0.0872786737257435,
        0.024234482472679675,
        7.7517295,
        1,
    )

    # Act
    client.addLine(line)

    # Assert
    response = boto3.client("dynamodb", region_name="us-east-1").get_item(
        TableName=dynamodb_table,
        Key=line.key(),
    )
    assert "Item" in response, f"Item not found. response: {response}"
    assert response["Item"] == line.to_item()

def test_add_line_error(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    line = Line(
        1,
        1,
        "06\/27\/2024",
        0.14956954529503239,
        0.8868912353567051,
        0.0872786737257435,
        0.024234482472679675,
        7.7517295,
        1,
    )

    # Act
    client.addLine(line)
    with pytest.raises(ValueError):
        client.addLine(line)

def test_get_line(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    line = Line(
        1,
        1,
        "Tyler",
        0.14956954529503239,
        0.8868912353567051,
        0.0872786737257435,
        0.024234482472679675,
        7.7517295,
        1,
    )
    client.addLine(line)

    # Act
    retrieved_line = client.getLine(1, 1)

    # Assert
    assert retrieved_line == line

def test_get_line_error(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    line = Line(
        1,
        1,
        "Tyler",
        0.14956954529503239,
        0.8868912353567051,
        0.0872786737257435,
        0.024234482472679675,
        7.7517295,
        1,
    )
    client.addLine(line)

    # Act
    with pytest.raises(ValueError):
        client.getLine(1, 2)

def test_listLines(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    line_1 = Line(
        1,
        1,
        "06\/27\/2024",
        0.14956954529503239,
        0.8868912353567051,
        0.0872786737257435,
        0.024234482472679675,
        7.7517295,
        1,
    )
    line_2 = Line(
        1,
        2,
        "A New Line",
        0.14956954529503239,
        0.8868912353567051,
        0.0872786737257435,
        0.024234482472679675,
        7.7517295,
        1,
    )
    client.addLine(line_1)
    client.addLine(line_2)

    # Act
    lines = client.listLines()

    # Assert
    assert line_1 in lines
    assert line_2 in lines

def test_listLinesEmpty(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act
    lines = client.listLines()

    # Assert
    assert len(lines) == 0

def test_listLinesFromImage(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    line_1 = Line(
        1,
        1,
        "06\/27\/2024",
        0.14956954529503239,
        0.8868912353567051,
        0.0872786737257435,
        0.024234482472679675,
        7.7517295,
        1,
    )
    line_2 = Line(
        1,
        2,
        "A New Line",
        0.14956954529503239,
        0.8868912353567051,
        0.0872786737257435,
        0.024234482472679675,
        7.7517295,
        1,
    )
    client.addLine(line_1)
    client.addLine(line_2)

    # Act
    lines = client.listLinesFromImage(1)

    # Assert
    assert line_1 in lines
    assert line_2 in lines
