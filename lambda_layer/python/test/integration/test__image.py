from typing import Literal
import pytest
import boto3
from dynamo import Image, Line, Word, Letter, DynamoClient


def test_add_image(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    image_id = 1
    image_width = 10
    image_height = 20
    timestamp_added = "2021-01-01T00:00:00"
    s3_bucket = "bucket"
    s3_key = "key"
    image = Image(
        image_id, image_width, image_height, timestamp_added, s3_bucket, s3_key
    )

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
    timestamp_added = "2021-01-01T00:00:00"
    s3_bucket = "bucket"
    s3_key = "key"
    image = Image(
        image_id, image_width, image_height, timestamp_added, s3_bucket, s3_key
    )

    # Act
    client.addImage(image)
    with pytest.raises(ValueError):
        client.addImage(image)


def test_get_image(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    image_id = 1
    image_width = 10
    image_height = 20
    timestamp_added = "2021-01-01T00:00:00"
    s3_bucket = "bucket"
    s3_key = "key"
    image = Image(
        image_id, image_width, image_height, timestamp_added, s3_bucket, s3_key
    )
    client.addImage(image)

    # Act
    retrieved_image = client.getImage(image_id)

    # Assert
    assert retrieved_image == image


def test_get_imageDetails(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    image_id = 1
    image = Image(1, 10, 20, "2021-01-01T00:00:00", "bucket", "key1")
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
    client.addImage(image)
    client.addLine(line)
    client.addWord(word)
    client.addLetter(letter)

    # Act
    retrieved_image, lines, words, letters = client.getImageDetails(image_id)

    # Assert
    assert retrieved_image == image
    assert lines == [line]
    assert words == [word]
    assert letters == [letter]


def test_deleteImage(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    image = Image(1, 10, 20, "2021-01-01T00:00:00", "bucket", "key1")
    client.addImage(image)

    # Act
    client.deleteImage(image.id)

    # Assert
    with pytest.raises(ValueError):
        client.getImage(1)


def test_deleteImage_error(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    image = Image(1, 10, 20, "2021-01-01T00:00:00", "bucket", "key1")

    # Act
    with pytest.raises(ValueError):
        client.deleteImage(image.id)


def test_listImages(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    image_1 = Image(1, 10, 20, "2021-01-01T00:00:00", "bucket", "key1")
    image_2 = Image(2, 30, 40, "2021-01-01T00:00:00", "bucket", "key2")
    client.addImage(image_1)
    client.addImage(image_2)

    # Act
    images = client.listImages()

    # Assert
    assert image_1 in images
    assert image_2 in images
