"""Contract tests for the Receipt DynamoDB entity."""

from datetime import datetime

import pytest

from receipt_dynamo import Receipt, item_to_receipt

pytestmark = pytest.mark.unit

IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
OTHER_IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed4"


def make_receipt(**overrides):
    """Build a receipt with independent nested coordinate dictionaries."""
    values = {
        "image_id": IMAGE_ID,
        "receipt_id": 1,
        "width": 200,
        "height": 100,
        "timestamp_added": "2021-01-01T00:00:00",
        "raw_s3_bucket": "test-bucket",
        "raw_s3_key": "test/key/receipt.jpg",
        "top_left": {"x": 0.0, "y": 0.0},
        "top_right": {"x": 200.0, "y": 0.0},
        "bottom_left": {"x": 0.0, "y": 100.0},
        "bottom_right": {"x": 200.0, "y": 100.0},
        "sha256": "abc123",
    }
    values.update(overrides)
    return Receipt(**values)


@pytest.fixture
def example_receipt():
    return make_receipt()


def test_receipt_init_normalizes_datetime_and_copies_points():
    point = {"x": 0, "y": 0}
    receipt = make_receipt(
        timestamp_added=datetime(2021, 1, 1), top_left=point
    )
    point["x"] = 99

    assert receipt.timestamp_added == "2021-01-01T00:00:00"
    assert receipt.top_left == {"x": 0, "y": 0}


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("image_id", 1, "uuid must be a string"),
        ("image_id", "bad", "uuid must be a valid UUIDv4"),
        ("receipt_id", -1, "receipt_id must be positive"),
        ("receipt_id", 0, "receipt_id must be positive"),
        ("receipt_id", "1", "receipt_id must be an integer"),
        ("receipt_id", True, "receipt_id must be an integer"),
        ("width", 0, "width and height must be positive integers"),
        ("width", True, "width and height must be positive integers"),
        ("height", -1, "width and height must be positive integers"),
        ("height", "100", "width and height must be positive integers"),
        ("timestamp_added", 123, "timestamp_added must be a datetime"),
        ("timestamp_added", "tomorrow", "valid ISO format timestamp"),
        ("raw_s3_bucket", None, "raw_s3_bucket must be a string"),
        ("raw_s3_bucket", 123, "raw_s3_bucket must be a string"),
        ("raw_s3_key", None, "raw_s3_key must be a string"),
        ("raw_s3_key", 123, "raw_s3_key must be a string"),
        ("sha256", False, "sha256 must be a string"),
        ("sha256", 123, "sha256 must be a string"),
        ("cdn_s3_bucket", 123, "cdn_s3_bucket must be a string"),
        ("cdn_s3_key", 123, "cdn_s3_key must be a string"),
    ],
)
def test_receipt_rejects_invalid_scalar_fields(field, value, match):
    with pytest.raises(ValueError, match=match):
        make_receipt(**{field: value})


@pytest.mark.parametrize(
    "point,match",
    [
        (None, "point must be a dictionary"),
        ({"x": 1}, "point must contain the key 'y'"),
        ({"x": 1, "y": 2, "z": 3}, "point must contain exactly"),
        ({"x": True, "y": 2}, r"point\['x'\] must be a number"),
        ({"x": float("nan"), "y": 2}, r"point\['x'\] must be a number"),
        ({"x": 1, "y": float("inf")}, r"point\['y'\] must be a number"),
    ],
)
@pytest.mark.parametrize(
    "field", ["top_left", "top_right", "bottom_left", "bottom_right"]
)
def test_receipt_rejects_invalid_nested_points(field, point, match):
    with pytest.raises(ValueError, match=match):
        make_receipt(**{field: point})


def test_receipt_keys_and_gsis_are_exact(example_receipt):
    assert example_receipt.key == {
        "PK": {"S": f"IMAGE#{IMAGE_ID}"},
        "SK": {"S": "RECEIPT#00001"},
    }
    assert example_receipt.gsi1_key() == {
        "GSI1PK": {"S": f"IMAGE#{IMAGE_ID}"},
        "GSI1SK": {"S": "RECEIPT#00001"},
    }
    assert example_receipt.gsi2_key() == {
        "GSI2PK": {"S": "RECEIPT"},
        "GSI2SK": {"S": f"IMAGE#{IMAGE_ID}#RECEIPT#00001"},
    }
    assert example_receipt.gsi4_key() == {
        "GSI4PK": {"S": f"IMAGE#{IMAGE_ID}#RECEIPT#00001"},
        "GSI4SK": {"S": "0_RECEIPT"},
    }


def test_receipt_item_has_exact_schema_and_nested_maps(example_receipt):
    item = example_receipt.to_item()
    expected_keys = {
        "PK",
        "SK",
        "GSI1PK",
        "GSI1SK",
        "GSI2PK",
        "GSI2SK",
        "GSI4PK",
        "GSI4SK",
        "TYPE",
        "width",
        "height",
        "timestamp_added",
        "raw_s3_bucket",
        "raw_s3_key",
        "top_left",
        "top_right",
        "bottom_left",
        "bottom_right",
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
    }
    assert set(item) == expected_keys
    assert item["TYPE"] == {"S": "RECEIPT"}
    assert item["top_left"] == {
        "M": {
            "x": {"N": "0.000000000000000000"},
            "y": {"N": "0.000000000000000000"},
        }
    }
    assert set(item["bottom_right"]["M"]) == {"x", "y"}


@pytest.mark.parametrize(
    "overrides",
    [
        {},
        {"sha256": None},
        {"cdn_s3_bucket": "cdn", "cdn_s3_key": "key"},
        {
            "cdn_thumbnail_s3_key": "thumb",
            "cdn_small_webp_s3_key": "small.webp",
            "cdn_medium_avif_s3_key": "medium.avif",
        },
    ],
)
def test_receipt_round_trip_preserves_all_fields(overrides):
    receipt = make_receipt(**overrides)
    item = receipt.to_item()
    restored = item_to_receipt(item)

    assert restored == receipt
    assert restored.to_item() == item


@pytest.mark.parametrize(
    "field,value",
    [
        ("image_id", OTHER_IMAGE_ID),
        ("receipt_id", 2),
        ("width", 201),
        ("height", 101),
        ("timestamp_added", "2021-01-01T00:00:01"),
        ("raw_s3_bucket", "other"),
        ("raw_s3_key", "other"),
        ("top_left", {"x": 1, "y": 0}),
        ("top_right", {"x": 201, "y": 0}),
        ("bottom_left", {"x": 1, "y": 100}),
        ("bottom_right", {"x": 201, "y": 100}),
        ("sha256", "other"),
        ("cdn_s3_key", "other"),
    ],
)
def test_receipt_equality_includes_every_persisted_field(field, value):
    assert make_receipt() != make_receipt(**{field: value})


def test_equal_receipts_have_equal_hashes_regardless_of_point_order():
    first = make_receipt()
    second = make_receipt()
    second.top_left = {"y": 0.0, "x": 0.0}

    assert first == second
    assert hash(first) == hash(second)


def test_receipt_repr_and_iteration_include_nested_state(example_receipt):
    representation = repr(example_receipt)
    mapping = dict(example_receipt)

    assert representation.startswith(f"Receipt(image_id='{IMAGE_ID}'")
    assert "top_left={'x': 0.0, 'y': 0.0}" in representation
    assert mapping["top_left"] == {"x": 0.0, "y": 0.0}
    assert mapping["sha256"] == "abc123"


def test_receipt_from_item_rejects_missing_and_malformed_fields(
    example_receipt,
):
    item = example_receipt.to_item()
    item.pop("top_left")
    with pytest.raises(ValueError, match="Invalid item format"):
        item_to_receipt(item)

    item = example_receipt.to_item()
    item["width"] = {"S": "wide"}
    with pytest.raises(ValueError, match="Error converting item to Receipt"):
        item_to_receipt(item)


@pytest.mark.parametrize(
    "field,value,match",
    [
        ("top_left", {"x": float("nan"), "y": 0}, "must be a number"),
        ("top_right", {"x": 1, "y": 2, "z": 3}, "exactly"),
        ("raw_s3_bucket", None, "raw_s3_bucket must be a string"),
        ("sha256", False, "sha256 must be a string"),
    ],
)
def test_receipt_serialization_revalidates_mutable_state(
    example_receipt, field, value, match
):
    setattr(example_receipt, field, value)

    with pytest.raises(ValueError, match=match):
        example_receipt.to_item()
