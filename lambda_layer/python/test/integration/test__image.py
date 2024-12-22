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
    response = boto3.client("dynamodb", region_name="us-east-1").get_item(
        TableName=dynamodb_table,
        Key={"PK": {"S": "IMAGE#00001"}, "SK": {"S": "IMAGE"}},
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


def test_get_image(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    image_id = 1
    image_width = 10
    image_height = 20
    image = Image(image_id, image_width, image_height)
    client.addImage(image)

    # Act
    retrieved_image = client.getImage(image_id)

    # Assert
    assert retrieved_image == image

def test_listImages(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    image_id = 1
    image_width = 10
    image_height = 20
    image_1 = Image(1, 10, 20)
    image_2 = Image(2, 30, 40)
    client.addImage(image_1)
    client.addImage(image_2)

    # Act
    images = client.listImages()

    # Assert
    assert image_1 in images
    assert image_2 in images
