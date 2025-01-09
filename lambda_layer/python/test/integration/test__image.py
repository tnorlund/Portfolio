from typing import Literal
import pytest
import boto3
from dynamo import Image, Line, Word, Letter, ScaledImage, DynamoClient


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
    scaled_image = ScaledImage(1, "2021-01-01T00:00:00", "Example_long_string", 90)
    client.addImage(image)
    client.addLine(line)
    client.addWord(word)
    client.addLetter(letter)
    client.addScaledImage(scaled_image)

    # Act
    retrieved_image, lines, words, letters, scaled_images = client.getImageDetails(
        image_id
    )

    # Assert
    assert retrieved_image == image
    assert lines == [line]
    assert words == [word]
    assert letters == [letter]
    assert scaled_images == [scaled_image]


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
    images, lek = client.listImages()  # Return type is (List[Image], Optional[Dict])

    # Assert
    assert image_1 in images
    assert image_2 in images
    # Because no limit was given, we expect all images and no pagination token
    assert lek is None


def test_listImages_pagination_returns_page_and_token(
    dynamodb_table: Literal["MyMockedTable"],
):
    """
    Verifies that when we provide a limit to listImages, it returns
    only 'limit' items plus a valid lastEvaluatedKey if there are more.
    """
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Create 5 images
    images_created = []
    for i in range(1, 6):
        img = Image(i, 100, 200, f"2021-01-0{i}T00:00:00", "bucket", f"key-{i}")
        client.addImage(img)
        images_created.append(img)

    # Act: Request only 2 items
    returned_images, lek = client.listImages(limit=2)

    # Assert
    assert len(returned_images) == 2, "Should only return 'limit' items"
    assert lek is not None, "Should return a lastEvaluatedKey since more items exist"

    # The images we got back should be a subset of the images_created
    for img in returned_images:
        assert img in images_created, f"Image {img.id} should be in the original set"


def test_listImages_pagination_uses_lastEvaluatedKey(
    dynamodb_table: Literal["MyMockedTable"],
):
    """
    Verifies that calling listImages again with the returned lastEvaluatedKey
    continues exactly where it left off.
    """
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Create 5 images
    images_created = []
    for i in range(1, 6):
        img = Image(i, 100, 200, f"2021-01-0{i}T00:00:00", "bucket", f"key-{i}")
        client.addImage(img)
        images_created.append(img)

    # Act: Request page 1 (limit=2)
    page_1_images, lek_1 = client.listImages(limit=2)
    # Request page 2 (limit=2), passing lastEvaluatedKey
    page_2_images, lek_2 = client.listImages(limit=2, last_evaluated_key=lek_1)
    # Request page 3 (limit=2), passing the second lastEvaluatedKey
    page_3_images, lek_3 = client.listImages(limit=2, last_evaluated_key=lek_2)

    # Assert
    # Page 1 should have exactly 2 items
    assert len(page_1_images) == 2
    assert lek_1 is not None

    # Page 2 should have exactly 2 items
    assert len(page_2_images) == 2
    assert lek_2 is not None

    # Page 3 should have exactly 1 item left (since total = 5)
    assert len(page_3_images) == 1
    # And no more items remain
    assert lek_3 is None

    # Combine all items from these pages
    all_returned = page_1_images + page_2_images + page_3_images
    # They should match the 5 images we inserted (in any order)
    assert sorted(all_returned, key=lambda x: x.id) == sorted(
        images_created, key=lambda x: x.id
    )


def test_listImages_pagination_no_limit_returns_all(
    dynamodb_table: Literal["MyMockedTable"],
):
    """
    Verifies that calling listImages() with limit=None (and no lastEvaluatedKey)
    preserves the old behavior of returning *all* images at once.
    """
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Create 3 images
    image_1 = Image(1, 10, 20, "2021-01-01T00:00:00", "bucket", "key1")
    image_2 = Image(2, 30, 40, "2021-01-01T00:00:00", "bucket", "key2")
    image_3 = Image(3, 50, 60, "2021-01-01T00:00:00", "bucket", "key3")

    client.addImage(image_1)
    client.addImage(image_2)
    client.addImage(image_3)

    # Act
    images, lek = client.listImages()  # No limit, no lastEvaluatedKey

    # Assert
    assert len(images) == 3, "Should return all images"
    assert lek is None, "No pagination token should be returned"

    for i in (image_1, image_2, image_3):
        assert i in images


def test_listImages_pagination_with_limit_exceeds_count(
    dynamodb_table: Literal["MyMockedTable"],
):
    """
    If we request a limit bigger than the total number of images,
    DynamoDB should return all images and no lastEvaluatedKey.
    """
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Create 2 images
    image_1 = Image(1, 10, 20, "2021-01-01T00:00:00", "bucket", "key1")
    image_2 = Image(2, 30, 40, "2021-01-01T00:00:00", "bucket", "key2")
    client.addImage(image_1)
    client.addImage(image_2)

    # Act: limit=10 but only 2 images in table
    images, lek = client.listImages(limit=10)

    # Assert
    assert len(images) == 2, "Should return all images if limit exceeds total count"
    assert lek is None, "Should not return a lastEvaluatedKey if no more pages remain"
    assert image_1 in images and image_2 in images


def test_listImages_pagination_empty_table(dynamodb_table: Literal["MyMockedTable"]):
    """
    If the table is empty, listImages should return an empty list and no token.
    """
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act
    images, lek = client.listImages(limit=5)

    # Assert
    assert images == [], "Should return empty list if no images exist"
    assert lek is None, "No next token if no images"
