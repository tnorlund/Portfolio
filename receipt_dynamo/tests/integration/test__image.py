# infra/lambda_layer/python/test/integration/test__image.py
from datetime import datetime
from uuid import uuid4

import boto3
import pytest
from botocore.exceptions import ClientError

from receipt_dynamo import DynamoClient, Image, Letter, Line, Receipt, Word


@pytest.fixture
def example_image():
    """
    Provides a sample Image for testing.

    Note: This image uses a fixed ID so that tests relying on that value
    (such as get or delete) work as expected.
    """
    return Image("3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        10,
        20,
        "2021-01-01T00:00:00",
        "bucket",
        "key", )


@pytest.mark.integration
def test_addImage_raises_value_error_for_none_image(dynamodb_table):
    """
    Integration test that checks addImage raises ValueError when 'image' is None.
    We rely on the 'dynamodb_table' fixture to create a table so that
    DynamoClient constructor won't raise an error about the missing table.
    """
    # Use the real table name from the fixture
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="Image parameter is required"):
        client.addImage(None)


@pytest.mark.integration
def test_addImage_raises_value_error_for_invalid_type(dynamodb_table):
    """
    Checks addImage with an invalid type for 'image'.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="image must be an instance"):
        client.addImage("not-an-image")


@pytest.mark.integration
def test_addImage_raises_conditional_check_failed(dynamodb_table, example_image, mocker):
    """
    Tests that addImage raises ValueError when a ConditionalCheckFailedException
    occurs (image already exists).
    """
    client = DynamoClient(dynamodb_table)

    # Patch the client's put_item to raise the ClientError
    mock_put = mocker.patch.object(client._client,
        "put_item",
        side_effect=ClientError({"Error": {"Code": "ConditionalCheckFailedException",
                    "Message": "The conditional request failed", }},
            "PutItem", ), )

    with pytest.raises(ValueError, match="already exists"):
        client.addImage(example_image)

    mock_put.assert_called_once()


@pytest.mark.integration
def test_addImage_raises_provisioned_throughput(dynamodb_table, example_image, mocker):
    """
    Tests that addImage raises an Exception with a message indicating that the
    provisioned throughput was exceeded when the DynamoDB put_item call returns a
    ProvisionedThroughputExceededException.
    """
    client = DynamoClient(dynamodb_table)

    # Patch the client's put_item to raise the ClientError
    mock_put = mocker.patch.object(client._client,
        "put_item",
        side_effect=ClientError({"Error": {"Code": "ProvisionedThroughputExceededException",
                    "Message": "Provisioned throughput exceeded", }},
            "PutItem", ), )

    with pytest.raises(Exception, match="Provisioned throughput exceeded"):
        client.addImage(example_image)

    mock_put.assert_called_once()


@pytest.mark.integration
def test_addImage_raises_internal_server_error(dynamodb_table, example_image, mocker):
    """
    Tests that addImage raises an Exception with a message indicating that an internal server error occurred,
    when the DynamoDB put_item call returns an InternalServerError.
    """
    client = DynamoClient(dynamodb_table)

    # Patch the client's put_item to raise a ClientError with code
    # "InternalServerError"
    mock_put = mocker.patch.object(client._client,
        "put_item",
        side_effect=ClientError({"Error": {"Code": "InternalServerError",
                    "Message": "Internal server error", }},
            "PutItem", ), )

    with pytest.raises(Exception, match="Internal server error:"):
        client.addImage(example_image)

    mock_put.assert_called_once()


@pytest.mark.integration
def test_addImage_raises_unknown_exception(dynamodb_table, example_image, mocker):
    """
    Tests that addImage raises a generic Exception with a message indicating an error putting the image,
    when the DynamoDB put_item call returns a ClientError with an unhandled error code.
    """
    client = DynamoClient(dynamodb_table)

    # Patch the client's put_item to raise a ClientError with an unknown error
    # code.
    mock_put = mocker.patch.object(client._client,
        "put_item",
        side_effect=ClientError({"Error": {"Code": "UnknownException",
                    "Message": "An unknown error occurred", }},
            "PutItem", ), )

    with pytest.raises(Exception, match="Error putting image:"):
        client.addImage(example_image)

    mock_put.assert_called_once()


@pytest.mark.integration
def test_addImage(dynamodb_table, example_image):
    """
    Verifies a successful addImage call actually persists data to DynamoDB.
    """
    client = DynamoClient(dynamodb_table)
    client.addImage(example_image)

    # Immediately call getImage after adding to prove integration.
    retrieved_image = client.getImage(example_image.image_id)
    assert (retrieved_image == example_image), "Retrieved image should match the image just added."

    # Also, do a direct DynamoDB check (this was in your original test).
    response = boto3.client("dynamodb", region_name="us-east-1").get_item(TableName=dynamodb_table,
        Key={"PK": {"S": f"IMAGE#{example_image.image_id}"},
            "SK": {"S": "IMAGE"}, }, )
    assert (response["Item"] == example_image.to_item()), "Direct DynamoDB check for item mismatch."


@pytest.mark.integration
def test_getImage(dynamodb_table, example_image):
    """
    This test focuses on retrieving an image, but also proves integration by
    adding the image first (so there's actually something to get).
    """
    client = DynamoClient(dynamodb_table)

    # We must add the image so there's something to retrieve.
    client.addImage(example_image)

    # Now specifically test getImage.
    retrieved_image = client.getImage(example_image.image_id)
    assert (retrieved_image == example_image), "The image retrieved via getImage should match the one added."


@pytest.mark.integration
def test_image_get_details(dynamodb_table, example_image):
    client = DynamoClient(dynamodb_table)
    image = example_image

    # Create sample Line, Word, and Letter objects that belong to this image.
    line = Line(image.image_id,
        1,
        "test_string",
        {"x": 0.4454263367632384,
            "height": 0.022867568134581906,
            "width": 0.08690182470506236,
            "y": 0.9167082878750482, },
        {"y": 0.9307722198001792, "x": 0.5323281614683008},
        {"y": 0.9395758560096301, "x": 0.44837726658954413},
        {"x": 0.529377231641995, "y": 0.9167082878750482},
        {"x": 0.4454263367632384, "y": 0.9255119240844992},
        -5.986527,
        -0.10448461,
        1, )
    word = Word(image.image_id,
        2,
        3,
        "test_string",
        {"y": 0.9167082878750482,
            "width": 0.08690182470506236,
            "x": 0.4454263367632384,
            "height": 0.022867568134581906, },
        {"y": 0.9307722198001792, "x": 0.5323281614683008},
        {"x": 0.44837726658954413, "y": 0.9395758560096301},
        {"y": 0.9167082878750482, "x": 0.529377231641995},
        {"x": 0.4454263367632384, "y": 0.9255119240844992},
        -5.986527,
        -0.10448461,
        1, )
    letter = Letter(image.image_id,
        1,
        1,
        1,
        "0",
        {"height": 0.022867568333804766,
            "width": 0.08688726243285705,
            "x": 0.4454336178993411,
            "y": 0.9167082877754368, },
        {"x": 0.5323208803321982, "y": 0.930772983660083},
        {"x": 0.44837726707985254, "y": 0.9395758561092415},
        {"x": 0.5293772311516867, "y": 0.9167082877754368},
        {"x": 0.4454336178993411, "y": 0.9255111602245953},
        -5.986527,
        -0.1044846,
        1, )

    client.addImage(image)
    client.addLine(line)
    client.addWord(word)
    client.addLetter(letter)

    (images,
        lines,
        words,
        word_tags,
        letters,
        receipts,
        receipt_windows,
        receipt_lines,
        receipt_words,
        receipt_word_tags,
        receipt_letters,
        gpt_initial_taggings,
        gpt_validations, ) = client.getImageDetails(image.image_id)
    retrieved_image = images[0]
    assert retrieved_image == image
    assert lines == [line]
    assert words == [word]
    assert letters == [letter]


@pytest.mark.integration
def test_image_delete(dynamodb_table, example_image):
    client = DynamoClient(dynamodb_table)
    client.addImage(example_image)
    client.deleteImage(example_image.image_id)
    with pytest.raises(ValueError):
        client.getImage(example_image.image_id)


@pytest.mark.integration
def test_image_delete_error(dynamodb_table, example_image):
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError):
        client.deleteImage(example_image.image_id)


@pytest.mark.integration
def test_image_delete_all(dynamodb_table):
    client = DynamoClient(dynamodb_table)
    correct_image_params = {"width": 10,
        "height": 20,
        "timestamp_added": "2021-01-01T00:00:00",
        "raw_s3_bucket": "bucket",
        "raw_s3_key": "key", }
    # Generate 1000 images with random UUIDs.
    images = [Image(str(uuid4()), **correct_image_params) for _ in range(1000)]
    client.addImages(images)
    client.deleteImages(images)
    response_images, _ = client.listImages()
    assert len(response_images) == 0, "all images should be deleted"


@pytest.mark.integration
def test_image_list_details(dynamodb_table):
    client = DynamoClient(dynamodb_table)
    correct_image_params = {"width": 10,
        "height": 20,
        "timestamp_added": "2021-01-01T00:00:00",
        "raw_s3_bucket": "bucket",
        "raw_s3_key": "key", }
    correct_line_params = {"text": "test",
        "bounding_box": {"x": 0.0, "y": 0.0, "width": 0.0, "height": 0.0},
        "top_right": {"x": 0.0, "y": 0.0},
        "top_left": {"x": 0.0, "y": 0.0},
        "bottom_right": {"x": 0.0, "y": 0.0},
        "bottom_left": {"x": 0.0, "y": 0.0},
        "angle_degrees": 0,
        "angle_radians": 0,
        "confidence": 1, }
    correct_receipt_params = {"width": 10,
        "height": 20,
        "timestamp_added": datetime.now().isoformat(),
        "raw_s3_bucket": "bucket",
        "raw_s3_key": "key",
        "top_left": {"x": 0, "y": 0},
        "top_right": {"x": 10, "y": 0},
        "bottom_left": {"x": 0, "y": 20},
        "bottom_right": {"x": 10, "y": 20},
        "sha256": "sha256", }

    image_id_1 = str(uuid4())
    image_id_2 = str(uuid4())
    image_id_3 = str(uuid4())  # a "different" image with no Image object

    images = [Image(image_id_1, **correct_image_params),
        Image(image_id_2, **correct_image_params), ]
    client.addImages(images)

    lines_in_image_1 = [Line(image_id_1, 1, **correct_line_params),
        Line(image_id_1, 2, **correct_line_params), ]
    lines_in_image_2 = [Line(image_id_2, 1, **correct_line_params),
        Line(image_id_2, 2, **correct_line_params),
        Line(image_id_2, 3, **correct_line_params), ]
    lines_different_image = [Line(image_id_3, 4, **correct_line_params), ]
    client.addLines(lines_in_image_1 + lines_in_image_2 + lines_different_image)

    receipts_in_image_1 = [Receipt(image_id_1, **correct_receipt_params, receipt_id=1),
        Receipt(image_id_1, **correct_receipt_params, receipt_id=2), ]
    receipts_in_image_2 = [Receipt(image_id_2, **correct_receipt_params, receipt_id=1),
        Receipt(image_id_2, **correct_receipt_params, receipt_id=2),
        Receipt(image_id_2, **correct_receipt_params, receipt_id=3), ]
    receipts_different_image = [Receipt(image_id_3, **correct_receipt_params, receipt_id=4), ]
    client.addReceipts(receipts_in_image_1 + receipts_in_image_2)
    client.addReceipts(receipts_different_image)

    payload, lek = client.listImageDetails()
    assert len(payload) == 2, "both images should be returned"
    assert lek is None, "No pagination token should be returned"
    image_1_details = payload[image_id_1]
    image_2_details = payload[image_id_2]
    assert all(k in image_1_details for k in ["image", "lines", "receipts"])
    assert lines_in_image_1 == image_1_details["lines"]
    assert receipts_in_image_1 == image_1_details["receipts"]
    assert all(k in image_2_details for k in ["image", "lines", "receipts"])
    assert lines_in_image_2 == image_2_details["lines"]
    assert receipts_in_image_2 == image_2_details["receipts"]


@pytest.mark.integration
def test_listImageDetails_pagination_returns_page_and_token(dynamodb_table):
    client = DynamoClient(dynamodb_table)
    images_created = []
    uuids = [str(uuid4()) for _ in range(6)]
    for i in range(1, 6):
        img = Image(uuids[i], 100, 200, f"2021-01-0{i}T00:00:00", "bucket", f"key-{i}")
        client.addImage(img)
        images_created.append(img)
    payload, lek = client.listImageDetails(limit=2)
    assert len(payload) == 2, "Should only return 'limit' items"
    assert (lek is not None), "Should return a lastEvaluatedKey since more items exist"
    # Ensure the returned images are among those created.
    for img in images_created:
        assert (img in images_created), f"Image {img.image_id} should be in the original set"


@pytest.mark.integration
def test_listImageDetails_pagination_uses_lastEvaluatedKey(dynamodb_table):
    client = DynamoClient(dynamodb_table)
    images_created = []
    uuids = [str(uuid4()) for _ in range(6)]
    for i in range(1, 6):
        img = Image(uuids[i], 100, 200, f"2021-01-0{i}T00:00:00", "bucket", f"key-{i}")
        client.addImage(img)
        images_created.append(img)
    page_1_payload, lek_1 = client.listImageDetails(limit=2)
    page_2_payload, lek_2 = client.listImageDetails(limit=2, last_evaluated_key=lek_1)
    page_3_payload, lek_3 = client.listImageDetails(limit=2, last_evaluated_key=lek_2)

    def extract_images(payload_dict):
        return [v["image"] for v in payload_dict.values() if "image" in v]

    page_1_images = extract_images(page_1_payload)
    assert len(page_1_images) == 2, "First page should have 2 images"
    assert lek_1 is not None, "First call should return a non-None LEK"

    page_2_images = extract_images(page_2_payload)
    assert len(page_2_images) == 2, "Second page should have 2 images"
    assert lek_2 is not None, "Second call should return a LEK"

    page_3_images = extract_images(page_3_payload)
    assert (len(page_3_images) == 1), "Third page should have the last remaining image"
    assert lek_3 is None, "No more images left, so LEK should be None"

    all_images = page_1_images + page_2_images + page_3_images
    assert sorted(all_images, key=lambda x: x.image_id) == sorted(images_created, key=lambda x: x.image_id)


@pytest.mark.integration
def test_listImageDetails_pagination_no_limit_returns_all(dynamodb_table):
    client = DynamoClient(dynamodb_table)
    image_1 = Image("3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        10,
        20,
        "2021-01-01T00:00:00",
        "bucket",
        "key1", )
    image_2 = Image("3f52804b-2fad-4e00-92c8-b593da3a8ed4",
        30,
        40,
        "2021-01-01T00:00:00",
        "bucket",
        "key2", )
    image_3 = Image("3f52804b-2fad-4e00-92c8-b593da3a8ed5",
        50,
        60,
        "2021-01-01T00:00:00",
        "bucket",
        "key3", )
    client.addImage(image_1)
    client.addImage(image_2)
    client.addImage(image_3)

    payload, lek = client.listImageDetails()

    def extract_images(payload_dict):
        return [v["image"] for v in payload_dict.values() if "image" in v]

    returned_images = extract_images(payload)
    assert len(returned_images) == 3, "Should return all images"
    assert (lek is None), "No pagination token should be returned if we got everything"
    for img in (image_1, image_2, image_3):
        assert img in returned_images


@pytest.mark.integration
def test_listImageDetails_pagination_with_limit_exceeds_count(dynamodb_table):
    client = DynamoClient(dynamodb_table)
    image_1 = Image("3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        10,
        20,
        "2021-01-01T00:00:00",
        "bucket",
        "key1", )
    image_2 = Image("3f52804b-2fad-4e00-92c8-b593da3a8ed2",
        30,
        40,
        "2021-01-01T00:00:00",
        "bucket",
        "key2", )
    client.addImage(image_1)
    client.addImage(image_2)

    payload, lek = client.listImageDetails(limit=10)

    def extract_images(payload_dict):
        return [v["image"] for v in payload_dict.values() if "image" in v]

    returned_images = extract_images(payload)
    assert (len(returned_images) == 2), "Should return all images if limit exceeds total count"
    assert (lek is None), "Should not return a lastEvaluatedKey if no more pages remain"
    assert image_1 in returned_images
    assert image_2 in returned_images


@pytest.mark.integration
def test_listImageDetails_pagination_empty_table(dynamodb_table):
    client = DynamoClient(dynamodb_table)
    payload, lek = client.listImageDetails(limit=5)
    assert (len(payload) == 0), "Should return empty dictionary if no images exist"
    assert lek is None, "No next token if no images"


@pytest.mark.integration
def test_listImageDetails_lek_structure_and_usage(dynamodb_table):
    client = DynamoClient(dynamodb_table)
    images_created = []
    uuids = [str(uuid4()) for _ in range(6)]
    for i in range(1, 6):
        img = Image(uuids[i], 100, 200, f"2021-01-0{i}T00:00:00", "bucket", f"key-{i}")
        client.addImage(img)
        images_created.append(img)

    payload_1, lek_1 = client.listImageDetails(limit=2)
    assert len(payload_1) == 2, "Page 1 should contain 2 images"
    assert (lek_1 is not None), "Should return a valid LastEvaluatedKey from page 1"
    assert isinstance(lek_1, dict), "LEK should be a dictionary"
    for key in ("PK", "SK", "GSI1PK", "GSI1SK"):
        assert key in lek_1, f"LEK dictionary should contain {key}"

    payload_2, lek_2 = client.listImageDetails(limit=2, last_evaluated_key=lek_1)
    assert len(payload_2) == 2, "Page 2 should return the next 2 images"
    assert lek_2 is not None, "We still have a third image left"

    payload_3, lek_3 = client.listImageDetails(limit=2, last_evaluated_key=lek_2)
    assert len(payload_3) == 1, "Page 3 should have the last remaining image"
    assert lek_3 is None, "No more pages expected"

    def extract_images(payload_dict):
        return [v["image"] for v in payload_dict.values() if "image" in v]

    all_returned = (extract_images(payload_1)
        + extract_images(payload_2)
        + extract_images(payload_3))
    assert sorted(all_returned, key=lambda x: x.image_id) == sorted(images_created, key=lambda x: x.image_id)


@pytest.mark.integration
def test_updateImages_success(dynamodb_table, example_image):
    """
    Tests happy path for updateImages.
    """
    client = DynamoClient(dynamodb_table)
    img1 = example_image
    img2 = Image(str(uuid4()),
        10,
        20,
        "2021-01-01T00:00:00",
        "bucket",
        "key2", )
    client.addImages([img1, img2])

    # Now update them
    img1.raw_s3_key = "updated/path/1"
    img2.raw_s3_key = "updated/path/2"
    client.updateImages([img1, img2])

    # Verify updates
    stored_images, _ = client.listImages()
    assert len(stored_images) == 2
    # Confirm the updated s3_keys
    for img in stored_images:
        if img.image_id == img1.image_id:
            assert img.raw_s3_key == "updated/path/1"
        else:
            assert img.raw_s3_key == "updated/path/2"


@pytest.mark.integration
def test_updateImages_raises_value_error_images_none(dynamodb_table, example_image):
    """
    Tests that updateImages raises ValueError when the images parameter is None.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="Images parameter is required and cannot be None."):
        client.updateImages(None)  # type: ignore


@pytest.mark.integration
def test_updateImages_raises_value_error_images_not_list(dynamodb_table, example_image):
    """
    Tests that updateImages raises ValueError when the images parameter is not a list.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="Images must be provided as a list."):
        client.updateImages("not-a-list")  # type: ignore


@pytest.mark.integration
def test_updateImages_raises_value_error_images_not_list_of_images(dynamodb_table, example_image):
    """
    Tests that updateImages raises ValueError when the images parameter is not a list of Image instances.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError,
        match="All items in the images list must be instances of the Image class.", ):
        client.updateImages([example_image, "not-an-image"])  # type: ignore


@pytest.mark.integration
def test_updateImages_raises_clienterror_conditional_check_failed(dynamodb_table, example_image, mocker):
    """
    Tests that updateImages raises an Exception when the ConditionalCheckFailedException error is raised.
    """
    client = DynamoClient(dynamodb_table)
    mock_transact = mocker.patch.object(client._client,
        "transact_write_items",
        side_effect=ClientError({"Error": {"Code": "ConditionalCheckFailedException",
                    "Message": "One or more images do not exist", }},
            "TransactWriteItems", ), )
    with pytest.raises(ValueError, match="One or more images do not exist"):
        client.updateImages([example_image])
    mock_transact.assert_called_once()


@pytest.mark.integration
def test_updateImages_raises_clienterror_provisioned_throughput_exceeded(dynamodb_table, example_image, mocker):
    """
    Tests that updateImages raises an Exception when the ProvisionedThroughputExceededException error is raised.
    """
    client = DynamoClient(dynamodb_table)
    mock_transact = mocker.patch.object(client._client,
        "transact_write_items",
        side_effect=ClientError({"Error": {"Code": "ProvisionedThroughputExceededException",
                    "Message": "Provisioned throughput exceeded", }},
            "TransactWriteItems", ), )
    with pytest.raises(Exception, match="Provisioned throughput exceeded"):
        client.updateImages([example_image])
    mock_transact.assert_called_once()


@pytest.mark.integration
def test_updateImages_raises_clienterror_internal_server_error(dynamodb_table, example_image, mocker):
    """
    Tests that updateImages raises an Exception when the InternalServerError error is raised.
    """
    client = DynamoClient(dynamodb_table)
    mock_transact = mocker.patch.object(client._client,
        "transact_write_items",
        side_effect=ClientError({"Error": {"Code": "InternalServerError",
                    "Message": "Internal server error", }},
            "TransactWriteItems", ), )
    with pytest.raises(Exception, match="Internal server error"):
        client.updateImages([example_image])
    mock_transact.assert_called_once()


@pytest.mark.integration
def test_updateImages_raises_clienterror_validation_exception(dynamodb_table, example_image, mocker):
    """
    Tests that updateImages raises an Exception when the ValidationException error is raised.
    """
    client = DynamoClient(dynamodb_table)
    mock_transact = mocker.patch.object(client._client,
        "transact_write_items",
        side_effect=ClientError({"Error": {"Code": "ValidationException",
                    "Message": "One or more parameters given were invalid", }},
            "TransactWriteItems", ), )
    with pytest.raises(Exception, match="One or more parameters given were invalid"):
        client.updateImages([example_image])
    mock_transact.assert_called_once()


@pytest.mark.integration
def test_updateImages_raises_clienterror_access_denied(dynamodb_table, example_image, mocker):
    """
    Tests that updateImages raises an Exception when the AccessDeniedException error is raised.
    """
    client = DynamoClient(dynamodb_table)
    mock_transact = mocker.patch.object(client._client,
        "transact_write_items",
        side_effect=ClientError({"Error": {"Code": "AccessDeniedException",
                    "Message": "Access denied", }},
            "TransactWriteItems", ), )
    with pytest.raises(Exception, match="Access denied"):
        client.updateImages([example_image])
    mock_transact.assert_called_once()


@pytest.mark.integration
def test_updateImages_raises_client_error(dynamodb_table, example_image, mocker):
    """
    Simulate any error (ResourceNotFound, etc.) in transact_write_items.
    """
    client = DynamoClient(dynamodb_table)
    mock_transact = mocker.patch.object(client._client,
        "transact_write_items",
        side_effect=ClientError({"Error": {"Code": "ResourceNotFoundException",
                    "Message": "No table found", }},
            "TransactWriteItems", ), )

    with pytest.raises(ValueError, match="Error updating images"):
        client.updateImages([example_image])

    mock_transact.assert_called_once()
