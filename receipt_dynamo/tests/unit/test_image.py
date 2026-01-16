# infra/lambda_layer/python/test/unit/test_image.py
"""Unit tests for the Image entity."""
# pylint: disable=redefined-outer-name,too-many-statements,too-many-arguments
# pylint: disable=too-many-locals,unused-argument,line-too-long

import pytest

from receipt_dynamo import Image, item_to_image


@pytest.fixture
def example_image():
    """Provides a sample Image for testing."""
    return Image(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        10,
        20,
        "2021-01-01T00:00:00",
        "bucket",
        "key",
        "abc123",
        "cdn_bucket",
        "cdn_key",
    )


@pytest.fixture
def example_image_no_sha():
    """Provides a sample Image for testing."""
    return Image(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        10,
        20,
        "2021-01-01T00:00:00",
        "bucket",
        "key",
        cdn_s3_bucket="cdn_bucket",
        cdn_s3_key="cdn_key",
    )


@pytest.fixture
def example_image_no_cdn_bucket():
    """Provides a sample Image for testing."""
    return Image(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        10,
        20,
        "2021-01-01T00:00:00",
        "bucket",
        "key",
        "abc123",
        cdn_s3_key="cdn_key",
    )


@pytest.fixture
def example_image_no_cdn_key():
    """Provides a sample Image for testing."""
    return Image(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        10,
        20,
        "2021-01-01T00:00:00",
        "bucket",
        "key",
        "abc123",
        "cdn_bucket",
    )


@pytest.mark.unit
def test_image_init_valid(example_image):
    """Test the Image constructor"""
    assert example_image.image_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert example_image.width == 10
    assert example_image.height == 20
    assert example_image.timestamp_added == "2021-01-01T00:00:00"
    assert example_image.raw_s3_bucket == "bucket"
    assert example_image.raw_s3_key == "key"
    assert example_image.sha256 == "abc123"
    assert example_image.cdn_s3_bucket == "cdn_bucket"
    assert example_image.cdn_s3_key == "cdn_key"


@pytest.mark.unit
def test_image_init_invalid_id():
    with pytest.raises(ValueError, match="uuid must be a string"):
        Image(
            1,
            10,
            20,
            "2021-01-01T00:00:00",
            "bucket",
            "key",
            sha256="abc123",
        )
    with pytest.raises(ValueError, match="uuid must be a valid UUID"):
        Image(
            "not-a-uuid",
            10,
            20,
            "2021-01-01T00:00:00",
            "bucket",
            "key",
            sha256="abc123",
        )


@pytest.mark.unit
def test_image_init_invalid_width_and_height():
    with pytest.raises(
        ValueError, match="width and height must be positive integers"
    ):
        Image(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            0,
            20,
            "2021-01-01T00:00:00",
            "bucket",
            "key",
            sha256="abc123",
        )
    with pytest.raises(
        ValueError, match="width and height must be positive integers"
    ):
        Image(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            10,
            0,
            "2021-01-01T00:00:00",
            "bucket",
            "key",
            sha256="abc123",
        )
    with pytest.raises(
        ValueError, match="width and height must be positive integers"
    ):
        Image(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            -10,
            20,
            "2021-01-01T00:00:00",
            "bucket",
            "key",
            sha256="abc123",
        )
    with pytest.raises(
        ValueError, match="width and height must be positive integers"
    ):
        Image(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            10,
            -20,
            "2021-01-01T00:00:00",
            "bucket",
            "key",
            sha256="abc123",
        )


@pytest.mark.unit
def test_image_init_invalid_timestamp():
    with pytest.raises(
        ValueError,
        match="timestamp_added must be a datetime object or a string",
    ):
        Image(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            10,
            20,
            42,
            "bucket",
            "key",
            sha256="abc123",
        )


@pytest.mark.unit
def test_image_init_invalid_s3_bucket():
    """Test that the s3 bucket is a str in the constructor"""
    with pytest.raises(ValueError, match="raw_s3_bucket must be a string"):
        Image(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            10,
            20,
            "2021-01-01T00:00:00",
            10,  # Should be a string
            "key",
            "abc123",
        )


@pytest.mark.unit
def test_image_init_invalid_s3_key():
    """Test that the s3 key is a str in the constructor"""
    with pytest.raises(ValueError, match="raw_s3_key must be a string"):
        Image(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            10,
            20,
            "2021-01-01T00:00:00",
            "bucket",
            10,  # Should be a string
            "abc123",
        )


@pytest.mark.unit
def test_image_init_invalid_sha256():
    with pytest.raises(ValueError, match="sha256 must be a string"):
        Image(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            10,
            20,
            "2021-01-01T00:00:00",
            "bucket",
            "key",
            sha256=42,
        )


@pytest.mark.unit
def test_image_init_invalid_cdn_s3_bucket():
    with pytest.raises(ValueError, match="cdn_s3_bucket must be a string"):
        Image(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            10,
            20,
            "2021-01-01T00:00:00",
            "bucket",
            "key",
            sha256="abc123",
            cdn_s3_bucket=42,
        )


@pytest.mark.unit
def test_image_init_invalid_cdn_s3_key():
    with pytest.raises(ValueError, match="cdn_s3_key must be a string"):
        Image(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            10,
            20,
            "2021-01-01T00:00:00",
            "bucket",
            "key",
            sha256="abc123",
            cdn_s3_key=42,
        )


@pytest.mark.unit
def test_image_key(example_image):
    """Test the Image.key method"""
    assert example_image.key == {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "IMAGE"},
    }


@pytest.mark.unit
def test_image_gsi1_key(example_image):
    """Test the Image.gsi1_key property"""
    assert example_image.gsi1_key == {
        "GSI1PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "GSI1SK": {"S": "IMAGE"},
    }


@pytest.mark.unit
def test_image_to_item(example_image):
    """Test the Image.to_item() method"""
    # Case: with sha256
    assert example_image.to_item() == {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "IMAGE"},
        "GSI1PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "GSI1SK": {"S": "IMAGE"},
        "GSI2PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "GSI2SK": {"S": "IMAGE"},
        "GSI3PK": {"S": "IMAGE#SCAN"},
        "GSI3SK": {"S": "NUM_RECEIPTS#00000"},
        "TYPE": {"S": "IMAGE"},
        "width": {"N": "10"},
        "height": {"N": "20"},
        "timestamp_added": {"S": "2021-01-01T00:00:00"},
        "raw_s3_bucket": {"S": "bucket"},
        "raw_s3_key": {"S": "key"},
        "sha256": {"S": "abc123"},
        "cdn_s3_bucket": {"S": "cdn_bucket"},
        "cdn_s3_key": {"S": "cdn_key"},
        "image_type": {"S": "SCAN"},
        "cdn_webp_s3_key": {"NULL": True},
        "cdn_avif_s3_key": {"NULL": True},
        # Thumbnail versions
        "cdn_thumbnail_s3_key": {"NULL": True},
        "cdn_thumbnail_webp_s3_key": {"NULL": True},
        "cdn_thumbnail_avif_s3_key": {"NULL": True},
        # Small versions
        "cdn_small_s3_key": {"NULL": True},
        "cdn_small_webp_s3_key": {"NULL": True},
        "cdn_small_avif_s3_key": {"NULL": True},
        # Medium versions
        "cdn_medium_s3_key": {"NULL": True},
        "cdn_medium_webp_s3_key": {"NULL": True},
        "cdn_medium_avif_s3_key": {"NULL": True},
        "receipt_count": {"NULL": True},
    }


@pytest.mark.unit
def test_image_to_item_no_sha(example_image_no_sha):
    """Test the Image.to_item() method"""
    # Case: without sha256
    assert example_image_no_sha.to_item() == {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "IMAGE"},
        "GSI1PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "GSI1SK": {"S": "IMAGE"},
        "GSI2PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "GSI2SK": {"S": "IMAGE"},
        "GSI3PK": {"S": "IMAGE#SCAN"},
        "GSI3SK": {"S": "NUM_RECEIPTS#00000"},
        "TYPE": {"S": "IMAGE"},
        "width": {"N": "10"},
        "height": {"N": "20"},
        "timestamp_added": {"S": "2021-01-01T00:00:00"},
        "raw_s3_bucket": {"S": "bucket"},
        "raw_s3_key": {"S": "key"},
        "sha256": {"NULL": True},
        "cdn_s3_bucket": {"S": "cdn_bucket"},
        "cdn_s3_key": {"S": "cdn_key"},
        "image_type": {"S": "SCAN"},
        "cdn_webp_s3_key": {"NULL": True},
        "cdn_avif_s3_key": {"NULL": True},
        # Thumbnail versions
        "cdn_thumbnail_s3_key": {"NULL": True},
        "cdn_thumbnail_webp_s3_key": {"NULL": True},
        "cdn_thumbnail_avif_s3_key": {"NULL": True},
        # Small versions
        "cdn_small_s3_key": {"NULL": True},
        "cdn_small_webp_s3_key": {"NULL": True},
        "cdn_small_avif_s3_key": {"NULL": True},
        # Medium versions
        "cdn_medium_s3_key": {"NULL": True},
        "cdn_medium_webp_s3_key": {"NULL": True},
        "cdn_medium_avif_s3_key": {"NULL": True},
        "receipt_count": {"NULL": True},
    }


@pytest.mark.unit
def test_image_to_item_no_cdn_bucket(example_image_no_cdn_bucket):
    """Test the Image.to_item() method"""
    # Case: without cdn_s3_bucket
    assert example_image_no_cdn_bucket.to_item() == {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "IMAGE"},
        "GSI1PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "GSI1SK": {"S": "IMAGE"},
        "GSI2PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "GSI2SK": {"S": "IMAGE"},
        "GSI3PK": {"S": "IMAGE#SCAN"},
        "GSI3SK": {"S": "NUM_RECEIPTS#00000"},
        "TYPE": {"S": "IMAGE"},
        "width": {"N": "10"},
        "height": {"N": "20"},
        "timestamp_added": {"S": "2021-01-01T00:00:00"},
        "raw_s3_bucket": {"S": "bucket"},
        "raw_s3_key": {"S": "key"},
        "sha256": {"S": "abc123"},
        "cdn_s3_bucket": {"NULL": True},
        "cdn_s3_key": {"S": "cdn_key"},
        "image_type": {"S": "SCAN"},
        "cdn_webp_s3_key": {"NULL": True},
        "cdn_avif_s3_key": {"NULL": True},
        # Thumbnail versions
        "cdn_thumbnail_s3_key": {"NULL": True},
        "cdn_thumbnail_webp_s3_key": {"NULL": True},
        "cdn_thumbnail_avif_s3_key": {"NULL": True},
        # Small versions
        "cdn_small_s3_key": {"NULL": True},
        "cdn_small_webp_s3_key": {"NULL": True},
        "cdn_small_avif_s3_key": {"NULL": True},
        # Medium versions
        "cdn_medium_s3_key": {"NULL": True},
        "cdn_medium_webp_s3_key": {"NULL": True},
        "cdn_medium_avif_s3_key": {"NULL": True},
        "receipt_count": {"NULL": True},
    }


@pytest.mark.unit
def test_image_to_item_no_cdn_key(example_image_no_cdn_key):
    """Test the Image.to_item() method"""
    # Case: without cdn_s3_key
    assert example_image_no_cdn_key.to_item() == {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "IMAGE"},
        "GSI1PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "GSI1SK": {"S": "IMAGE"},
        "GSI2PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "GSI2SK": {"S": "IMAGE"},
        "GSI3PK": {"S": "IMAGE#SCAN"},
        "GSI3SK": {"S": "NUM_RECEIPTS#00000"},
        "TYPE": {"S": "IMAGE"},
        "width": {"N": "10"},
        "height": {"N": "20"},
        "timestamp_added": {"S": "2021-01-01T00:00:00"},
        "raw_s3_bucket": {"S": "bucket"},
        "raw_s3_key": {"S": "key"},
        "sha256": {"S": "abc123"},
        "cdn_s3_bucket": {"S": "cdn_bucket"},
        "cdn_s3_key": {"NULL": True},
        "image_type": {"S": "SCAN"},
        "cdn_webp_s3_key": {"NULL": True},
        "cdn_avif_s3_key": {"NULL": True},
        # Thumbnail versions
        "cdn_thumbnail_s3_key": {"NULL": True},
        "cdn_thumbnail_webp_s3_key": {"NULL": True},
        "cdn_thumbnail_avif_s3_key": {"NULL": True},
        # Small versions
        "cdn_small_s3_key": {"NULL": True},
        "cdn_small_webp_s3_key": {"NULL": True},
        "cdn_small_avif_s3_key": {"NULL": True},
        # Medium versions
        "cdn_medium_s3_key": {"NULL": True},
        "cdn_medium_webp_s3_key": {"NULL": True},
        "cdn_medium_avif_s3_key": {"NULL": True},
        "receipt_count": {"NULL": True},
    }


@pytest.mark.unit
def test_image_repr(example_image):
    """Test the Image.__repr__() method"""
    assert repr(example_image) == (
        "Image("
        "image_id='3f52804b-2fad-4e00-92c8-b593da3a8ed3', "
        "width=10, "
        "height=20, "
        "timestamp_added=2021-01-01T00:00:00, "
        "raw_s3_bucket='bucket', "
        "raw_s3_key='key', "
        "sha256='abc123', "
        "cdn_s3_bucket='cdn_bucket', "
        "cdn_s3_key='cdn_key', "
        "cdn_webp_s3_key=None, "
        "cdn_avif_s3_key=None, "
        "cdn_thumbnail_s3_key=None, "
        "cdn_thumbnail_webp_s3_key=None, "
        "cdn_thumbnail_avif_s3_key=None, "
        "cdn_small_s3_key=None, "
        "cdn_small_webp_s3_key=None, "
        "cdn_small_avif_s3_key=None, "
        "cdn_medium_s3_key=None, "
        "cdn_medium_webp_s3_key=None, "
        "cdn_medium_avif_s3_key=None, "
        "image_type='SCAN', "
        "receipt_count=None"
        ")"
    )


@pytest.mark.unit
def test_image_iter(example_image):
    """Test the Image.__iter__() method"""
    # If you include sha256 in iteration, test that:
    assert example_image.to_dict() == {
        "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "width": 10,
        "height": 20,
        "timestamp_added": "2021-01-01T00:00:00",
        "raw_s3_bucket": "bucket",
        "raw_s3_key": "key",
        "sha256": "abc123",
        "cdn_s3_bucket": "cdn_bucket",
        "cdn_s3_key": "cdn_key",
        "cdn_webp_s3_key": None,
        "cdn_avif_s3_key": None,
        "cdn_thumbnail_s3_key": None,
        "cdn_thumbnail_webp_s3_key": None,
        "cdn_thumbnail_avif_s3_key": None,
        "cdn_small_s3_key": None,
        "cdn_small_webp_s3_key": None,
        "cdn_small_avif_s3_key": None,
        "cdn_medium_s3_key": None,
        "cdn_medium_webp_s3_key": None,
        "cdn_medium_avif_s3_key": None,
        "image_type": "SCAN",
        "receipt_count": None,
    }


@pytest.mark.unit
def test_image_eq():
    """Test the Image.__eq__() method"""

    i1 = Image(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        10,
        20,
        "2021-01-01T00:00:00",
        "bucket",
        "key",
        "abc123",
        "cdn_bucket",
        "cdn_key",
    )
    i2 = Image(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        10,
        20,
        "2021-01-01T00:00:00",
        "bucket",
        "key",
        "abc123",
        "cdn_bucket",
        "cdn_key",
    )
    i3 = Image(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed4",
        10,
        20,
        "2021-01-01T00:00:00",
        "bucket",
        "key",
        "abc123",
        "cdn_bucket",
        "cdn_key",
    )  # different id
    i4 = Image(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        20,
        20,
        "2021-01-01T00:00:00",
        "bucket",
        "key",
        "abc123",
        "cdn_bucket",
        "cdn_key",
    )  # different width
    i5 = Image(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        10,
        30,
        "2021-01-01T00:00:00",
        "bucket",
        "key",
        "abc123",
        "cdn_bucket",
        "cdn_key",
    )  # different height
    i6 = Image(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        10,
        20,
        "2021-01-01T00:00:01",
        "bucket",
        "key",
        "abc123",
        "cdn_bucket",
        "cdn_key",
    )  # different timestamp
    i7 = Image(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        10,
        20,
        "2021-01-01T00:00:00",
        "Bucket",
        "key",
        "abc123",
        "cdn_bucket",
        "cdn_key",
    )  # different raw_s3_bucket
    i8 = Image(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        10,
        20,
        "2021-01-01T00:00:00",
        "bucket",
        "Key",
        "abc123",
        "cdn_bucket",
        "cdn_key",
    )  # different raw_s3_key
    i9 = Image(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        10,
        20,
        "2021-01-01T00:00:00",
        "bucket",
        "key",
        "abc124",
        "cdn_bucket",
        "cdn_key",
    )  # different sha256
    i10 = Image(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        10,
        20,
        "2021-01-01T00:00:00",
        "bucket",
        "key",
        "abc123",
        "Cdn_bucket",
        "cdn_key",
    )  # different cdn_bucket
    i11 = Image(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        10,
        20,
        "2021-01-01T00:00:00",
        "bucket",
        "key",
        "abc123",
        "cdn_bucket",
        "Cdn_key",
    )  # different cdn_key

    assert i1 == i2, "Should be equal"
    assert i1 != i3, "Comparing different ids"
    assert i1 != i4, "Comparing different widths"
    assert i1 != i5, "Comparing different heights"
    assert i1 != i6, "Comparing different timestamps"
    assert i1 != i7, "Comparing different raw_s3_buckets"
    assert i1 != i8, "Comparing different raw_s3_keys"
    assert i1 != i9, "Comparing different sha256s"
    assert i1 != i10, "Comparing different cdn_s3_buckets"
    assert i1 != i11, "Comparing different cdn_s3_keys"

    # Compare with non-Image object
    assert i1 != 42, "Should return NotImplemented"


@pytest.mark.unit
def test_item_to_image(
    example_image,
    example_image_no_sha,
    example_image_no_cdn_bucket,
    example_image_no_cdn_key,
):
    """Test the item_to_image() function"""
    assert (
        item_to_image(example_image.to_item()) == example_image
    ), "Should convert item to Image object with SHA256"
    assert (
        item_to_image(example_image_no_sha.to_item()) == example_image_no_sha
    ), "Should convert item to Image object without SHA256"
    assert (
        item_to_image(example_image_no_cdn_bucket.to_item())
        == example_image_no_cdn_bucket
    ), "Should convert item to Image object without cdn_s3_bucket"
    assert (
        item_to_image(example_image_no_cdn_key.to_item())
        == example_image_no_cdn_key
    ), "Should convert item to Image object without cdn_s3_key"
    # Case: missing required key
    with pytest.raises(
        ValueError, match="^Invalid item format\nmissing keys: ."
    ):
        item_to_image(
            {
                "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
                "SK": {"S": "IMAGE"},
            }
        )
    # Bad item format
    with pytest.raises(ValueError, match="Error converting item to Image: "):
        item_to_image(
            {
                "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
                "SK": {"S": "IMAGE"},
                "TYPE": {"S": "IMAGE"},
                "width": {"S": "String Rather Than "},
                "height": {"N": "20"},
                "timestamp_added": {"S": "2021-01-01T00:00:00"},
                "raw_s3_bucket": {"S": "bucket"},
                "raw_s3_key": {"S": "key"},
                "sha256": {"S": "abc123"},
                "cdn_s3_bucket": {"S": "cdn_bucket"},
                "image_type": {"S": "SCAN"},
            }
        )


@pytest.mark.unit
def test_image_init_invalid_receipt_count():
    """Test that invalid receipt_count values raise appropriate errors."""

    # Test negative receipt_count
    with pytest.raises(
        ValueError, match="receipt_count must be a non-negative integer"
    ):
        Image(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            10,
            20,
            "2021-01-01T00:00:00",
            "bucket",
            "key",
            receipt_count=-1,
        )

    # Test string receipt_count
    with pytest.raises(ValueError, match="receipt_count must be an integer"):
        Image(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            10,
            20,
            "2021-01-01T00:00:00",
            "bucket",
            "key",
            receipt_count="5",
        )

    # Test float receipt_count
    with pytest.raises(ValueError, match="receipt_count must be an integer"):
        Image(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            10,
            20,
            "2021-01-01T00:00:00",
            "bucket",
            "key",
            receipt_count=5.5,
        )


@pytest.mark.unit
def test_image_init_valid_receipt_count():
    """Test that valid receipt_count values work correctly."""

    # Test positive receipt_count
    image1 = Image(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        10,
        20,
        "2021-01-01T00:00:00",
        "bucket",
        "key",
        receipt_count=5,
    )
    assert image1.receipt_count == 5

    # Test zero receipt_count
    image2 = Image(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        10,
        20,
        "2021-01-01T00:00:00",
        "bucket",
        "key",
        receipt_count=0,
    )
    assert image2.receipt_count == 0

    # Test None receipt_count (should work)
    image3 = Image(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        10,
        20,
        "2021-01-01T00:00:00",
        "bucket",
        "key",
        receipt_count=None,
    )
    assert image3.receipt_count is None
