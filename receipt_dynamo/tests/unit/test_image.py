"""Contract tests for the Image DynamoDB entity."""

from datetime import datetime

import pytest

from receipt_dynamo import Image, item_to_image
from receipt_dynamo.constants import ImageType

pytestmark = pytest.mark.unit

IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
OTHER_IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed4"


def make_image(**overrides):
    """Build an image while keeping each test focused on one field."""
    values = {
        "image_id": IMAGE_ID,
        "width": 10,
        "height": 20,
        "timestamp_added": "2021-01-01T00:00:00",
        "raw_s3_bucket": "bucket",
        "raw_s3_key": "key",
        "sha256": "abc123",
        "cdn_s3_bucket": "cdn_bucket",
        "cdn_s3_key": "cdn_key",
    }
    values.update(overrides)
    return Image(**values)


@pytest.fixture
def example_image():
    return make_image()


def test_image_init_normalizes_supported_values():
    image = make_image(
        timestamp_added=datetime(2021, 1, 1),
        image_type=ImageType.SCAN,
        receipt_count=12,
    )

    assert image.timestamp_added == "2021-01-01T00:00:00"
    assert image.image_type == "SCAN"
    assert image.receipt_count == 12


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("image_id", 1, "uuid must be a string"),
        ("image_id", "not-a-uuid", "uuid must be a valid UUIDv4"),
        ("width", 0, "width and height must be positive integers"),
        ("width", -1, "width and height must be positive integers"),
        ("width", True, "width and height must be positive integers"),
        ("height", 0, "width and height must be positive integers"),
        ("height", "20", "width and height must be positive integers"),
        ("height", False, "width and height must be positive integers"),
        ("timestamp_added", 42, "timestamp_added must be a datetime"),
        ("timestamp_added", "yesterday", "valid ISO format timestamp"),
        ("raw_s3_bucket", None, "raw_s3_bucket must be a string"),
        ("raw_s3_bucket", 42, "raw_s3_bucket must be a string"),
        ("raw_s3_key", None, "raw_s3_key must be a string"),
        ("raw_s3_key", 42, "raw_s3_key must be a string"),
        ("sha256", False, "sha256 must be a string"),
        ("sha256", 42, "sha256 must be a string"),
        ("cdn_s3_bucket", 42, "cdn_s3_bucket must be a string"),
        ("cdn_s3_key", 42, "cdn_s3_key must be a string"),
        ("image_type", "INVALID", "image_type must be one of"),
        ("image_type", 42, "image_type must be a ImageType or a string"),
        ("receipt_count", -1, "receipt_count must be non-negative"),
        ("receipt_count", "1", "receipt_count must be an integer"),
        ("receipt_count", True, "receipt_count must be an integer"),
    ],
)
def test_image_rejects_invalid_fields(field, value, match):
    with pytest.raises(ValueError, match=match):
        make_image(**{field: value})


def test_image_keys_are_exact(example_image):
    assert example_image.key == {
        "PK": {"S": f"IMAGE#{IMAGE_ID}"},
        "SK": {"S": "IMAGE"},
    }
    assert example_image.gsi1_key == {
        "GSI1PK": {"S": f"IMAGE#{IMAGE_ID}"},
        "GSI1SK": {"S": "IMAGE"},
    }
    assert example_image.gsi2_key == {
        "GSI2PK": {"S": f"IMAGE#{IMAGE_ID}"},
        "GSI2SK": {"S": "IMAGE"},
    }
    assert example_image.gsi3_key == {
        "GSI3PK": {"S": "IMAGE#SCAN"},
        "GSI3SK": {"S": "NUM_RECEIPTS#00000"},
    }
    assert make_image(receipt_count=7).gsi3_key["GSI3SK"] == {
        "S": "NUM_RECEIPTS#00007"
    }


def test_image_item_has_exact_schema_and_values(example_image):
    item = example_image.to_item()
    expected_keys = {
        "PK",
        "SK",
        "GSI1PK",
        "GSI1SK",
        "GSI2PK",
        "GSI2SK",
        "GSI3PK",
        "GSI3SK",
        "TYPE",
        "width",
        "height",
        "timestamp_added",
        "raw_s3_bucket",
        "raw_s3_key",
        "sha256",
        "cdn_s3_bucket",
        "cdn_s3_key",
        "cdn_webp_s3_key",
        "cdn_avif_s3_key",
        "cdn_thumbnail_s3_key",
        "cdn_thumbnail_webp_s3_key",
        "cdn_thumbnail_avif_s3_key",
        "cdn_small_s3_key",
        "cdn_small_webp_s3_key",
        "cdn_small_avif_s3_key",
        "cdn_medium_s3_key",
        "cdn_medium_webp_s3_key",
        "cdn_medium_avif_s3_key",
        "image_type",
        "receipt_count",
    }
    assert set(item) == expected_keys
    assert item["TYPE"] == {"S": "IMAGE"}
    assert item["width"] == {"N": "10"}
    assert item["height"] == {"N": "20"}
    assert item["sha256"] == {"S": "abc123"}
    assert item["receipt_count"] == {"NULL": True}


@pytest.mark.parametrize(
    "field",
    ["sha256", "cdn_s3_bucket", "cdn_s3_key", "cdn_webp_s3_key"],
)
def test_image_optional_strings_round_trip_as_null(field):
    image = make_image(**{field: None})
    item = image.to_item()

    assert item[field] == {"NULL": True}
    assert item_to_image(item) == image


@pytest.mark.parametrize("receipt_count", [None, 0, 1, 99999])
def test_image_round_trip_preserves_receipt_count(receipt_count):
    image = make_image(receipt_count=receipt_count)
    item = image.to_item()

    assert item_to_image(item) == image
    assert item_to_image(item).to_item() == item


@pytest.mark.parametrize(
    "field,value",
    [
        ("image_id", OTHER_IMAGE_ID),
        ("width", 11),
        ("height", 21),
        ("timestamp_added", "2021-01-01T00:00:01"),
        ("raw_s3_bucket", "other-bucket"),
        ("raw_s3_key", "other-key"),
        ("sha256", "other-hash"),
        ("cdn_s3_bucket", "other-cdn"),
        ("cdn_s3_key", "other-cdn-key"),
        ("receipt_count", 2),
    ],
)
def test_image_equality_includes_every_persisted_field(field, value):
    assert make_image() != make_image(**{field: value})


def test_image_repr_and_mapping_include_core_fields(example_image):
    representation = repr(example_image)
    mapping = example_image.to_dict()

    assert representation.startswith(f"Image(image_id='{IMAGE_ID}'")
    assert "receipt_count=None" in representation
    assert mapping["image_id"] == IMAGE_ID
    assert mapping["sha256"] == "abc123"
    assert mapping["image_type"] == "SCAN"


def test_image_from_item_rejects_missing_and_malformed_fields(example_image):
    item = example_image.to_item()
    item.pop("height")
    with pytest.raises(ValueError, match="Invalid item format"):
        item_to_image(item)

    item = example_image.to_item()
    item["width"] = {"S": "ten"}
    with pytest.raises(ValueError, match="Error converting item to Image"):
        item_to_image(item)


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("receipt_count", True, "receipt_count must be an integer"),
        ("raw_s3_bucket", None, "raw_s3_bucket must be a string"),
        ("sha256", False, "sha256 must be a string"),
    ],
)
def test_image_serialization_revalidates_mutable_state(
    example_image, field, value, match
):
    setattr(example_image, field, value)

    with pytest.raises(ValueError, match=match):
        example_image.to_item()
