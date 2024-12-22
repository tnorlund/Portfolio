from datetime import datetime
import pytest
from dynamo import Image, itemToImage


def test_init():
    """Test the Image constructor"""
    with pytest.raises(ValueError):
        Image(0, 0.5, 0.5, "2021-01-01T00:00:00", "bucket", "key")
    with pytest.raises(ValueError):
        Image(1, -0.5, 0.5, "2021-01-01T00:00:00", "bucket", "key")
    with pytest.raises(ValueError):
        Image(1, 0.5, 1.5, "2021-01-01T00:00:00", "bucket", "key")
    with pytest.raises(ValueError):
        Image(1, 0.5, 0.5, 42, "bucket", "key")
    image = Image(1, 10, 20, "2021-01-01T00:00:00", "bucket", "key")
    assert int(image.id) == 1
    assert image.width == 10
    assert image.height == 20
    assert image.timestamp_added == "2021-01-01T00:00:00"
    assert image.s3_bucket == "bucket"
    assert image.s3_key == "key"
    image = Image(1, 10, 20, datetime(2021, 1, 1, 0, 0, 0, 0), "bucket", "key")
    assert int(image.id) == 1
    assert image.width == 10
    assert image.height == 20
    assert image.timestamp_added == "2021-01-01T00:00:00"
    assert image.s3_bucket == "bucket"
    assert image.s3_key == "key"


def test_key():
    """Test the Image.key() method"""
    image = Image(1, 10, 20, "2021-01-01T00:00:00", "bucket", "key")
    assert image.key() == {"PK": {"S": "IMAGE#00001"}, "SK": {"S": "IMAGE"}}


def test_to_item():
    """Test the Image.to_item() method"""
    image = Image(1, 10, 20, "2021-01-01T00:00:00", "bucket", "key")
    assert image.to_item() == {
        "PK": {"S": "IMAGE#00001"},
        "SK": {"S": "IMAGE"},
        "Width": {"N": "10"},
        "Height": {"N": "20"},
        "TimestampAdded": {"S": "2021-01-01T00:00:00"},
        "S3Bucket": {"S": "bucket"},
        "S3Key": {"S": "key"},
    }


def test_repr():
    """Test the Image.__repr__() method"""
    image = Image(1, 10, 20, "2021-01-01T00:00:00", "bucket", "key")
    assert repr(image) == "Image(id=1, s3_key=key)"


def test_iter():
    """Test the Image.__iter__() method"""
    image = Image(1, 10, 20, "2021-01-01T00:00:00", "bucket", "key")
    assert dict(image) == {
        "id": 1,
        "width": 10,
        "height": 20,
        "timestamp_added": "2021-01-01T00:00:00",
        "s3_bucket": "bucket",
        "s3_key": "key",
    }


def test_eq():
    """Test the Image.__eq__() method"""
    image1 = Image(1, 10, 20, "2021-01-01T00:00:00", "bucket", "key")
    image2 = Image(1, 10, 20, "2021-01-01T00:00:00", "bucket", "key")
    assert image1 == image2
    image3 = Image(2, 10, 20, "2021-01-01T00:00:00", "bucket", "key")
    assert image1 != image3
    image4 = Image(1, 20, 20, "2021-01-01T00:00:00", "bucket", "key")
    assert image1 != image4
    image5 = Image(1, 10, 30, "2021-01-01T00:00:00", "bucket", "key")
    assert image1 != image5
    assert image1 != 42


def test_itemToImage():
    """Test the itemToImage() function"""
    item = {
        "PK": {"S": "IMAGE#1"},
        "SK": {"S": "IMAGE"},
        "Width": {"N": "10"},
        "Height": {"N": "20"},
        "TimestampAdded": {"S": "2021-01-01T00:00:00"},
        "S3Bucket": {"S": "bucket"},
        "S3Key": {"S": "key"},
    }
    image = itemToImage(item)
    assert image == Image(1, 10, 20, "2021-01-01T00:00:00", "bucket", "key")
    item = {
        "PK": {"S": "IMAGE#1"},
        "SK": {"S": "IMAGE"},
        "Width": {"N": "10"},
    }
    with pytest.raises(ValueError):
        itemToImage(item)
    item = {
        "PK": {"S": "IMAGE#1"},
        "SK": {"S": "IMAGE"},
        "Height": {"N": "20"},
    }
    with pytest.raises(ValueError):
        itemToImage(item)
    item = {
        "PK": {"S": "IMAGE#1"},
        "SK": {"S": "IMAGE"},
    }
    with pytest.raises(ValueError):
        itemToImage(item)
    item = {
        "PK": {"S": "IMAGE#1"},
        "SK": {"S": "IMAGE"},
        "Width": {"N": "10"},
        "Height": {"N": "20"},
        "TimestampAdded": {"S": "2021-01-01T00:00:00"},
        "S3Bucket": {"S": "bucket"},
        "Extra": {"S": "Extra"},
    }
    with pytest.raises(ValueError):
        itemToImage(item)
    item = {
        "PK": {"S": "IMAGE#1"},
        "SK": {"S": "IMAGE"},
        "Width": {"N": "10"},
        "Height": {"N": "20"},
        "TimestampAdded": {"S": "2021-01-01T00:00:00"},
        "S3Key": {"S": "key"},
        "Extra": {"S": "Extra"},
    }
    with pytest.raises(ValueError):
        itemToImage(item)
    item = {
        "PK": {"S": "IMAGE#1"},
        "SK": {"S": "IMAGE"},
        "Width": {"N": "10"},
        "Height": {"N": "20"},
        "TimestampAdded": {"S": "2021-01-01T00:00:00"},
        "S3Bucket": {"S": "bucket"},
        "S3Key": {"S": "key"},
        "Extra": {"S": "Extra"},
    }
    with pytest.raises(ValueError):
        itemToImage(item)
