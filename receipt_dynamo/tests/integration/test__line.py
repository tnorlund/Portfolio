from typing import Any, Dict, Literal

import boto3
import pytest

from receipt_dynamo import DynamoClient, Line
from receipt_dynamo.data.shared_exceptions import EntityAlreadyExistsError, EntityNotFoundError

correct_line_params: Dict[str, Any] = {
    "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
    "line_id": 1,
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
def test_line_add(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    line = Line(**correct_line_params)

    # Act
    client.add_line(line)

    # Assert
    response = boto3.client("dynamodb", region_name="us-east-1").get_item(
        TableName=dynamodb_table,
        Key=line.key,
    )
    assert "Item" in response, f"Item not found. response: {response}"
    assert response["Item"] == line.to_item()


@pytest.mark.integration
def test_line_add_error(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    line = Line(**correct_line_params)

    # Act
    client.add_line(line)
    with pytest.raises(EntityAlreadyExistsError):
        client.add_line(line)


@pytest.mark.integration
def test_line_add_all(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    line_1 = Line(**correct_line_params)
    line_2_params = correct_line_params.copy()
    line_2_params["line_id"] = 2
    line_2_params["text"] = "A New Line"
    line_2 = Line(**line_2_params)

    # Act
    client.add_lines([line_1, line_2])

    # Assert
    response = boto3.client("dynamodb", region_name="us-east-1").get_item(
        TableName=dynamodb_table,
        Key=line_1.key,
    )
    assert "Item" in response, f"Item not found. response: {response}"
    assert response["Item"] == line_1.to_item()

    response = boto3.client("dynamodb", region_name="us-east-1").get_item(
        TableName=dynamodb_table,
        Key=line_2.key,
    )
    assert "Item" in response, f"Item not found. response: {response}"
    assert response["Item"] == line_2.to_item()


@pytest.mark.integration
def test_line_delete(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    line = Line(**correct_line_params)
    client.add_line(line)

    # Act
    client.delete_line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1)

    # Assert
    with pytest.raises(EntityNotFoundError):
        client.get_line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1)


@pytest.mark.integration
def test_line_delete_error(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    line = Line(**correct_line_params)
    client.add_line(line)

    # Act
    with pytest.raises(ValueError):
        client.delete_line("invalid-uuid", 2)


@pytest.mark.integration
def test_line_delete_all(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    line_1 = Line(**correct_line_params)
    line_2_params = correct_line_params.copy()
    line_2_params["line_id"] = 2
    line_2_params["text"] = "A New Line"
    line_2 = Line(**line_2_params)
    client.add_line(line_1)
    client.add_line(line_2)

    # Act
    client.delete_lines([line_1, line_2])

    # Assert
    with pytest.raises(EntityNotFoundError):
        client.get_line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1)
    with pytest.raises(EntityNotFoundError):
        client.get_line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 2)


@pytest.mark.integration
def test_line_get(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    line = Line(**correct_line_params)
    client.add_line(line)

    # Act
    retrieved_line = client.get_line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1)

    # Assert
    assert retrieved_line == line


@pytest.mark.integration
def test_line_get_error(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    line = Line(**correct_line_params)
    client.add_line(line)

    # Act
    with pytest.raises(EntityNotFoundError):
        client.get_line("invalid-uuid", 2)


@pytest.mark.integration
def test_line_list(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    line_1 = Line(**correct_line_params)
    line_2_params = correct_line_params.copy()
    line_2_params["line_id"] = 2
    line_2_params["text"] = "A New Line"
    line_2 = Line(**line_2_params)
    client.add_line(line_1)
    client.add_line(line_2)

    # Act
    lines, _ = client.list_lines()

    # Assert
    assert line_1 in lines
    assert line_2 in lines


@pytest.mark.integration
def test_line_list_empty(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act
    lines, _ = client.list_lines()

    # Assert
    assert len(lines) == 0


@pytest.mark.integration
def test_line_list_from_image(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    line_1 = Line(**correct_line_params)
    line_2_params = correct_line_params.copy()
    line_2_params["line_id"] = 2
    line_2_params["text"] = "A New Line"
    line_2 = Line(**line_2_params)
    client.add_line(line_1)
    client.add_line(line_2)

    # Act
    lines = client.list_lines_from_image(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    )

    # Assert
    assert line_1 in lines
    assert line_2 in lines
