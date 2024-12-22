from typing import Literal
import pytest
import boto3
from dynamo import Image, DynamoClient


def test_add_image(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    image_id = 1
    image_width = 10
    image_height = 20
    image = Image(image_id, image_width, image_height)

    # Act
    client.addImage(image)

    # Assert
    response = boto3.client("dynamodb").get_item(
        TableName=dynamodb_table,
        Key={"PK": {"S": f"IMAGE#{image_id}"}, "SK": {"S": "IMAGE"}},
    )
    assert response["Item"] == image.to_item()

def test_add_image_error(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    image_id = 1
    image_width = 10
    image_height = 20
    image = Image(image_id, image_width, image_height)

    # Act
    client.addImage(image)
    with pytest.raises(Exception):
        client.addImage(image)
