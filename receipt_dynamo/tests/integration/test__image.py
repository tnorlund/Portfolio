from datetime import datetime
from uuid import uuid4

import boto3
import pytest
from botocore.exceptions import ClientError

from receipt_dynamo import DynamoClient, Image, Letter, Line, Word
from receipt_dynamo.constants import OCRJobType, OCRStatus
from receipt_dynamo.data.shared_exceptions import EntityAlreadyExistsError
from receipt_dynamo.entities import (
    OCRJob,
    OCRRoutingDecision,
    ReceiptMetadata,
    ReceiptWordLabel,
)


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
    with pytest.raises(ValueError, match="image parameter is required"):
        client.add_image(None)


@pytest.mark.integration
def test_addImage_raises_value_error_for_invalid_type(dynamodb_table):
    """
    Checks addImage with an invalid type for 'image'.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError, match="image must be an instance"):
        client.add_image("not-an-image")


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

    with pytest.raises(EntityAlreadyExistsError, match="already exists"):
        client.add_image(example_image)

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
        client.add_image(example_image)

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

    with pytest.raises(Exception, match="Internal server error"):
        client.add_image(example_image)

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

    with pytest.raises(Exception, match="Could not add image to DynamoDB"):
        client.add_image(example_image)

    mock_put.assert_called_once()


@pytest.mark.integration
def test_addImage(dynamodb_table, example_image):
    """
    Verifies a successful addImage call actually persists data to DynamoDB.
    """
    client = DynamoClient(dynamodb_table)
    client.add_image(example_image)

    # Immediately call getImage after adding to prove integration.
    retrieved_image = client.get_image(example_image.image_id)
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
    client.add_image(example_image)

    # Now specifically test getImage.
    retrieved_image = client.get_image(example_image.image_id)
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

    client.add_image(image)
    client.add_line(line)
    client.add_word(word)
    client.add_letter(letter)
    receipt_metadata = ReceiptMetadata(
        image_id=image.image_id,
        receipt_id=1,
        place_id="id",
        merchant_name="Merchant",
        matched_fields=["name"],
        validated_by="NEARBY_LOOKUP",
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
    )
    client.add_receipt_metadata(receipt_metadata)
    ocr_job = OCRJob(
        image_id=image.image_id,
        job_id="4f52804b-2fad-4e00-92c8-b593da3a8ed3",
        s3_bucket="bucket",
        s3_key="key",
        created_at=datetime(2025, 1, 1, 0, 0, 0),
        updated_at=datetime(2025, 1, 1, 0, 0, 0),
        status=OCRStatus.PENDING,
        job_type=OCRJobType.FIRST_PASS,
    )
    routing_decision = OCRRoutingDecision(
        image_id=image.image_id,
        job_id=ocr_job.job_id,
        s3_bucket="bucket",
        s3_key="key",
        created_at=datetime(2025, 1, 1, 0, 0, 0),
        updated_at=datetime(2025, 1, 1, 0, 0, 0),
        receipt_count=1,
        status=OCRStatus.PENDING,
    )
    client.add_ocr_job(ocr_job)
    client.add_ocr_routing_decision(routing_decision)

    # Add a receipt word label
    receipt_word_label = ReceiptWordLabel(
        image_id=image.image_id,
        receipt_id=1,
        line_id=1,
        word_id=1,
        label="MERCHANT_NAME",
        reasoning="Identified as merchant name based on position and format",
        timestamp_added=datetime(2025, 1, 1, 0, 0, 0),
        validation_status="NONE",
        label_proposed_by="pattern_detector",
        label_consolidated_from=None,
    )
    client.add_receipt_word_label(receipt_word_label)

    details = client.get_image_details(image.image_id)

    (
        images,
        lines,
        words,
        letters,
        receipts,
        receipt_lines,
        receipt_words,
        receipt_letters,
        receipt_word_labels,
        receipt_metadatas,
        ocr_jobs,
        routing_decisions,
    ) = details
    retrieved_image = images[0]
    assert retrieved_image == image
    assert lines == [line]
    assert words == [word]
    assert letters == [letter]
    assert len(receipt_word_labels) == 1
    retrieved_label = receipt_word_labels[0]
    assert retrieved_label.image_id == receipt_word_label.image_id
    assert retrieved_label.receipt_id == receipt_word_label.receipt_id
    assert retrieved_label.line_id == receipt_word_label.line_id
    assert retrieved_label.word_id == receipt_word_label.word_id
    assert retrieved_label.label == receipt_word_label.label
    assert retrieved_label.reasoning == receipt_word_label.reasoning
    retrieved_metadata = receipt_metadatas[0]
    assert retrieved_metadata.image_id == receipt_metadata.image_id
    assert retrieved_metadata.receipt_id == receipt_metadata.receipt_id
    assert ocr_jobs == [ocr_job]
    retrieved_decision = routing_decisions[0]
    assert retrieved_decision.image_id == routing_decision.image_id
    assert retrieved_decision.job_id == routing_decision.job_id
    assert retrieved_decision.s3_bucket == routing_decision.s3_bucket
    assert retrieved_decision.s3_key == routing_decision.s3_key
    assert retrieved_decision.created_at == routing_decision.created_at
    assert retrieved_decision.updated_at == routing_decision.updated_at
    assert retrieved_decision.receipt_count == routing_decision.receipt_count
    assert retrieved_decision.status == routing_decision.status


@pytest.mark.integration
def test_image_get_details_multiple_receipt_metadatas(
    dynamodb_table, example_image
):
    """Test that image details correctly handles multiple receipt metadatas."""
    client = DynamoClient(dynamodb_table)
    image = example_image

    # Add the image
    client.add_image(image)

    # Create multiple receipt metadatas for the same image
    receipt_metadata1 = ReceiptMetadata(
        image_id=image.image_id,
        receipt_id=1,
        place_id="place_1",
        merchant_name="Merchant A",
        matched_fields=["name", "address"],
        validated_by="NEARBY_LOOKUP",
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
    )

    receipt_metadata2 = ReceiptMetadata(
        image_id=image.image_id,
        receipt_id=2,
        place_id="place_2",
        merchant_name="Merchant B",
        matched_fields=["name"],
        validated_by="TEXT_SEARCH",
        timestamp=datetime(2025, 1, 1, 1, 0, 0),
    )

    receipt_metadata3 = ReceiptMetadata(
        image_id=image.image_id,
        receipt_id=3,
        place_id="place_3",
        merchant_name="Merchant C",
        matched_fields=["name", "phone"],
        validated_by="PHONE_LOOKUP",
        timestamp=datetime(2025, 1, 1, 2, 0, 0),
    )

    # Add all receipt metadatas
    client.add_receipt_metadata(receipt_metadata1)
    client.add_receipt_metadata(receipt_metadata2)
    client.add_receipt_metadata(receipt_metadata3)

    # Get image details
    details = client.get_image_details(image.image_id)

    (
        images,
        lines,
        words,
        letters,
        receipts,
        receipt_lines,
        receipt_words,
        receipt_letters,
        receipt_word_labels,
        receipt_metadatas,
        ocr_jobs,
        routing_decisions,
    ) = details

    # Verify we got all three receipt metadatas
    assert len(receipt_metadatas) == 3
    # Verify no receipt word labels were added
    assert len(receipt_word_labels) == 0

    # Sort by receipt_id for consistent ordering
    sorted_metadatas = sorted(receipt_metadatas, key=lambda x: x.receipt_id)

    # Verify each metadata
    assert sorted_metadatas[0].receipt_id == 1
    assert sorted_metadatas[0].merchant_name == "Merchant A"
    assert sorted_metadatas[0].place_id == "place_1"

    assert sorted_metadatas[1].receipt_id == 2
    assert sorted_metadatas[1].merchant_name == "Merchant B"
    assert sorted_metadatas[1].place_id == "place_2"

    assert sorted_metadatas[2].receipt_id == 3
    assert sorted_metadatas[2].merchant_name == "Merchant C"
    assert sorted_metadatas[2].place_id == "place_3"


@pytest.mark.integration
def test_image_get_details_no_receipt_metadata(dynamodb_table, example_image):
    """Test that image details correctly handles no receipt metadata."""
    client = DynamoClient(dynamodb_table)
    image = example_image

    # Add image with some basic data but no receipt metadata
    client.add_image(image)

    # Add a line just to have some data
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
    client.add_line(line)

    # Get image details
    details = client.get_image_details(image.image_id)

    (
        images,
        lines,
        words,
        letters,
        receipts,
        receipt_lines,
        receipt_words,
        receipt_letters,
        receipt_word_labels,
        receipt_metadatas,
        ocr_jobs,
        routing_decisions,
    ) = details

    # Verify we have the image and line but no receipt metadata
    assert len(images) == 1
    assert len(lines) == 1
    assert len(receipt_metadatas) == 0
    assert len(receipt_word_labels) == 0


@pytest.mark.integration
def test_image_get_details_with_multiple_receipt_word_labels(
    dynamodb_table, example_image
):
    """Test that image details correctly handles multiple receipt word labels."""
    client = DynamoClient(dynamodb_table)
    image = example_image

    # Add the image
    client.add_image(image)

    # Create multiple receipt word labels for the same image
    labels = []
    for i in range(3):
        label = ReceiptWordLabel(
            image_id=image.image_id,
            receipt_id=1,
            line_id=i + 1,
            word_id=i + 1,
            label=["MERCHANT_NAME", "PRODUCT_NAME", "GRAND_TOTAL"][i],
            reasoning=f"Test reasoning {i + 1}",
            timestamp_added=datetime(2025, 1, 1, i, 0, 0),
            validation_status="NONE",
            label_proposed_by="test_system",
            label_consolidated_from=None,
        )
        client.add_receipt_word_label(label)
        labels.append(label)

    # Get image details
    details = client.get_image_details(image.image_id)

    (
        images,
        lines,
        words,
        letters,
        receipts,
        receipt_lines,
        receipt_words,
        receipt_letters,
        receipt_word_labels,
        receipt_metadatas,
        ocr_jobs,
        routing_decisions,
    ) = details

    # Verify we got all three receipt word labels
    assert len(receipt_word_labels) == 3

    # Sort by line_id for consistent ordering
    sorted_labels = sorted(receipt_word_labels, key=lambda x: x.line_id)

    # Verify each label
    for i, label in enumerate(sorted_labels):
        assert label.image_id == labels[i].image_id
        assert label.receipt_id == labels[i].receipt_id
        assert label.line_id == labels[i].line_id
        assert label.word_id == labels[i].word_id
        assert label.label == labels[i].label
        assert label.reasoning == labels[i].reasoning
        assert label.label_proposed_by == labels[i].label_proposed_by


@pytest.mark.integration
def test_image_delete(dynamodb_table, example_image):
    client = DynamoClient(dynamodb_table)
    client.add_image(example_image)
    client.delete_image(example_image.image_id)
    with pytest.raises(ValueError):
        client.get_image(example_image.image_id)


@pytest.mark.integration
def test_image_delete_error(dynamodb_table, example_image):
    client = DynamoClient(dynamodb_table)
    with pytest.raises(ValueError):
        client.delete_image(example_image.image_id)


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
    client.add_images(images)
    client.delete_images(images)
    response_images, _ = client.list_images()
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
    client.add_images([img1, img2])

    # Now update them
    img1.raw_s3_key = "updated/path/1"
    img2.raw_s3_key = "updated/path/2"
    client.update_images([img1, img2])

    # Verify updates
    stored_images, _ = client.list_images()
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
        ValueError, match="images parameter is required and cannot be None."
    ):
        client.update_images(None)  # type: ignore


@pytest.mark.integration
def test_updateImages_raises_value_error_images_not_list(
    dynamodb_table, example_image
):
    """
    Tests that updateImages raises ValueError when the images parameter is not
    a list.
    """
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        ValueError, match="images must be a list of Image instances."
    ):
        client.update_images("not-a-list")  # type: ignore


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
        match="All images must be instances of the Image class.",
    ):
        client.update_images([example_image, "not-an-image"])  # type: ignore


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
        client.update_images([example_image])
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
        client.update_images([example_image])
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
        client.update_images([example_image])
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
        client.update_images([example_image])
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
        client.update_images([example_image])
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

    from receipt_dynamo.data.shared_exceptions import DynamoDBError

    with pytest.raises(
        DynamoDBError,
        match="Could not update ReceiptValidationResult in the database",
    ):
        client.update_images([example_image])

    mock_transact.assert_called_once()


@pytest.mark.integration
def test_listImagesByType_no_limit(dynamodb_table, example_image):
    client = DynamoClient(dynamodb_table)
    client.add_image(example_image)

    images, lek = client.list_images_by_type(example_image.image_type)

    assert images == [example_image]
    assert lek is None


@pytest.mark.integration
def test_listImagesByType_invalid_type(dynamodb_table, example_image):
    client = DynamoClient(dynamodb_table)
    client.add_image(example_image)

    with pytest.raises(ValueError):
        client.list_images_by_type("INVALID")


@pytest.mark.integration
def test_listImagesByType_with_pagination(
    dynamodb_table, example_image, mocker
):
    client = DynamoClient(dynamodb_table)

    first_page = {
        "Items": [example_image.to_item()],
        "LastEvaluatedKey": {"d": "k"},
    }
    second_page = {"Items": [example_image.to_item()]}

    mock_query = mocker.patch.object(
        client._client, "query", side_effect=[first_page, second_page]
    )

    images, lek = client.list_images_by_type(
        example_image.image_type, limit=10
    )

    assert len(images) == 1
    assert lek == first_page["LastEvaluatedKey"]
    assert mock_query.call_count == 1
