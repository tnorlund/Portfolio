"""
Parameterized integration tests for Image operations.
This file contains refactored tests using pytest.mark.parametrize to reduce
code duplication, following the pattern of test__receipt.py.
"""

from datetime import datetime
from typing import Any, List, Literal, Type
from uuid import uuid4

import boto3
import pytest
from botocore.exceptions import ClientError
from pytest_mock import MockerFixture

from receipt_dynamo import DynamoClient, Image, Letter, Line, Word
from receipt_dynamo.constants import ImageType, OCRJobType, OCRStatus
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    EntityNotFoundError,
    EntityValidationError,
    OperationError,
)
from receipt_dynamo.entities import (
    OCRJob,
    OCRRoutingDecision,
    ReceiptMetadata,
    ReceiptWordLabel,
)

# -------------------------------------------------------------------
#                        FIXTURES
# -------------------------------------------------------------------


@pytest.fixture(name="unique_image_id")
def _unique_image_id() -> str:
    """Provides a unique IMAGE_ID for each test to avoid conflicts."""
    return str(uuid4())


@pytest.fixture(name="sample_image")
def _sample_image(unique_image_id: str) -> Image:
    """Provides a sample Image for testing with a unique ID."""
    return Image(
        image_id=unique_image_id,
        width=10,
        height=20,
        timestamp_added="2021-01-01T00:00:00",
        raw_s3_bucket="bucket",
        raw_s3_key="key",
    )


@pytest.fixture(name="sample_line")
def _sample_line(unique_image_id: str) -> Line:
    """Provides a sample Line for testing."""
    return Line(
        unique_image_id,
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


@pytest.fixture(name="sample_word")
def _sample_word(unique_image_id: str) -> Word:
    """Provides a sample Word for testing."""
    return Word(
        unique_image_id,
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


@pytest.fixture(name="sample_letter")
def _sample_letter(unique_image_id: str) -> Letter:
    """Provides a sample Letter for testing."""
    return Letter(
        unique_image_id,
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


@pytest.fixture(name="sample_receipt_metadata")
def _sample_receipt_metadata(unique_image_id: str) -> ReceiptMetadata:
    """Provides a sample ReceiptMetadata for testing."""
    return ReceiptMetadata(
        image_id=unique_image_id,
        receipt_id=1,
        place_id="id",
        merchant_name="Merchant",
        matched_fields=["name"],
        validated_by="NEARBY_LOOKUP",
        timestamp=datetime(2025, 1, 1, 0, 0, 0),
    )


@pytest.fixture(name="sample_ocr_job")
def _sample_ocr_job(unique_image_id: str) -> OCRJob:
    """Provides a sample OCRJob for testing."""
    return OCRJob(
        image_id=unique_image_id,
        job_id=str(uuid4()),
        s3_bucket="bucket",
        s3_key="key",
        created_at=datetime(2025, 1, 1, 0, 0, 0),
        updated_at=datetime(2025, 1, 1, 0, 0, 0),
        status=OCRStatus.PENDING,
        job_type=OCRJobType.FIRST_PASS,
    )


@pytest.fixture(name="sample_routing_decision")
def _sample_routing_decision(
    unique_image_id: str, sample_ocr_job: OCRJob
) -> OCRRoutingDecision:
    """Provides a sample OCRRoutingDecision for testing."""
    return OCRRoutingDecision(
        image_id=unique_image_id,
        job_id=sample_ocr_job.job_id,
        s3_bucket="bucket",
        s3_key="key",
        created_at=datetime(2025, 1, 1, 0, 0, 0),
        updated_at=datetime(2025, 1, 1, 0, 0, 0),
        receipt_count=1,
        status=OCRStatus.PENDING,
    )


@pytest.fixture(name="sample_receipt_word_label")
def _sample_receipt_word_label(unique_image_id: str) -> ReceiptWordLabel:
    """Provides a sample ReceiptWordLabel for testing."""
    return ReceiptWordLabel(
        image_id=unique_image_id,
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


@pytest.fixture(name="batch_images")
def _batch_images() -> List[Image]:
    """Provides a list of 100 images for batch testing."""
    # Use random but consistent seed for deterministic UUIDs
    import random  # pylint: disable=import-outside-toplevel

    # Set seed for deterministic generation
    random.seed(42)

    images = []
    for i in range(100):
        # Generate UUID v4 (random.seed makes it deterministic)
        images.append(
            Image(
                image_id=str(uuid4()),
                width=10,
                height=20,
                timestamp_added="2021-01-01T00:00:00",
                raw_s3_bucket="bucket",
                raw_s3_key=f"key-{i:04d}",
            )
        )

    return images


# -------------------------------------------------------------------
#                   PARAMETERIZED CLIENT ERROR TESTS
# -------------------------------------------------------------------

# Common error scenarios for all operations matching test__receipt.py
ERROR_SCENARIOS = [
    (
        "ProvisionedThroughputExceededException",
        DynamoDBThroughputError,
        "Throughput exceeded",
    ),
    ("InternalServerError", DynamoDBServerError, "DynamoDB server error"),
    ("ValidationException", EntityValidationError, "Validation error"),
    ("AccessDeniedException", DynamoDBError, "DynamoDB error during"),
    (
        "ResourceNotFoundException",
        OperationError,
        "DynamoDB resource not found",
    ),
]

# Additional error for add operations
ADD_ERROR_SCENARIOS = [
    (
        "ConditionalCheckFailedException",
        EntityValidationError,
        "image already exists",
    ),
] + ERROR_SCENARIOS

# Additional error for update operations
UPDATE_ERROR_SCENARIOS = [
    (
        "ConditionalCheckFailedException",
        EntityNotFoundError,
        "Cannot update images: one or more images not found",
    ),
] + ERROR_SCENARIOS


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", ADD_ERROR_SCENARIOS
)
def test_add_image_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_image: Image,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:  # pylint: disable=too-many-arguments
    """Tests that add_image raises appropriate exceptions for various
    ClientError scenarios."""
    client = DynamoClient(dynamodb_table)
    # pylint: disable=protected-access
    mock_put = mocker.patch.object(
        client._client,
        "put_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": error_code,
                    "Message": f"Mocked {error_code}",
                }
            },
            "PutItem",
        ),
    )

    with pytest.raises(expected_exception, match=error_match):
        client.add_image(sample_image)
    mock_put.assert_called_once()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", UPDATE_ERROR_SCENARIOS
)
def test_update_images_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_image: Image,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:  # pylint: disable=too-many-arguments
    """Tests that update_images raises appropriate exceptions for various
    ClientError scenarios."""
    client = DynamoClient(dynamodb_table)
    # pylint: disable=protected-access
    mock_transact = mocker.patch.object(
        client._client,
        "transact_write_items",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": error_code,
                    "Message": f"Mocked {error_code}",
                }
            },
            "TransactWriteItems",
        ),
    )

    with pytest.raises(expected_exception, match=error_match):
        client.update_images([sample_image])
    mock_transact.assert_called_once()


@pytest.mark.integration
@pytest.mark.parametrize(
    "error_code,expected_exception,error_match", ERROR_SCENARIOS
)
def test_delete_image_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    unique_image_id: str,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:  # pylint: disable=too-many-arguments
    """Tests that delete_image raises appropriate exceptions for various
    ClientError scenarios."""
    client = DynamoClient(dynamodb_table)
    # pylint: disable=protected-access
    mock_delete = mocker.patch.object(
        client._client,
        "delete_item",
        side_effect=ClientError(
            {
                "Error": {
                    "Code": error_code,
                    "Message": f"Mocked {error_code}",
                }
            },
            "DeleteItem",
        ),
    )

    with pytest.raises(expected_exception, match=error_match):
        client.delete_image(unique_image_id)
    mock_delete.assert_called_once()


# -------------------------------------------------------------------
#                      VALIDATION ERROR TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_match",
    [
        (None, "image cannot be None"),
        ("not-an-image", "image must be an instance of Image"),
    ],
)
def test_add_image_validation_errors(
    dynamodb_table: Literal["MyMockedTable"],
    invalid_input: Any,
    expected_match: str,
) -> None:
    """Tests that add_image raises OperationError for invalid inputs."""
    client = DynamoClient(dynamodb_table)
    with pytest.raises(OperationError, match=expected_match):
        client.add_image(invalid_input)


@pytest.mark.integration
@pytest.mark.parametrize(
    "invalid_input,expected_match",
    [
        (None, "images cannot be None"),
        ("not-a-list", "images must be a list"),
    ],
)
def test_update_images_validation_errors(
    dynamodb_table: Literal["MyMockedTable"],
    invalid_input: Any,
    expected_match: str,
) -> None:
    """Tests that update_images raises OperationError for invalid inputs."""
    client = DynamoClient(dynamodb_table)
    with pytest.raises(OperationError, match=expected_match):
        client.update_images(invalid_input)


@pytest.mark.integration
def test_update_images_invalid_list_contents(
    dynamodb_table: Literal["MyMockedTable"],
    sample_image: Image,
) -> None:
    """Tests that update_images raises OperationError for list with
    non-Image instances."""
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        OperationError, match="images must be a list of Image instances"
    ):
        client.update_images([sample_image, "not-an-image"])


# -------------------------------------------------------------------
#                      SUCCESS PATH TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
def test_add_image_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_image: Image,
) -> None:
    """Tests the happy path of add_image."""
    client = DynamoClient(dynamodb_table)
    client.add_image(sample_image)

    # Verify the image in DynamoDB
    retrieved = client.get_image(sample_image.image_id)
    assert retrieved == sample_image

    # Direct DynamoDB verification
    response = boto3.client("dynamodb", region_name="us-east-1").get_item(
        TableName=dynamodb_table,
        Key={
            "PK": {"S": f"IMAGE#{sample_image.image_id}"},
            "SK": {"S": "IMAGE"},
        },
    )
    assert response["Item"] == sample_image.to_item()


@pytest.mark.integration
def test_add_image_duplicate_raises(
    dynamodb_table: Literal["MyMockedTable"],
    sample_image: Image,
) -> None:
    """Tests that adding a duplicate image raises EntityValidationError."""
    client = DynamoClient(dynamodb_table)
    client.add_image(sample_image)

    with pytest.raises(EntityValidationError, match="image already exists"):
        client.add_image(sample_image)


@pytest.mark.integration
def test_get_image_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_image: Image,
) -> None:
    """Tests successful image retrieval."""
    client = DynamoClient(dynamodb_table)
    client.add_image(sample_image)

    retrieved = client.get_image(sample_image.image_id)
    assert retrieved == sample_image


@pytest.mark.integration
def test_get_image_not_found(
    dynamodb_table: Literal["MyMockedTable"],
    unique_image_id: str,
) -> None:
    """Tests get_image raises EntityNotFoundError for non-existent image."""
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        EntityNotFoundError, match=f"Image with ID {unique_image_id} not found"
    ):
        client.get_image(unique_image_id)


@pytest.mark.integration
def test_get_image_invalid_id(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests get_image raises OperationError for invalid UUID."""
    client = DynamoClient(dynamodb_table)
    with pytest.raises(OperationError, match="uuid must be a valid UUIDv4"):
        client.get_image("not-a-uuid")


@pytest.mark.integration
def test_update_images_success(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests successful batch image update."""
    client = DynamoClient(dynamodb_table)

    # Create two images with unique IDs
    img1 = Image(
        str(uuid4()),
        10,
        20,
        "2021-01-01T00:00:00",
        "bucket",
        "key1",
    )
    img2 = Image(
        str(uuid4()),
        10,
        20,
        "2021-01-01T00:00:00",
        "bucket",
        "key2",
    )

    # Add images first
    client.add_images([img1, img2])

    # Update images
    img1.raw_s3_key = "updated/path/1"
    img2.raw_s3_key = "updated/path/2"
    client.update_images([img1, img2])

    # Verify updates
    retrieved1 = client.get_image(img1.image_id)
    retrieved2 = client.get_image(img2.image_id)

    assert retrieved1.raw_s3_key == "updated/path/1"
    assert retrieved2.raw_s3_key == "updated/path/2"


@pytest.mark.integration
def test_delete_image_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_image: Image,
) -> None:
    """Tests successful image deletion."""
    client = DynamoClient(dynamodb_table)
    client.add_image(sample_image)
    client.delete_image(sample_image.image_id)

    with pytest.raises(EntityNotFoundError):
        client.get_image(sample_image.image_id)


@pytest.mark.integration
def test_delete_image_not_found(
    dynamodb_table: Literal["MyMockedTable"],
    unique_image_id: str,
) -> None:
    """Tests delete_image raises EntityNotFoundError for non-existent image."""
    client = DynamoClient(dynamodb_table)
    # Based on the condition_expression="attribute_exists(PK)" in
    # _delete_entity
    with pytest.raises(EntityNotFoundError):
        client.delete_image(unique_image_id)


@pytest.mark.integration
def test_delete_images_batch(
    dynamodb_table: Literal["MyMockedTable"],
    batch_images: List[Image],
) -> None:
    """Tests batch deletion of multiple images."""
    client = DynamoClient(dynamodb_table)
    client.add_images(batch_images)
    client.delete_images(batch_images)

    response_images, _ = client.list_images()
    assert len(response_images) == 0


# -------------------------------------------------------------------
#                      LIST OPERATIONS TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
def test_list_images_by_type_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_image: Image,
) -> None:
    """Tests listing images by type."""
    client = DynamoClient(dynamodb_table)
    client.add_image(sample_image)

    images, last_evaluated_key = client.list_images_by_type(ImageType.SCAN)
    assert len(images) == 1
    assert images[0] == sample_image
    assert last_evaluated_key is None


@pytest.mark.integration
def test_list_images_by_type_invalid_type(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests that list_images_by_type raises EntityValidationError for
    invalid type."""
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        EntityValidationError,
        match="image_type must be one of: SCAN, PHOTO, NATIVE"
    ):
        client.list_images_by_type("INVALID")


@pytest.mark.integration
def test_list_images_by_type_with_pagination(
    dynamodb_table: Literal["MyMockedTable"],
) -> None:
    """Tests list_images_by_type handles pagination correctly."""
    client = DynamoClient(dynamodb_table)

    # Add multiple images to ensure pagination
    images = []
    for i in range(3):
        img = Image(
            str(uuid4()),
            width=10 + i,
            height=20 + i,
            timestamp_added="2021-01-01T00:00:00",
            raw_s3_bucket="bucket",
            raw_s3_key=f"key-{i}",
            image_type=ImageType.SCAN,
        )
        images.append(img)
        client.add_image(img)

    # Query with small limit to force pagination
    results, last_evaluated_key = client.list_images_by_type(
        ImageType.SCAN, limit=2
    )

    # Should get 2 results and a pagination key
    assert len(results) == 2
    assert last_evaluated_key is not None

    # Get next page
    results2, _ = client.list_images_by_type(
        ImageType.SCAN, limit=2, last_evaluated_key=last_evaluated_key
    )

    # Should get at least 1 more result
    assert len(results2) >= 1


# -------------------------------------------------------------------
#                   IMAGE DETAILS TESTS
# -------------------------------------------------------------------


@pytest.mark.integration
def test_get_image_details_complete(
    dynamodb_table: Literal["MyMockedTable"],
    sample_image: Image,
    sample_line: Line,
    sample_word: Word,
    sample_letter: Letter,
    sample_receipt_metadata: ReceiptMetadata,
    sample_ocr_job: OCRJob,
    sample_routing_decision: OCRRoutingDecision,
    sample_receipt_word_label: ReceiptWordLabel,
) -> None:  # pylint: disable=too-many-arguments
    """Tests get_image_details with all related entities."""
    client = DynamoClient(dynamodb_table)

    # Add all entities
    client.add_image(sample_image)
    client.add_line(sample_line)
    client.add_word(sample_word)
    client.add_letter(sample_letter)
    client.add_receipt_metadata(sample_receipt_metadata)
    client.add_ocr_job(sample_ocr_job)
    client.add_ocr_routing_decision(sample_routing_decision)
    client.add_receipt_word_label(sample_receipt_word_label)

    # Get details
    details = client.get_image_details(sample_image.image_id)

    # Verify all entities are retrieved
    assert len(details.images) == 1
    assert details.images[0] == sample_image
    assert details.lines == [sample_line]
    assert details.words == [sample_word]
    assert details.letters == [sample_letter]

    # Verify receipt word label
    assert len(details.receipt_word_labels) == 1
    retrieved_label = details.receipt_word_labels[0]
    assert retrieved_label.image_id == sample_receipt_word_label.image_id
    assert retrieved_label.receipt_id == sample_receipt_word_label.receipt_id
    assert retrieved_label.line_id == sample_receipt_word_label.line_id
    assert retrieved_label.word_id == sample_receipt_word_label.word_id
    assert retrieved_label.label == sample_receipt_word_label.label
    assert retrieved_label.reasoning == sample_receipt_word_label.reasoning

    # Verify receipt metadata
    assert len(details.receipt_metadatas) == 1
    retrieved_metadata = details.receipt_metadatas[0]
    assert retrieved_metadata.image_id == sample_receipt_metadata.image_id
    assert retrieved_metadata.receipt_id == sample_receipt_metadata.receipt_id

    # Verify OCR entities
    assert details.ocr_jobs == [sample_ocr_job]
    assert len(details.ocr_routing_decisions) == 1
    retrieved_decision = details.ocr_routing_decisions[0]
    assert retrieved_decision.image_id == sample_routing_decision.image_id
    assert retrieved_decision.job_id == sample_routing_decision.job_id
    assert (
        retrieved_decision.receipt_count == sample_routing_decision.receipt_count
    )
    assert retrieved_decision.status == sample_routing_decision.status


@pytest.mark.integration
def test_get_image_details_multiple_receipt_metadatas(
    dynamodb_table: Literal["MyMockedTable"],
    sample_image: Image,
) -> None:
    """Tests get_image_details with multiple receipt metadatas."""
    client = DynamoClient(dynamodb_table)
    client.add_image(sample_image)

    # Create multiple receipt metadatas
    metadatas = []
    for i in range(1, 4):
        metadata = ReceiptMetadata(
            image_id=sample_image.image_id,
            receipt_id=i,
            place_id=f"place_{i}",
            merchant_name=f"Merchant {chr(65 + i - 1)}",  # A,B,C
            matched_fields=(
                ["name"] if i == 2 else
                ["name", "address" if i == 1 else "phone"]
            ),
            validated_by=["NEARBY_LOOKUP", "TEXT_SEARCH", "PHONE_LOOKUP"][i - 1],
            timestamp=datetime(2025, 1, 1, i - 1, 0, 0),
        )
        client.add_receipt_metadata(metadata)
        metadatas.append(metadata)

    # Get details
    details = client.get_image_details(sample_image.image_id)

    # Verify all metadatas retrieved
    assert len(details.receipt_metadatas) == 3

    # Sort for consistent comparison
    sorted_metadatas = sorted(
        details.receipt_metadatas, key=lambda x: x.receipt_id
    )

    for i, metadata in enumerate(sorted_metadatas):
        assert metadata.receipt_id == i + 1
        assert metadata.merchant_name == f"Merchant {chr(65 + i)}"
        assert metadata.place_id == f"place_{i + 1}"


@pytest.mark.integration
def test_get_image_details_no_related_entities(
    dynamodb_table: Literal["MyMockedTable"],
    sample_image: Image,
) -> None:
    """Tests get_image_details with only the image entity."""
    client = DynamoClient(dynamodb_table)
    client.add_image(sample_image)

    details = client.get_image_details(sample_image.image_id)

    # Verify only image is present
    assert len(details.images) == 1
    assert details.images[0] == sample_image

    # Verify all other lists are empty
    assert all(
        len(getattr(details, attr)) == 0
        for attr in [
            "lines",
            "words",
            "letters",
            "receipts",
            "receipt_lines",
            "receipt_words",
            "receipt_letters",
            "receipt_word_labels",
            "receipt_metadatas",
            "ocr_jobs",
            "ocr_routing_decisions",
        ]
    )


@pytest.mark.integration
def test_get_image_details_multiple_receipt_word_labels(
    dynamodb_table: Literal["MyMockedTable"],
    sample_image: Image,
) -> None:
    """Tests get_image_details with multiple receipt word labels."""
    client = DynamoClient(dynamodb_table)
    client.add_image(sample_image)

    # Create multiple labels
    label_types = ["MERCHANT_NAME", "PRODUCT_NAME", "GRAND_TOTAL"]
    labels = []
    for i, label_type in enumerate(label_types):
        label = ReceiptWordLabel(
            image_id=sample_image.image_id,
            receipt_id=1,
            line_id=i + 1,
            word_id=i + 1,
            label=label_type,
            reasoning=f"Test reasoning {i + 1}",
            timestamp_added=datetime(2025, 1, 1, i, 0, 0),
            validation_status="NONE",
            label_proposed_by="test_system",
            label_consolidated_from=None,
        )
        client.add_receipt_word_label(label)
        labels.append(label)

    # Get details
    details = client.get_image_details(sample_image.image_id)

    # Verify all labels retrieved
    assert len(details.receipt_word_labels) == 3

    # Sort for consistent comparison
    sorted_labels = sorted(
        details.receipt_word_labels, key=lambda x: x.line_id
    )

    for i, label in enumerate(sorted_labels):
        assert label.image_id == labels[i].image_id
        assert label.receipt_id == labels[i].receipt_id
        assert label.line_id == labels[i].line_id
        assert label.word_id == labels[i].word_id
        assert label.label == labels[i].label
        assert label.reasoning == labels[i].reasoning
        assert label.label_proposed_by == labels[i].label_proposed_by


# -------------------------------------------------------------------
#                      UPDATE IMAGE TEST
# -------------------------------------------------------------------


@pytest.mark.integration
def test_update_image_success(
    dynamodb_table: Literal["MyMockedTable"],
    sample_image: Image,
) -> None:
    """Tests successful single image update."""
    client = DynamoClient(dynamodb_table)

    # Add image first
    client.add_image(sample_image)

    # Update the image
    sample_image.raw_s3_key = "updated/key"
    client.update_image(sample_image)

    # Verify update
    retrieved = client.get_image(sample_image.image_id)
    assert retrieved.raw_s3_key == "updated/key"
