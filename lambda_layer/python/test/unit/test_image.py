import pytest
from dynamo import Image, itemToImage


def test_init():
    """Test the Image constructor"""
    with pytest.raises(ValueError):
        Image(0, 0.5, 0.5)
    with pytest.raises(ValueError):
        Image(1, -0.5, 0.5)
    with pytest.raises(ValueError):
        Image(1, 0.5, 1.5)
    image = Image(1, 10, 20)
    assert image.id == 1
    assert image.width == 10
    assert image.height == 20


def test_key():
    """Test the Image.key() method"""
    image = Image(1, 10, 20)
    assert image.key() == {"PK": {"S": "IMAGE#1"}, "SK": {"S": "IMAGE"}}


def test_to_item():
    """Test the Image.to_item() method"""
    image = Image(1, 10, 20)
    assert image.to_item() == {
        "PK": {"S": "IMAGE#1"},
        "SK": {"S": "IMAGE"},
        "Width": {"N": "10"},
        "Height": {"N": "20"},
    }


def test_repr():
    """Test the Image.__repr__() method"""
    image = Image(1, 10, 20)
    assert repr(image) == "Image(id=1, width=10, height=20)"


def test_iter():
    """Test the Image.__iter__() method"""
    image = Image(1, 10, 20)
    assert dict(image) == {"id": 1, "width": 10, "height": 20}


def test_eq():
    """Test the Image.__eq__() method"""
    image1 = Image(1, 10, 20)
    image2 = Image(1, 10, 20)
    assert image1 == image2
    image3 = Image(2, 10, 20)
    assert image1 != image3
    image4 = Image(1, 20, 20)
    assert image1 != image4
    image5 = Image(1, 10, 30)
    assert image1 != image5
    assert image1 != 42


def test_itemToImage():
    """Test the itemToImage() function"""
    item = {
        "PK": {"S": "IMAGE#1"},
        "SK": {"S": "IMAGE"},
        "Width": {"N": "10"},
        "Height": {"N": "20"},
    }
    image = itemToImage(item)
    assert image == Image(1, 10, 20)
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
        "Extra": {"S": "Extra"},
    }
    with pytest.raises(ValueError):
        itemToImage(item)
    item = {
        "PK": {"S": "IMAGE#1"},
        "SK": {"S": "IMAGE"},
        "Width": {"N": "10"},
        "Height": {"N": "20"},
        "Extra": {"S": "Extra"},
    }
    with pytest.raises(ValueError):
        itemToImage(item)
    item = {
        "PK": {"S": "IMAGE#1"},
        "SK": {"S": "IMAGE"},
        "Width": {"N": "10"},
        "Height": {"N": "20"},
        "Extra": {"S": "Extra"},
    }
    with pytest.raises(ValueError):
        itemToImage(item)
    item = {
        "PK": {"S": "IMAGE#1"},
        "SK": {"S": "IMAGE"},
        "Width": {"N": "10"},
        "Height": {"N": "20"},
        "Extra": {"S": "Extra"},
    }
    with pytest.raises(ValueError):
        itemToImage(item)
