from typing import Literal
import pytest
import boto3
from dynamo import Line, DynamoClient

correct_line_params = {
    "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
    "id": 1,
    "text": "test_string",
    "bounding_box": {
        "x": 0.4454263367632384,
        "height": 0.022867568134581906,
        "width": 0.08690182470506236,
        "y": 0.9167082878750482,
    },
    "top_right": {"y": 0.9307722198001792, "x": 0.5323281614683008},
    "top_left": {"y": 0.9395758560096301, "x": 0.44837726658954413},
    "bottom_right": {"x": 0.529377231641995, "y": 0.9167082878750482},
    "bottom_left": {"x": 0.4454263367632384, "y": 0.9255119240844992},
    "angle_degrees": -5.986527,
    "angle_radians": -0.10448461,
    "confidence": 1,
}


@pytest.mark.integration
def test_add_line(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    line = Line(**correct_line_params)

    # Act
    client.addLine(line)

    # Assert
    response = boto3.client("dynamodb", region_name="us-east-1").get_item(
        TableName=dynamodb_table,
        Key=line.key(),
    )
    assert "Item" in response, f"Item not found. response: {response}"
    assert response["Item"] == line.to_item()


@pytest.mark.integration
def test_add_line_error(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    line = Line(**correct_line_params)

    # Act
    client.addLine(line)
    with pytest.raises(ValueError):
        client.addLine(line)


@pytest.mark.integration
def test_add_lines(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    line_1 = Line(**correct_line_params)
    line_2_params = correct_line_params.copy()
    line_2_params["id"] = 2
    line_2_params["text"] = "A New Line"
    line_2 = Line(**line_2_params)

    # Act
    client.addLines([line_1, line_2])

    # Assert
    response = boto3.client("dynamodb", region_name="us-east-1").get_item(
        TableName=dynamodb_table,
        Key=line_1.key(),
    )
    assert "Item" in response, f"Item not found. response: {response}"
    assert response["Item"] == line_1.to_item()

    response = boto3.client("dynamodb", region_name="us-east-1").get_item(
        TableName=dynamodb_table,
        Key=line_2.key(),
    )
    assert "Item" in response, f"Item not found. response: {response}"
    assert response["Item"] == line_2.to_item()


@pytest.mark.integration
def test_delete_line(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    line = Line(**correct_line_params)
    client.addLine(line)

    # Act
    client.deleteLine("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1)

    # Assert
    with pytest.raises(ValueError):
        client.getLine("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1)


@pytest.mark.integration
def test_delete_line_error(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    line = Line(**correct_line_params)
    client.addLine(line)

    # Act
    with pytest.raises(ValueError):
        client.deleteLine(1, 2)


@pytest.mark.integration
def test_deleteLines(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    line_1 = Line(**correct_line_params)
    line_2_params = correct_line_params.copy()
    line_2_params["id"] = 2
    line_2_params["text"] = "A New Line"
    line_2 = Line(**line_2_params)
    client.addLine(line_1)
    client.addLine(line_2)

    # Act
    client.deleteLines([line_1, line_2])

    # Assert
    with pytest.raises(ValueError):
        client.getLine(1, 1)
    with pytest.raises(ValueError):
        client.getLine(1, 2)


@pytest.mark.integration
def test_get_line(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    line = Line(**correct_line_params)
    client.addLine(line)

    # Act
    retrieved_line = client.getLine("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1)

    # Assert
    assert retrieved_line == line


@pytest.mark.integration
def test_get_line_error(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    line = Line(**correct_line_params)
    client.addLine(line)

    # Act
    with pytest.raises(ValueError):
        client.getLine(1, 2)


@pytest.mark.integration
def test_listLines(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    line_1 = Line(**correct_line_params)
    line_2_params = correct_line_params.copy()
    line_2_params["id"] = 2
    line_2_params["text"] = "A New Line"
    line_2 = Line(**line_2_params)
    client.addLine(line_1)
    client.addLine(line_2)

    # Act
    lines = client.listLines()

    # Assert
    assert line_1 in lines
    assert line_2 in lines


@pytest.mark.integration
def test_listLinesEmpty(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act
    lines = client.listLines()

    # Assert
    assert len(lines) == 0


@pytest.mark.integration
def test_listLinesFromImage(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    line_1 = Line(**correct_line_params)
    line_2_params = correct_line_params.copy()
    line_2_params["id"] = 2
    line_2_params["text"] = "A New Line"
    line_2 = Line(**line_2_params)
    client.addLine(line_1)
    client.addLine(line_2)

    # Act
    lines = client.listLinesFromImage("3f52804b-2fad-4e00-92c8-b593da3a8ed3")

    # Assert
    assert line_1 in lines
    assert line_2 in lines
