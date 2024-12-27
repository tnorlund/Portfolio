from typing import Literal
import pytest
import boto3
from dynamo import ScaledImage, DynamoClient


def test_addScaledImage(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    scaled_image = ScaledImage(
        1, 248, 350, "2021-01-01T00:00:00", "Example_long_string", 0.1
    )

    # Act
    client.addScaledImage(scaled_image)

    # Assert
    response = boto3.client("dynamodb", region_name="us-east-1").get_item(
        TableName=dynamodb_table,
        Key=scaled_image.key(),
    )
    assert response["Item"] == scaled_image.to_item()

def test_addScaledImage_error(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    scaled_image = ScaledImage(
        1, 248, 350, "2021-01-01T00:00:00", "Example_long_string", 0.1
    )

    # Act
    client.addScaledImage(scaled_image)
    with pytest.raises(ValueError):
        client.addScaledImage(scaled_image)

def test_getScaledImage(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    scaled_image = ScaledImage(
        1, 248, 350, "2021-01-01T00:00:00", "Example_long_string", 0.1
    )
    client.addScaledImage(scaled_image)

    # Act
    response = client.getScaledImage(scaled_image.image_id, scaled_image.scale)

    # Assert
    assert response == scaled_image

def test_getScaledImage_error(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    scaled_image = ScaledImage(
        1, 248, 350, "2021-01-01T00:00:00", "Example_long_string", 0.1
    )

    # Act
    with pytest.raises(ValueError):
        client.getScaledImage(scaled_image.image_id, scaled_image.scale)

def test_listScaledImages(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    scaled_image = ScaledImage(
        1, 248, 350, "2021-01-01T00:00:00", "Example_long_string", 0.1
    )
    client.addScaledImage(scaled_image)

    # Act
    response = client.listScaledImages()

    # Assert
    assert response == [scaled_image]
