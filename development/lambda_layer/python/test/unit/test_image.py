from datetime import datetime
import pytest
from dynamo import Image, itemToImage


def test_init():
    """Test the Image constructor"""
    # Existing ValueError checks
    with pytest.raises(ValueError):
        Image(0, 0.5, 0.5, "2021-01-01T00:00:00", "bucket", "key")
    with pytest.raises(ValueError):
        Image(1, -0.5, 0.5, "2021-01-01T00:00:00", "bucket", "key")
    with pytest.raises(ValueError):
        Image(1, 0.5, 1.5, "2021-01-01T00:00:00", "bucket", "key")
    with pytest.raises(ValueError):
        Image(1, 0.5, 0.5, 42, "bucket", "key")

    # Test with no sha256
    image = Image(1, 10, 20, "2021-01-01T00:00:00", "bucket", "key")
    assert int(image.id) == 1
    assert image.width == 10
    assert image.height == 20
    assert image.timestamp_added == "2021-01-01T00:00:00"
    assert image.s3_bucket == "bucket"
    assert image.s3_key == "key"
    assert image.sha256 is None

    # Test with sha256
    image = Image(
        1, 10, 20, datetime(2021, 1, 1, 0, 0, 0), "bucket", "key", sha256="my-sha256"
    )
    assert int(image.id) == 1
    assert image.width == 10
    assert image.height == 20
    assert image.timestamp_added == "2021-01-01T00:00:00"
    assert image.s3_bucket == "bucket"
    assert image.s3_key == "key"
    assert image.sha256 == "my-sha256"


def test_key():
    """Test the Image.key() method"""
    image = Image(1, 10, 20, "2021-01-01T00:00:00", "bucket", "key", sha256="abc123")
    assert image.key() == {"PK": {"S": "IMAGE#00001"}, "SK": {"S": "IMAGE"}}


def test_gsi1_key():
    """Test the Image.gsi1_key() method"""
    image = Image(1, 10, 20, "2021-01-01T00:00:00", "bucket", "key", sha256="abc123")
    assert image.gsi1_key() == {
        "GSI1PK": {"S": "IMAGE"},
        "GSI1SK": {"S": "IMAGE#00001"},
    }


def test_to_item():
    """Test the Image.to_item() method"""
    # Case: with sha256
    image = Image(1, 10, 20, "2021-01-01T00:00:00", "bucket", "key", sha256="abc123")
    assert image.to_item() == {
        "PK": {"S": "IMAGE#00001"},
        "SK": {"S": "IMAGE"},
        "GSI1PK": {"S": "IMAGE"},
        "GSI1SK": {"S": "IMAGE#00001"},
        "TYPE": {"S": "IMAGE"},
        "width": {"N": "10"},
        "height": {"N": "20"},
        "timestamp_added": {"S": "2021-01-01T00:00:00"},
        "s3_bucket": {"S": "bucket"},
        "s3_key": {"S": "key"},
        "sha256": {"S": "abc123"},
        "cdn_s3_bucket": {"S": ""},
        "cdn_s3_key": {"S": ""},
    }

    # Case: without sha256
    image = Image(2, 10, 20, "2022-01-01T00:00:00", "bucket2", "key2")
    item_dict = image.to_item()
    assert item_dict["PK"] == {"S": "IMAGE#00002"}
    assert "Sha256" not in item_dict, "Sha256 should not appear if not provided"


def test_repr():
    """Test the Image.__repr__() method"""
    image = Image(1, 10, 20, "2021-01-01T00:00:00", "bucket", "key")
    assert repr(image) == "Image(id=1, s3_key=key)"


def test_iter():
    """Test the Image.__iter__() method"""
    # If you include sha256 in iteration, test that:
    image = Image(1, 10, 20, "2021-01-01T00:00:00", "bucket", "key", sha256="abc123")
    assert dict(image) == {
        "id": 1,
        "width": 10,
        "height": 20,
        "timestamp_added": "2021-01-01T00:00:00",
        "s3_bucket": "bucket",
        "s3_key": "key",
        "sha256": "abc123",
    }


def test_eq():
    """Test the Image.__eq__() method"""
    image1 = Image(1, 10, 20, "2021-01-01T00:00:00", "bucket", "key", sha256="abc123")
    image2 = Image(1, 10, 20, "2021-01-01T00:00:00", "bucket", "key", sha256="abc123")
    assert image1 == image2, "Should be equal when sha256 is the same"

    image3 = Image(1, 10, 20, "2021-01-01T00:00:00", "bucket", "key", sha256="def456")
    assert image1 != image3, "Should differ if sha256 differs"

    image4 = Image(2, 10, 20, "2021-01-01T00:00:00", "bucket", "key", sha256="abc123")
    assert image1 != image4, "Should differ if ID differs"

    # Also check that it's not equal to a different type
    assert image1 != 42


def test_itemToImage():
    """Test the itemToImage() function"""
    # 1) Good item with SHA256
    item = {
        "PK": {"S": "IMAGE#00001"},
        "SK": {"S": "IMAGE"},
        "GSI1PK": {"S": "IMAGE"},
        "GSI1SK": {"S": "IMAGE#00001"},
        "TYPE": {"S": "IMAGE"},
        "width": {"N": "10"},
        "height": {"N": "20"},
        "timestamp_added": {"S": "2021-01-01T00:00:00"},
        "s3_bucket": {"S": "bucket"},
        "s3_key": {"S": "key"},
        "sha256": {"S": "abc123"},
    }
    image = itemToImage(item)
    assert image == Image(
        1, 10, 20, "2021-01-01T00:00:00", "bucket", "key", sha256="abc123"
    ), "Should convert item to Image object with SHA256"

    # 2) Good item w/o Sha256
    item_no_sha = {
        "PK": {"S": "IMAGE#00002"},
        "SK": {"S": "IMAGE"},
        "GSI1PK": {"S": "IMAGE"},
        "GSI1SK": {"S": "IMAGE#00002"},
        "TYPE": {"S": "IMAGE"},
        "width": {"N": "10"},
        "height": {"N": "20"},
        "timestamp_added": {"S": "2021-01-01T00:00:00"},
        "s3_bucket": {"S": "bucket"},
        "s3_key": {"S": "key"},
    }
    image_no_sha_obj = itemToImage(item_no_sha)
    assert image_no_sha_obj == Image(
        2, 10, 20, "2021-01-01T00:00:00", "bucket", "key"
    ), "Should convert item to Image object without SHA256"

    # 3) Various missing attributes -> still raises ValueError
    item_missing_width = {
        "PK": {"S": "IMAGE#1"},
        "SK": {"S": "IMAGE"},
        "TYPE": {"S": "IMAGE"},
        "height": {"N": "20"},
    }
    with pytest.raises(ValueError):
        itemToImage(item_missing_width)

    item_missing_height = {
        "PK": {"S": "IMAGE#00078"},
        "SK": {"S": "IMAGE"},
        "TYPE": {"S": "IMAGE"},
        "width": {"N": "2480"},
        "height": {"N": "3508"},
        "timestamp_added": {"S": "2025-01-05T19:02:12.010520"},
        "s3_bucket": {"S": "raw-image-bucket-c779c32"},
        "s3_key": {"S": "raw/d20141ee-180f-4484-9d3d-7886b78fd019.png"},
    }
    image = itemToImage(item_missing_height)
    assert image == Image(
        78,
        2480,
        3508,
        "2025-01-05T19:02:12.010520",
        "raw-image-bucket-c779c32",
        "raw/d20141ee-180f-4484-9d3d-7886b78fd019.png",
    ), "Should convert old item with no GSI1 keys to Image object"
