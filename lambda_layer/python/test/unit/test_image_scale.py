import pytest
from dynamo import ScaledImage, itemToImageScale

def test_init():
    """Test the ScaledImage constructor
    
    An example scaled image object has these attributes:
    - image_id: 1,
    - width: 248,
    - height: 350,
    - timestamp_added: "2021-01-01T00:00:00",
    - base64: "Example_long_string",
    - scale: 0.1
    """
    with pytest.raises(ValueError):
        ScaledImage(0, 248, 350, "2021-01-01T00:00:00", "Example_long_string", 0.1)
    with pytest.raises(ValueError):
        ScaledImage(1, -248, 350, "2021-01-01T00:00:00", "Example_long_string", 0.1)
    with pytest.raises(ValueError):
        ScaledImage(1, 248, -350, "2021-01-01T00:00:00", "Example_long_string", 0.1)
    with pytest.raises(ValueError):
        ScaledImage(1, 248, 350, 42, "Example_long_string", 0.1)
    with pytest.raises(ValueError):
        ScaledImage(1, 248, 350, "2021-01-01T00:00:00", None, 0.1)
    with pytest.raises(ValueError):
        ScaledImage(1, 248, 350, "2021-01-01T00:00:00", "Example_long_string", -0.1)

    scaled_image = ScaledImage(
        1, 248, 350, "2021-01-01T00:00:00", "Example_long_string", 0.1
    )
    assert scaled_image.image_id == 1
    assert scaled_image.width == 248
    assert scaled_image.height == 350
    assert scaled_image.timestamp_added == "2021-01-01T00:00:00"
    assert scaled_image.base64 == "Example_long_string"
    assert scaled_image.scale == 0.1

def test_key():
    """Test the ScaledImage.key() method"""
    scaled_image = ScaledImage(1, 248, 350, "2021-01-01T00:00:00", "Example_long_string", 0.1)
    assert scaled_image.key() == {
        "PK": {"S": "IMAGE#00001"},
        "SK": {"S": "IMAGE_SCALE#0_1000"},
    }

def test_to_item():
    """Test the ScaledImage.to_item() method"""
    scaled_image = ScaledImage(1, 248, 350, "2021-01-01T00:00:00", "Example_long_string", 0.1)
    assert scaled_image.to_item() == {
        "PK": {"S": "IMAGE#00001"},
        "SK": {"S": "IMAGE_SCALE#0_1000"},
        "Type": {"S": "IMAGE_SCALE"},
        "Width": {"N": "248"},
        "Height": {"N": "350"},
        "TimestampAdded": {"S": "2021-01-01T00:00:00"},
        "Base64": {"S": "Example_long_string"},
        "Scale": {"N": "0.1"},
    }

def test_repr():
    """Test the ScaledImage.__repr__() method"""
    scaled_image = ScaledImage(1, 248, 350, "2021-01-01T00:00:00", "Example_long_string", 0.1)
    assert repr(scaled_image) == "ScaledImage(image_id=1, scale=0.1)"

def test_iter():
    """Test the ScaledImage.__iter__() method"""
    scaled_image = ScaledImage(1, 248, 350, "2021-01-01T00:00:00", "Example_long_string", 0.1)
    assert dict(scaled_image) == {
        "image_id": 1,
        "width": 248,
        "height": 350,
        "timestamp_added": "2021-01-01T00:00:00",
        "base64": "Example_long_string",
        "scale": 0.1,
    }

def test_eq():
    """Test the ScaledImage.__eq__() method"""
    scaled_image1 = ScaledImage(1, 248, 350, "2021-01-01T00:00:00", "Example_long_string", 0.1)
    scaled_image2 = ScaledImage(1, 248, 350, "2021-01-01T00:00:00", "Example_long_string", 0.1)
    assert scaled_image1 == scaled_image2

    scaled_image3 = ScaledImage(2, 248, 350, "2021-01-01T00:00:00", "Example_long_string", 0.1)
    assert scaled_image1 != scaled_image3, "image_id is different"

    scaled_image4 = ScaledImage(1, 249, 350, "2021-01-01T00:00:00", "Example_long_string", 0.1)
    assert scaled_image1 != scaled_image4, "width is different"

    scaled_image5 = ScaledImage(1, 248, 351, "2021-01-01T00:00:00", "Example_long_string", 0.1)
    assert scaled_image1 != scaled_image5, "height is different"

    scaled_image6 = ScaledImage(1, 248, 350, "2022-01-01T00:00:00", "Example_long_string", 0.1)
    assert scaled_image1 != scaled_image6, "timestamp_added is different"

    scaled_image7 = ScaledImage(1, 248, 350, "2021-01-01T00:00:00", "Example_long_string2", 0.1)
    assert scaled_image1 != scaled_image7, "base64 is different"

    scaled_image8 = ScaledImage(1, 248, 350, "2021-01-01T00:00:00", "Example_long_string", 0.2)
    assert scaled_image1 != scaled_image8, "scale is different"

def test_itemToImageScale():
    """Test the itemToImageScale() function"""
    item = {
        "PK": {"S": "IMAGE#00001"},
        "SK": {"S": "IMAGE_SCALE#0_1000"},
        "Type": {"S": "IMAGE_SCALE"},
        "Width": {"N": "248"},
        "Height": {"N": "350"},
        "TimestampAdded": {"S": "2021-01-01T00:00:00"},
        "Base64": {"S": "Example_long_string"},
        "Scale": {"N": "0.1"},
    }
    scaled_image = itemToImageScale(item)
    assert scaled_image.image_id == 1
    assert scaled_image.width == 248
    assert scaled_image.height == 350
    assert scaled_image.timestamp_added == "2021-01-01T00:00:00"
    assert scaled_image.base64 == "Example_long_string"
    assert scaled_image.scale == 0.1