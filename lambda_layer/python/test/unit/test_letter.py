import pytest
from dynamo import Letter, itemToLetter


def test_init():
    """Test the Letter constructor

    An example Letter object has these attributes:
    - image_id: 1
    - line_id: 1
    - word_id: 1
    - id: 1
    - text: "0"
    - x: 0.1495780447020218
    - y: 0.8868912352252727
    - width: 0.08726167491176465
    - height: 0.024234482735544627
    - angle: 7.75173
    - confidence: 1
    """
    # image_id
    with pytest.raises(ValueError):
        Letter(
            -1,
            1,
            1,
            1,
            "0",
            0.1495780447020218,
            0.8868912352252727,
            0.08726167491176465,
            0.024234482735544627,
            7.75173,
            1,
        )
        Letter(
            0.1,
            1,
            1,
            1,
            "0",
            0.1495780447020218,
            0.8868912352252727,
            0.08726167491176465,
            0.024234482735544627,
            7.75173,
            1,
        )
    # line_id
    with pytest.raises(ValueError):
        Letter(
            1,
            -1,
            1,
            1,
            "0",
            0.1495780447020218,
            0.8868912352252727,
            0.08726167491176465,
            0.024234482735544627,
            7.75173,
            1,
        )
        Letter(
            1,
            0.1,
            1,
            1,
            "0",
            0.1495780447020218,
            0.8868912352252727,
            0.08726167491176465,
            0.024234482735544627,
            7.75173,
            1,
        )
    # word_id
    with pytest.raises(ValueError):
        Letter(
            1,
            1,
            -1,
            1,
            "0",
            0.1495780447020218,
            0.8868912352252727,
            0.08726167491176465,
            0.024234482735544627,
            7.75173,
            1,
        )
        Letter(
            1,
            1,
            0.1,
            1,
            "0",
            0.1495780447020218,
            0.8868912352252727,
            0.08726167491176465,
            0.024234482735544627,
            7.75173,
            1,
        )
    # id
    with pytest.raises(ValueError):
        Letter(
            1,
            1,
            1,
            -1,
            "0",
            0.1495780447020218,
            0.8868912352252727,
            0.08726167491176465,
            0.024234482735544627,
            7.75173,
            1,
        )
        Letter(
            1,
            1,
            1,
            0.1,
            "0",
            0.1495780447020218,
            0.8868912352252727,
            0.08726167491176465,
            0.024234482735544627,
            7.75173,
            1,
        )
    # text
    with pytest.raises(ValueError):
        Letter(
            1,
            1,
            1,
            1,
            None,
            0.1495780447020218,
            0.8868912352252727,
            0.08726167491176465,
            0.024234482735544627,
            7.75173,
            1,
        )
        Letter(
            1,
            1,
            1,
            1,
            "00",
            0.1495780447020218,
            0.8868912352252727,
            0.08726167491176465,
            0.024234482735544627,
            7.75173,
            1,
        )
    # x
    with pytest.raises(ValueError):
        Letter(
            1,
            1,
            1,
            1,
            "0",
            -0.1,
            0.8868912352252727,
            0.08726167491176465,
            0.024234482735544627,
            7.75173,
            1,
        )
        Letter(
            1,
            1,
            1,
            1,
            "0",
            1,
            0.8868912352252727,
            0.08726167491176465,
            0.024234482735544627,
            7.75173,
            1,
        )
    # y
    with pytest.raises(ValueError):
        Letter(
            1,
            1,
            1,
            1,
            "0",
            0.1495780447020218,
            -0.1,
            0.08726167491176465,
            0.024234482735544627,
            7.75173,
            1,
        )
        Letter(
            1,
            1,
            1,
            1,
            "0",
            0.1495780447020218,
            1,
            0.08726167491176465,
            0.024234482735544627,
            7.75173,
            1,
        )
    # width
    with pytest.raises(ValueError):
        Letter(
            1,
            1,
            1,
            1,
            "0",
            0.1495780447020218,
            0.8868912352252727,
            -0.1,
            0.024234482735544627,
            7.75173,
            1,
        )
        Letter(
            1,
            1,
            1,
            1,
            "0",
            0.1495780447020218,
            0.8868912352252727,
            1,
            0.024234482735544627,
            7.75173,
            1,
        )
    # height
    with pytest.raises(ValueError):
        Letter(
            1,
            1,
            1,
            1,
            "0",
            0.1495780447020218,
            0.8868912352252727,
            0.08726167491176465,
            -0.1,
            7.75173,
            1,
        )
        Letter(
            1,
            1,
            1,
            1,
            "0",
            0.1495780447020218,
            0.8868912352252727,
            0.08726167491176465,
            1,
            7.75173,
            1,
        )
    # angle
    with pytest.raises(ValueError):
        Letter(
            1,
            1,
            1,
            1,
            "0",
            0.1495780447020218,
            0.8868912352252727,
            0.08726167491176465,
            0.024234482735544627,
            "Not an int nor a float",
            1,
        )
    # confidence
    with pytest.raises(ValueError):
        Letter(
            1,
            1,
            1,
            1,
            "0",
            0.1495780447020218,
            0.8868912352252727,
            0.08726167491176465,
            0.024234482735544627,
            7.75173,
            -0.1,
        )
        Letter(
            1,
            1,
            1,
            1,
            "0",
            0.1495780447020218,
            0.8868912352252727,
            0.08726167491176465,
            0.024234482735544627,
            7.75173,
            1.1,
        )

    letter = Letter(
        1,
        1,
        1,
        1,
        "0",
        0.1495780447020218,
        0.8868912352252727,
        0.08726167491176465,
        0.024234482735544627,
        7.75173,
        1,
    )
    assert letter.image_id == 1
    assert letter.line_id == 1
    assert letter.word_id == 1
    assert letter.id == 1
    assert letter.text == "0"
    assert letter.x == 0.1495780447020218
    assert letter.y == 0.8868912352252727
    assert letter.width == 0.08726167491176465
    assert letter.height == 0.024234482735544627
    assert letter.angle == 7.75173
    assert letter.confidence == 1.00

def test_key():
    """Test the Letter.key method"""
    letter = Letter(
        1,
        1,
        1,
        1,
        "0",
        0.1495780447020218,
        0.8868912352252727,
        0.08726167491176465,
        0.024234482735544627,
        7.75173,
        1,
    )
    assert letter.key() == {
        "PK": {"S": "IMAGE#00001"},
        "SK": {"S": "LINE#00001#WORD#00001#LETTER#00001"},
    }

def test_to_item():
    """Test the Letter.to_item method"""
    letter = Letter(
        1,
        1,
        1,
        1,
        "0",
        0.1495780447020218,
        0.8868912352252727,
        0.08726167491176465,
        0.024234482735544627,
        7.75173,
        1,
    )
    assert letter.to_item() == {
        "PK": {"S": "IMAGE#00001"},
        "SK": {"S": "LINE#00001#WORD#00001#LETTER#00001"},
        "Type": {"S": "LETTER"},
        "Text": {"S": "0"},
        "X": {"N": "0.14957804470202180000"},
        "Y": {"N": "0.88689123522527270000"},
        "Width": {"N": "0.08726167491176465000"},
        "Height": {"N": "0.02423448273554462700"},
        "Angle": {"N": "7.7517300000"},
        "Confidence": {"N": "1.00"},
    }

def test_repr():
    """Test the Letter.__repr__ method"""
    letter = Letter(
        1,
        1,
        1,
        1,
        "0",
        0.1495780447020218,
        0.8868912352252727,
        0.08726167491176465,
        0.024234482735544627,
        7.75173,
        1,
    )
    assert repr(letter) == "Letter(id=1, text='0')"

def test_iter():
    """Test the Letter.__iter__ method"""
    letter = Letter(
        1,
        1,
        1,
        1,
        "0",
        0.1495780447020218,
        0.8868912352252727,
        0.08726167491176465,
        0.024234482735544627,
        7.75173,
        1,
    )
    assert dict(letter) == {
        "image_id": 1,
        "line_id": 1,
        "word_id": 1,
        "id": 1,
        "text": "0",
        "x": 0.1495780447020218,
        "y": 0.8868912352252727,
        "width": 0.08726167491176465,
        "height": 0.024234482735544627,
        "angle": 7.75173,
        "confidence": 1.00,
    }

def test_eq():
    letter1 = Letter(
        1,
        1,
        1,
        1,
        "0",
        0.1495780447020218,
        0.8868912352252727,
        0.08726167491176465,
        0.024234482735544627,
        7.75173,
        1,
    )
    letter2 = Letter(
        1,
        1,
        1,
        1,
        "0",
        0.1495780447020218,
        0.8868912352252727,
        0.08726167491176465,
        0.024234482735544627,
        7.75173,
        1,
    )
    assert letter1 == letter2, "The two Letter objects should be equal"

    letter3 = Letter(
        2,
        1,
        1,
        1,
        "0",
        0.1495780447020218,
        0.8868912352252727,
        0.08726167491176465,
        0.024234482735544627,
        7.75173,
        1,
    )
    assert letter1 != letter3, "The equal operator works with different image_id"

    letter4 = Letter(
        1,
        2,
        1,
        1,
        "0",
        0.1495780447020218,
        0.8868912352252727,
        0.08726167491176465,
        0.024234482735544627,
        7.75173,
        1,
    )
    assert letter1 != letter4, "The equal operator works with different line_id"

    letter5 = Letter(
        1,
        1,
        2,
        1,
        "0",
        0.1495780447020218,
        0.8868912352252727,
        0.08726167491176465,
        0.024234482735544627,
        7.75173,
        1,
    )
    assert letter1 != letter5, "The equal operator works with different word_id"

    letter6 = Letter(
        1,
        1,
        1,
        2,
        "0",
        0.1495780447020218,
        0.8868912352252727,
        0.08726167491176465,
        0.024234482735544627,
        7.75173,
        1,
    )
    assert letter1 != letter6, "The equal operator works with different id"

    letter7 = Letter(
        1,
        1,
        1,
        1,
        "1",
        0.1495780447020218,
        0.8868912352252727,
        0.08726167491176465,
        0.024234482735544627,
        7.75173,
        1,
    )
    assert letter1 != letter7, "The equal operator works with different text"

    letter8 = Letter(
        1,
        1,
        1,
        1,
        "0",
        0.1495780447020217,
        0.8868912352252727,
        0.08726167491176465,
        0.024234482735544627,
        7.75173,
        1,
    )
    assert letter1 != letter8, "The equal operator works with different x"

    letter9 = Letter(
        1,
        1,
        1,
        1,
        "0",
        0.1495780447020218,
        0.8868912352252726,
        0.08726167491176465,
        0.024234482735544627,
        7.75173,
        1,
    )
    assert letter1 != letter9, "The equal operator works with different y"

    letter10 = Letter(
        1,
        1,
        1,
        1,
        "0",
        0.1495780447020218,
        0.8868912352252727,
        0.08726167491176464,
        0.024234482735544627,
        7.75173,
        1,
    )
    assert letter1 != letter10, "The equal operator works with different width"

    letter11 = Letter(
        1,
        1,
        1,
        1,
        "0",
        0.1495780447020218,
        0.8868912352252727,
        0.08726167491176465,
        0.024234482735544625,
        7.75173,
        1,
    )
    assert letter1 != letter11, "The equal operator works with different height"

    letter12 = Letter(
        1,
        1,
        1,
        1,
        "0",
        0.1495780447020218,
        0.8868912352252727,
        0.08726167491176465,
        0.024234482735544627,
        7.75172,
        1,
    )
    assert letter1 != letter12, "The equal operator works with different angle"

    letter13 = Letter(
        1,
        1,
        1,
        1,
        "0",
        0.1495780447020218,
        0.8868912352252727,
        0.08726167491176465,
        0.024234482735544627,
        7.75173,
        0.9,
    )
    assert letter1 != letter13, "The equal operator works with different confidence"


def test_itemToLetter():
    """Test the itemToLetter function"""
    item = {
        "PK": {"S": "IMAGE#00001"},
        "SK": {"S": "LINE#00001#WORD#00001#LETTER#00001"},
        "Type": {"S": "LETTER"},
        "Text": {"S": "0"},
        "X": {"N": "0.14957804470202180000"},
        "Y": {"N": "0.88689123522527270000"},
        "Width": {"N": "0.08726167491176465000"},
        "Height": {"N": "0.02423448273554462700"},
        "Angle": {"N": "7.7517300000"},
        "Confidence": {"N": "1.00"},
    }
    letter = itemToLetter(item)
    assert letter.image_id == 1
    assert letter.line_id == 1
    assert letter.word_id == 1
    assert letter.id == 1
    assert letter.text == "0"
    assert letter.x == 0.1495780447020218
    assert letter.y == 0.8868912352252727
    assert letter.width == 0.08726167491176465
    assert letter.height == 0.024234482735544627
    assert letter.angle == 7.75173
    assert letter.confidence == 1.00
