from typing import Literal
import pytest
import boto3
from dynamo import ScaledImage, DynamoClient


def test_addScaledImage(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    scaled_image = ScaledImage(
        1, "2021-01-01T00:00:00", "Example_long_string", 90
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
        1, "2021-01-01T00:00:00", "Example_long_string", 90
    )

    # Act
    client.addScaledImage(scaled_image)
    with pytest.raises(ValueError):
        client.addScaledImage(scaled_image)

def test_deleteScaledImage(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    scaled_image = ScaledImage(
        1, "2021-01-01T00:00:00", "Example_long_string", 90
    )
    client.addScaledImage(scaled_image)

    # Act
    client.deleteScaledImage(scaled_image.image_id, scaled_image.quality)

    # Assert
    with pytest.raises(ValueError):
        client.getScaledImage(scaled_image.image_id, scaled_image.quality)

def test_deleteScaledImage_error(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    scaled_image = ScaledImage(
        1, "2021-01-01T00:00:00", "Example_long_string", 90
    )

    # Act
    with pytest.raises(ValueError):
        client.deleteScaledImage(scaled_image.image_id, scaled_image.quality)

def test_deleteScaledImages(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    scaled_image1 = ScaledImage(
        1, "2021-01-01T00:00:00", "Example_long_string", 90
    )
    scaled_image2 = ScaledImage(
        1, "2022-01-01T00:00:00", "Example_long_string", 80
    )
    scaled_image3 = ScaledImage(
        1, "2023-01-01T00:00:00", "Example_long_string", 70
    )
    client.addScaledImage(scaled_image1)
    client.addScaledImage(scaled_image2)
    client.addScaledImage(scaled_image3)

    # Act
    client.deleteScaledImages([scaled_image1, scaled_image2, scaled_image3])

    # Assert
    with pytest.raises(ValueError):
        client.getScaledImage(scaled_image1.image_id, scaled_image1.quality)
    with pytest.raises(ValueError):
        client.getScaledImage(scaled_image2.image_id, scaled_image2.quality)
    with pytest.raises(ValueError):
        client.getScaledImage(scaled_image3.image_id, scaled_image3.quality)

def test_deleteScaledImageFromImage(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    scaled_image1 = ScaledImage(
        1, "2021-01-01T00:00:00", "Example_long_string", 90
    )
    scaled_image2 = ScaledImage(
        1, "2022-01-01T00:00:00", "Example_long_string", 80
    )
    scaled_image3 = ScaledImage(
        1, "2023-01-01T00:00:00", "Example_long_string", 70
    )
    client.addScaledImage(scaled_image1)
    client.addScaledImage(scaled_image2)
    client.addScaledImage(scaled_image3)

    # Act
    client.deleteScaledImageFromImage(1)

    # Assert
    with pytest.raises(ValueError):
        client.getScaledImage(scaled_image1.image_id, scaled_image1.quality)
    with pytest.raises(ValueError):
        client.getScaledImage(scaled_image2.image_id, scaled_image2.quality)
    with pytest.raises(ValueError):
        client.getScaledImage(scaled_image3.image_id, scaled_image3.quality)

def test_getScaledImage(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    scaled_image = ScaledImage(
        1, "2021-01-01T00:00:00", "Example_long_string", 90
    )
    client.addScaledImage(scaled_image)

    # Act
    response = client.getScaledImage(scaled_image.image_id, scaled_image.quality)

    # Assert
    assert response == scaled_image

def test_getScaledImage_error(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    scaled_image = ScaledImage(
        1, "2021-01-01T00:00:00", "Example_long_string", 90
    )

    # Act
    with pytest.raises(ValueError):
        client.getScaledImage(scaled_image.image_id, scaled_image.quality)

def test_listScaledImagesFromImage(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    scaled_image1 = ScaledImage(
        1, "2021-01-01T00:00:00", "Example_long_string", 90
    )
    scaled_image2 = ScaledImage(
        1, "2022-01-01T00:00:00", "Example_long_string", 80
    )
    scaled_image3 = ScaledImage(
        1, "2023-01-01T00:00:00", "Example_long_string", 70
    )
    client.addScaledImage(scaled_image1)
    client.addScaledImage(scaled_image2)
    client.addScaledImage(scaled_image3)

    # Act
    response = client.listScaledImagesFromImage(1)

    # Assert
    assert response == [scaled_image1, scaled_image2, scaled_image3]

def test_listScaledImages(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    scaled_image = ScaledImage(
        1, "2021-01-01T00:00:00", "Example_long_string", 90
    )
    client.addScaledImage(scaled_image)

    # Act
    response = client.listScaledImages()

    # Assert
    assert response == [scaled_image]
