import pytest
from dynamo import Image


def test_init():
    """Test the Image constructor"""
    with pytest.raises(ValueError):
        Image(0, 0.5, 0.5)
    with pytest.raises(ValueError):
        Image(1, -0.5, 0.5)
    with pytest.raises(ValueError):
        Image(1, 0.5, 1.5)
    image = Image(1, 0.5, 0.5)
    assert image.id == 1
    assert image.width == 0.5
    assert image.height == 0.5


def test_key():
    """Test the Image.key() method"""
    image = Image(1, 0.5, 0.5)
    assert image.key() == {"PK": {"S": "IMAGE#1"}, "SK": {"S": "IMAGE"}}


def test_to_item():
    """Test the Image.to_item() method"""
    image = Image(1, 0.5, 0.5)
    assert image.to_item() == {
        "PK": {"S": "IMAGE#1"},
        "SK": {"S": "IMAGE"},
        "Width": {"N": 0.5},
        "Height": {"N": 0.5},
    }

def test_repr():
    """Test the Image.__repr__() method"""
    image = Image(1, 0.5, 0.5)
    assert repr(image) == "Image(id=1, width=0.5, height=0.5)"

def test_iter():
    """Test the Image.__iter__() method"""
    image = Image(1, 0.5, 0.5)
    assert dict(image) == {"id": 1, "width": 0.5, "height": 0.5}
