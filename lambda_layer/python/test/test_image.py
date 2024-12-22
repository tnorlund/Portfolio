import pytest
from dynamo import Image

def test_init():
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
