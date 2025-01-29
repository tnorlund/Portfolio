from typing import Literal
import pytest
import boto3
from dynamo import Image, Line, Word, Letter, Receipt, DynamoClient
from datetime import datetime
import json

correct_receipt_params = {
    "id": 1,
    "image_id": 1,
    "width": 10,
    "height": 20,
    "timestamp_added": datetime.now().isoformat(),
    "raw_s3_bucket": "bucket",
    "raw_s3_key": "key",
    "top_left": {"x": 0, "y": 0},
    "top_right": {"x": 10, "y": 0},
    "bottom_left": {"x": 0, "y": 20},
    "bottom_right": {"x": 10, "y": 20},
    "sha256": "sha256",
}

correct_line_params = {
    "id": 1,
    "image_id": 1,
    "text": "test",
    "bounding_box": {"x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0},
    "top_right": {"x": 0.0, "y": 0.0},
    "top_left": {"x": 0.0, "y": 0.0},
    "bottom_right": {"x": 0.0, "y": 0.0},
    "bottom_left": {"x": 0.0, "y": 0.0},
    "angle_degrees": 0,
    "angle_radians": 0,
    "confidence": 1,
}

correct_image_params = {
    "id": 1,
    "width": 10,
    "height": 20,
    "timestamp_added": datetime.now().isoformat(),
    "raw_s3_bucket": "bucket",
    "raw_s3_key": "key",
}


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
        "test_string",
        {
            "x": 0.4454263367632384,
            "height": 0.022867568134581906,
            "width": 0.08690182470506236,
            "y": 0.9167082878750482,
        },
        {"y": 0.9307722198001792, "x": 0.5323281614683008},
        {"y": 0.9395758560096301, "x": 0.44837726658954413},
        {"x": 0.529377231641995, "y": 0.9167082878750482},
        {"x": 0.4454263367632384, "y": 0.9255119240844992},
        -5.986527,
        -0.10448461,
        1,
    )
    word = Word(
        1,
        2,
        3,
        "test_string",
        {
            "y": 0.9167082878750482,
            "width": 0.08690182470506236,
            "x": 0.4454263367632384,
            "height": 0.022867568134581906,
        },
        {"y": 0.9307722198001792, "x": 0.5323281614683008},
        {"x": 0.44837726658954413, "y": 0.9395758560096301},
        {"y": 0.9167082878750482, "x": 0.529377231641995},
        {"x": 0.4454263367632384, "y": 0.9255119240844992},
        -5.986527,
        -0.10448461,
        1,
    )
    letter = Letter(
        1,
        1,
        1,
        1,
        "0",
        {
            "height": 0.022867568333804766,
            "width": 0.08688726243285705,
            "x": 0.4454336178993411,
            "y": 0.9167082877754368,
        },
        {"x": 0.5323208803321982, "y": 0.930772983660083},
        {"x": 0.44837726707985254, "y": 0.9395758561092415},
        {"x": 0.5293772311516867, "y": 0.9167082877754368},
        {"x": 0.4454336178993411, "y": 0.9255111602245953},
        -5.986527,
        -0.1044846,
        1,
    )
    client.addImage(image)
    client.addLine(line)
    client.addWord(word)
    client.addLetter(letter)

    # Act
    retrieved_image, lines, words, word_tags, letters, receipts = client.getImageDetails(
        image_id
    )

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


def test_deleteImages(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    images = [Image(**{**correct_image_params, "id": i}) for i in range(1, 1001)]
    client.addImages(images)

    # Act
    client.deleteImages(images)

    # Assert
    response_images = client.listImages()
    assert len(response_images) == 0, "all images should be deleted"


def test_listImageDetails(dynamodb_table: Literal["MyMockedTable"]):
    # Arrange
    client = DynamoClient(dynamodb_table)
    # Add multiple images
    images = [
        Image(**correct_image_params),
        Image(**{**correct_image_params, "id": 2}),
    ]
    client.addImages(images)
    # Add Lines with same image_ids
    lines_in_image_1 = [
        Line(**correct_line_params),
        Line(**{**correct_line_params, "id": 2}),
    ]
    lines_in_image_2 = [
        Line(**{**correct_line_params, "id": 1, "image_id": 2}),
        Line(**{**correct_line_params, "id": 2, "image_id": 2}),
        Line(**{**correct_line_params, "id": 3, "image_id": 2}),
    ]
    lines_different_image = [
        Line(**{**correct_line_params, "id": 4, "image_id": 3}),
    ]
    client.addLines(lines_in_image_1 + lines_in_image_2 + lines_different_image)
    receipts_in_image_1 = [
        Receipt(**correct_receipt_params),
        Receipt(**{**correct_receipt_params, "id": 2}),
    ]
    receipts_in_image_2 = [
        Receipt(**{**correct_receipt_params, "id": 1, "image_id": 2}),
        Receipt(**{**correct_receipt_params, "id": 2, "image_id": 2}),
        Receipt(**{**correct_receipt_params, "id": 3, "image_id": 2}),
    ]
    client.addReceipts(receipts_in_image_1 + receipts_in_image_2)
    receipts_different_image = [
        Receipt(**{**correct_receipt_params, "id": 4, "image_id": 3}),
    ]
    client.addReceipts(receipts_different_image)

    # Act
    payload, lek = (
        client.listImageDetails()
    )  # Return type is (List[Image], Optional[Dict])

    # Assert
    assert len(payload) == 2, "both images should be returned"
    assert lek is None, "No pagination token should be returned"
    assert all(key in payload[1].keys() for key in ["image", "lines", "receipts"])
    assert lines_in_image_1 == payload[1]["lines"]
    assert receipts_in_image_1 == payload[1]["receipts"]
    assert all(key in payload[2].keys() for key in ["image", "lines", "receipts"])
    assert lines_in_image_2 == payload[2]["lines"]
    assert receipts_in_image_2 == payload[2]["receipts"]


def test_listImageDetails_pagination_returns_page_and_token(
    dynamodb_table: Literal["MyMockedTable"],
):
    """
    Verifies that when we provide a limit to listImageDetails, it returns
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
    payload, lek = client.listImageDetails(limit=2)

    # Assert
    assert len(payload) == 2, "Should only return 'limit' items"
    assert lek is not None, "Should return a lastEvaluatedKey since more items exist"

    # The images we got back should be a subset of the images_created
    for image_index in payload:
        img = images_created[image_index - 1]
        assert img in images_created, f"Image {img.id} should be in the original set"


def test_listImageDetails_pagination_uses_lastEvaluatedKey(
    dynamodb_table: Literal["MyMockedTable"]
):
    """
    Verifies we can do classic pagination: 2 images on page 1, 2 on page 2,
    and 1 on page 3, with each call using the lastEvaluatedKey from the prior.
    """
    client = DynamoClient(dynamodb_table)

    # Create 5 images
    images_created = []
    for i in range(1, 6):
        img = Image(i, 100, 200, f"2021-01-0{i}T00:00:00", "bucket", f"key-{i}")
        client.addImage(img)
        images_created.append(img)

    # Page 1: limit=2
    page_1_payload, lek_1 = client.listImageDetails(limit=2)
    # Page 2: use lek_1
    page_2_payload, lek_2 = client.listImageDetails(limit=2, last_evaluated_key=lek_1)
    # Page 3: use lek_2
    page_3_payload, lek_3 = client.listImageDetails(limit=2, last_evaluated_key=lek_2)

    # Helper: extract the Image objects from the returned payload
    def extract_images(payload_dict):
        return [v["image"] for v in payload_dict.values() if "image" in v]

    # Assert Page 1
    page_1_images = extract_images(page_1_payload)
    assert len(page_1_images) == 2, "First page should have 2 images"
    assert lek_1 is not None, "First call should return a non-None LEK (since more data exist)"

    # Assert Page 2
    page_2_images = extract_images(page_2_payload)
    assert len(page_2_images) == 2, "Second page should have 2 images"
    assert lek_2 is not None, "Second call should also return a LEK (we still have 1 left)"

    # Assert Page 3
    page_3_images = extract_images(page_3_payload)
    assert len(page_3_images) == 1, "Third page should have the last remaining image"
    assert lek_3 is None, "No more images left, so LEK should be None"

    # Confirm we got all 5 images in total
    all_images = page_1_images + page_2_images + page_3_images
    assert sorted(all_images, key=lambda x: x.id) == sorted(images_created, key=lambda x: x.id)


def test_listImageDetails_pagination_no_limit_returns_all(
    dynamodb_table: Literal["MyMockedTable"],
):
    """
    Verifies that calling listImageDetails() with limit=None (and no lastEvaluatedKey)
    returns *all* images at once.
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
    # listImageDetails() now returns (dict, lek), not (list, lek)
    payload, lek = client.listImageDetails()  # No limit, no lastEvaluatedKey

    # Flatten the dictionary to a list of Image objects
    def extract_images(payload_dict):
        return [v["image"] for v in payload_dict.values() if "image" in v]

    returned_images = extract_images(payload)

    # Assert
    assert len(returned_images) == 3, "Should return all images"
    assert lek is None, "No pagination token should be returned if we got everything"

    # Ensure our newly-added images are in the returned set
    for i in (image_1, image_2, image_3):
        assert i in returned_images


def test_listImageDetails_pagination_with_limit_exceeds_count(
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

    # Act: limit=10 but only 2 images in the table
    payload, lek = client.listImageDetails(limit=10)

    # Flatten the dictionary to a list of Image objects
    def extract_images(payload_dict):
        return [v["image"] for v in payload_dict.values() if "image" in v]

    returned_images = extract_images(payload)

    # Assert
    assert (
        len(returned_images) == 2
    ), "Should return all images if limit exceeds total count"
    assert lek is None, "Should not return a lastEvaluatedKey if no more pages remain"

    # Ensure our 2 newly-added images are in the returned set
    assert image_1 in returned_images
    assert image_2 in returned_images


def test_listImageDetails_pagination_empty_table(
    dynamodb_table: Literal["MyMockedTable"],
):
    """
    If the table is empty, listImageDetails should return an empty dict and no token.
    """
    # Arrange
    client = DynamoClient(dynamodb_table)

    # Act
    payload, lek = client.listImageDetails(limit=5)

    # Assert
    # Check that 'payload' is an empty dict
    assert len(payload) == 0, "Should return empty dictionary if no images exist"
    assert lek is None, "No next token if no images"


def test_listImageDetails_lek_structure_and_usage(
    dynamodb_table: Literal["MyMockedTable"]
):
    """
    Demonstrates that the LEK (LastEvaluatedKey) we get back:
      1) Has the correct shape for a DynamoDB key (PK, SK, GSI1PK, GSI1SK)
      2) Actually works when used in subsequent calls
    """
    client = DynamoClient(dynamodb_table)

    # Create 5 images
    images_created = []
    for i in range(1, 6):
        img = Image(i, 100, 200, f"2021-01-0{i}T00:00:00", "bucket", f"key-{i}")
        client.addImage(img)
        images_created.append(img)

    # 1) Page 1: limit=2
    payload_1, lek_1 = client.listImageDetails(limit=2)
    assert len(payload_1) == 2, "Page 1 should contain 2 images"
    assert lek_1 is not None, "Should return a valid LastEvaluatedKey from page 1"

    # Confirm it includes PK, SK, GSI1PK, GSI1SK
    assert isinstance(lek_1, dict), "LEK should be a dictionary"
    for key in ("PK", "SK", "GSI1PK", "GSI1SK"):
        assert key in lek_1, f"LEK dictionary should contain {key}"

    # 2) Page 2: use lek_1
    payload_2, lek_2 = client.listImageDetails(limit=2, last_evaluated_key=lek_1)
    assert len(payload_2) == 2, "Page 2 should return the next 2 images"
    assert lek_2 is not None, "We still have a third image left"

    # 3) Page 3: use lek_2
    payload_3, lek_3 = client.listImageDetails(limit=2, last_evaluated_key=lek_2)
    assert len(payload_3) == 1, "Page 3 should have the last remaining image"
    assert lek_3 is None, "No more pages expected"

    # Gather all images and compare
    def extract_images(payload_dict):
        return [v["image"] for v in payload_dict.values() if "image" in v]

    all_returned = (
        extract_images(payload_1)
        + extract_images(payload_2)
        + extract_images(payload_3)
    )
    assert sorted(all_returned, key=lambda x: x.id) == sorted(
        images_created, key=lambda x: x.id
    ), "We should retrieve all 5 images in total."
