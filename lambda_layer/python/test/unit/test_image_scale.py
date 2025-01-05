import pytest
from dynamo import ScaledImage, ItemToScaledImage

def test_init():
    """Test the ScaledImage constructor
    
    An example scaled image object has these attributes:
    - image_id: 1
    - timestamp_added: "2021-01-01T00:00:00"
    - base64: "Example_long_string"
    - quality: 90
    """
    # image_id
    with pytest.raises(ValueError):
        ScaledImage(0, "2021-01-01T00:00:00", "Example_long_string", 90)
    # timestamp_added
    with pytest.raises(ValueError):
        ScaledImage(1, 42, "Example_long_string", 90)
    # base64
    with pytest.raises(ValueError):
        ScaledImage(1, "2021-01-01T00:00:00", None, 90)
    # quality
    with pytest.raises(ValueError):
        ScaledImage(1, "2021-01-01T00:00:00", "Example_long_string", 0)

    scaled_image = ScaledImage(
        1, "2021-01-01T00:00:00", "Example_long_string", 90
    )
    assert scaled_image.image_id == 1
    assert scaled_image.timestamp_added == "2021-01-01T00:00:00"
    assert scaled_image.base64 == "Example_long_string"
    assert scaled_image.quality == 90

def test_key():
    """Test the ScaledImage.key() method"""
    scaled_image = ScaledImage(1, "2021-01-01T00:00:00", "Example_long_string", 90)
    assert scaled_image.key() == {
        "PK": {"S": "IMAGE#00001"},
        "SK": {"S": "IMAGE_SCALE#00090"},
    }

def test_to_item():
    """Test the ScaledImage.to_item() method"""
    scaled_image = ScaledImage(1, "2021-01-01T00:00:00", "Example_long_string", 90)
    assert scaled_image.to_item() == {
        "PK": {"S": "IMAGE#00001"},
        "SK": {"S": "IMAGE_SCALE#00090"},
        "Type": {"S": "IMAGE_SCALE"},
        "TimestampAdded": {"S": "2021-01-01T00:00:00"},
        "Base64": {"S": "Example_long_string"},
        "Quality": {"N": "90"},
    }

def test_repr():
    """Test the ScaledImage.__repr__() method"""
    scaled_image = ScaledImage(1, "2021-01-01T00:00:00", "Example_long_string", 90)
    assert repr(scaled_image) == "ScaledImage(image_id=1, quality=90)"

def test_iter():
    """Test the ScaledImage.__iter__() method"""
    scaled_image = ScaledImage(1, "2021-01-01T00:00:00", "Example_long_string", 90)
    assert dict(scaled_image) == {
        "image_id": 1,
        "timestamp_added": "2021-01-01T00:00:00",
        "base64": "Example_long_string",
        "quality": 90,
    }

def test_eq():
    """Test the ScaledImage.__eq__() method"""
    scaled_image1 = ScaledImage(1, "2021-01-01T00:00:00", "Example_long_string", 90)
    scaled_image2 = ScaledImage(1, "2021-01-01T00:00:00", "Example_long_string", 90)
    assert scaled_image1 == scaled_image2

    scaled_image3 = ScaledImage(2, "2021-01-01T00:00:00", "Example_long_string", 90)
    assert scaled_image1 != scaled_image3, "image_id is different"

    scaled_image6 = ScaledImage(1, "2022-01-01T00:00:00", "Example_long_string", 90)
    assert scaled_image1 != scaled_image6, "timestamp_added is different"

    scaled_image7 = ScaledImage(1, "2021-01-01T00:00:00", "Example_long_string1", 90)
    assert scaled_image1 != scaled_image7, "base64 is different"

    scaled_image8 = ScaledImage(1, "2021-01-01T00:00:00", "Example_long_string", 80)
    assert scaled_image1 != scaled_image8, "quality is different"

def test_ItemToScaledImage():
    """Test the ItemToScaledImage() function"""
    item = {
        "PK": {"S": "IMAGE#00001"},
        "SK": {"S": "IMAGE_SCALE#00090"},
        "Type": {"S": "IMAGE_SCALE"},
        "TimestampAdded": {"S": "2021-01-01T00:00:00"},
        "Base64": {"S": "Example_long_string"},
        "Quality": {"N": "90"},
    }
    scaled_image = ItemToScaledImage(item)
    assert scaled_image.image_id == 1
    assert scaled_image.timestamp_added == "2021-01-01T00:00:00"
    assert scaled_image.base64 == "Example_long_string"
    assert scaled_image.quality == 90