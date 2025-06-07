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
    return Image(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        10,
        20,
        "2021-01-01T00:00:00",
        "bucket",
        "key",
    )


@pytest.mark.integration
def test_addImage_raises_value_error_for_none_image(dynamodb_table):
    """
    Integration test that checks addImage raises ValueError when 'image' is
    None. We rely on the 'dynamodb_table' fixture to create a table so that
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
def test_addImage_raises_conditional_check_failed(
    dynamodb_table, example_image, mocker
):
    """
    Tests that addImage raises ValueError when a
    ConditionalCheckFailedException occurs (image already exists).
    """
    client = DynamoClient(dynamodb_table)

    # Patch the client's put_item to raise the ClientError
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ConditionalCheckFailedException",
                    "Message": "The conditional request failed",
                }
            },
            "PutItem",
        ),
    )

    with pytest.raises(ValueError, match="already exists"):
        client.addImage(example_image)

    mock_put.assert_called_once()


@pytest.mark.integration
def test_addImage_raises_provisioned_throughput(
    dynamodb_table, example_image, mocker
):
    """
    Tests that addImage raises an Exception with a message indicating that the
    provisioned throughput was exceeded when the DynamoDB put_item call
    returns a ProvisionedThroughputExceededException.
    """
    client = DynamoClient(dynamodb_table)

    # Patch the client's put_item to raise the ClientError
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ProvisionedThroughputExceededException",
                    "Message": "Provisioned throughput exceeded",
                }
            },
            "PutItem",
        ),
    )

    with pytest.raises(Exception, match="Provisioned throughput exceeded"):
        client.addImage(example_image)

    mock_put.assert_called_once()


@pytest.mark.integration
def test_addImage_raises_internal_server_error(
    dynamodb_table, example_image, mocker
):
    """
    Tests that addImage raises an Exception with a message indicating that an
    internal server error occurred, when the DynamoDB put_item call returns an
    InternalServerError.
    """
    client = DynamoClient(dynamodb_table)

    # Patch the client's put_item to raise a ClientError with code
    # "InternalServerError"
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "InternalServerError",
                    "Message": "Internal server error",
                }
            },
            "PutItem",
        ),
    )

    with pytest.raises(Exception, match="Internal server error:"):
        client.addImage(example_image)

    mock_put.assert_called_once()


@pytest.mark.integration
def test_addImage_raises_unknown_exception(
    dynamodb_table, example_image, mocker
):
    """
    Tests that addImage raises a generic Exception with a message indicating
    an error putting the image, when the DynamoDB put_item call returns a
    ClientError with an unhandled error code.
    """
    client = DynamoClient(dynamodb_table)

    # Patch the client's put_item to raise a ClientError with an unknown error
    # code.
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "UnknownException",
                    "Message": "An unknown error occurred",
                }
            },
            "PutItem",
        ),
    )

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
    assert (
        retrieved_image == example_image
    ), "Retrieved image should match the image just added."

    # Also, do a direct DynamoDB check (this was in your original test).
    response = boto3.client("dynamodb", region_name="us-east-1").get_item(
        TableName=dynamodb_table,
        Key={
            "PK": {"S": f"IMAGE#{example_image.image_id}"},
            "SK": {"S": "IMAGE"},
        },
    )
    assert (
        response["Item"] == example_image.to_item()
    ), "Direct DynamoDB check for item mismatch."


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
    assert (
        retrieved_image == example_image
    ), "The image retrieved via getImage should match the one added."


@pytest.mark.integration
def test_image_get_details(dynamodb_table, example_image):
    client = DynamoClient(dynamodb_table)
    image = example_image

    # Create sample Line, Word, and Letter objects that belong to this image.
    line = Line(
        image.image_id,
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
        image.image_id,
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
        image.image_id,
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

    (
        images,
        lines,
        words,
        word_tags,
        letters,
        receipts,
        receipt_lines,
        receipt_words,
        receipt_word_tags,
        receipt_letters,
    ) = client.getImageDetails(image.image_id)
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
    correct_image_params = {
        "width": 10,
        "height": 20,
        "timestamp_added": "2021-01-01T00:00:00",
        "raw_s3_bucket": "bucket",
        "raw_s3_key": "key",
    }
    # Generate 1000 images with random UUIDs.
    images = [Image(str(uuid4()), **correct_image_params) for _ in range(1000)]
    client.addImages(images)
    client.deleteImages(images)
    response_images, _ = client.listImages()
    assert len(response_images) == 0, "all images should be deleted"


@pytest.mark.integration
def test_updateImages_success(dynamodb_table, example_image):
    """
    Tests happy path for updateImages.
    """
    client = DynamoClient(dynamodb_table)
    img1 = example_image
    img2 = Image(
        str(uuid4()),
        10,
        20,
        "2021-01-01T00:00:00",
        "bucket",
        "key2",
    )
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
def test_updateImages_raises_value_error_images_none(
    dynamodb_table, example_image
):
    """
    Tests that updateImages raises ValueError when the images parameter is
    None.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        ValueError, match="Images parameter is required and cannot be None."
    ):
        client.updateImages(None)  # type: ignore


@pytest.mark.integration
def test_updateImages_raises_value_error_images_not_list(
    dynamodb_table, example_image
):
    """
    Tests that updateImages raises ValueError when the images parameter is not
    a list.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="Images must be provided as a list."):
        client.updateImages("not-a-list")  # type: ignore


@pytest.mark.integration
def test_updateImages_raises_value_error_images_not_list_of_images(
    dynamodb_table, example_image
):
    """
    Tests that updateImages raises ValueError when the images parameter is not
    a list of Image instances.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        ValueError,
        match=(
            "All items in the images list must be instances of the "
            "Image class."
        ),
    ):
        client.updateImages([example_image, "not-an-image"])  # type: ignore


@pytest.mark.integration
def test_updateImages_raises_clienterror_conditional_check_failed(
    dynamodb_table, example_image, mocker
):
    """
    Tests that updateImages raises an Exception when the
    ConditionalCheckFailedException error is raised.
    """
    client = DynamoClient(dynamodb_table)
    mock_transact = mocker.patch.object(
        client._client,
        "transact_write_items",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ConditionalCheckFailedException",
                    "Message": "One or more images do not exist",
                }
            },
            "TransactWriteItems",
        ),
    )
    with pytest.raises(ValueError, match="One or more images do not exist"):
        client.updateImages([example_image])
    mock_transact.assert_called_once()


@pytest.mark.integration
def test_updateImages_raises_clienterror_provisioned_throughput_exceeded(
    dynamodb_table, example_image, mocker
):
    """
    Tests that updateImages raises an Exception when the
    ProvisionedThroughputExceededException error is raised.
    """
    client = DynamoClient(dynamodb_table)
    mock_transact = mocker.patch.object(
        client._client,
        "transact_write_items",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ProvisionedThroughputExceededException",
                    "Message": "Provisioned throughput exceeded",
                }
            },
            "TransactWriteItems",
        ),
    )
    with pytest.raises(Exception, match="Provisioned throughput exceeded"):
        client.updateImages([example_image])
    mock_transact.assert_called_once()


@pytest.mark.integration
def test_updateImages_raises_clienterror_internal_server_error(
    dynamodb_table, example_image, mocker
):
    """
    Tests that updateImages raises an Exception when the InternalServerError
    error is raised.
    """
    client = DynamoClient(dynamodb_table)
    mock_transact = mocker.patch.object(
        client._client,
        "transact_write_items",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "InternalServerError",
                    "Message": "Internal server error",
                }
            },
            "TransactWriteItems",
        ),
    )
    with pytest.raises(Exception, match="Internal server error"):
        client.updateImages([example_image])
    mock_transact.assert_called_once()


@pytest.mark.integration
def test_updateImages_raises_clienterror_validation_exception(
    dynamodb_table, example_image, mocker
):
    """
    Tests that updateImages raises an Exception when the ValidationException
    error is raised.
    """
    client = DynamoClient(dynamodb_table)
    mock_transact = mocker.patch.object(
        client._client,
        "transact_write_items",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ValidationException",
                    "Message": "One or more parameters given were invalid",
                }
            },
            "TransactWriteItems",
        ),
    )
    with pytest.raises(
        Exception, match="One or more parameters given were invalid"
    ):
        client.updateImages([example_image])
    mock_transact.assert_called_once()


@pytest.mark.integration
def test_updateImages_raises_clienterror_access_denied(
    dynamodb_table, example_image, mocker
):
    """
    Tests that updateImages raises an Exception when the AccessDeniedException
    error is raised.
    """
    client = DynamoClient(dynamodb_table)
    mock_transact = mocker.patch.object(
        client._client,
        "transact_write_items",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "AccessDeniedException",
                    "Message": "Access denied",
                }
            },
            "TransactWriteItems",
        ),
    )
    with pytest.raises(Exception, match="Access denied"):
        client.updateImages([example_image])
    mock_transact.assert_called_once()


@pytest.mark.integration
def test_updateImages_raises_client_error(
    dynamodb_table, example_image, mocker
):
    """
    Simulate any error (ResourceNotFound, etc.) in transact_write_items.
    """
    client = DynamoClient(dynamodb_table)
    mock_transact = mocker.patch.object(
        client._client,
        "transact_write_items",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": "ResourceNotFoundException",
                    "Message": "No table found",
                }
            },
            "TransactWriteItems",
        ),
    )

    with pytest.raises(ValueError, match="Error updating images"):
        client.updateImages([example_image])

    mock_transact.assert_called_once()
