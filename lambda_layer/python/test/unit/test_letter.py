import pytest
from dynamo import Letter, itemToLetter

correct_letter_params = {
    "image_id": 1,
    "line_id": 1,
    "word_id": 1,
    "id": 1,
    "text": "0",
    "boundingBox": {
        "height": 0.022867568333804766,
        "width": 0.08688726243285705,
        "x": 0.4454336178993411,
        "y": 0.9167082877754368,
    },
    "topRight": {"x": 0.5323208803321982, "y": 0.930772983660083},
    "topLeft": {"x": 0.44837726707985254, "y": 0.9395758561092415},
    "bottomRight": {"x": 0.5293772311516867, "y": 0.9167082877754368},
    "bottomLeft": {"x": 0.4454336178993411, "y": 0.9255111602245953},
    "angleDegrees": -5.986527,
    "angleRadians": -0.1044846,
    "confidence": 1,
}


def test_init():
    """Test the Letter constructor"""
    letter = Letter(**correct_letter_params)
    assert letter.image_id == 1
    assert letter.line_id == 1
    assert letter.word_id == 1
    assert letter.id == 1
    assert letter.text == "0"
    assert letter.boundingBox == {
        "height": 0.022867568333804766,
        "width": 0.08688726243285705,
        "x": 0.4454336178993411,
        "y": 0.9167082877754368,
    }
    assert letter.topRight == {"x": 0.5323208803321982, "y": 0.930772983660083}
    assert letter.topLeft == {"x": 0.44837726707985254, "y": 0.9395758561092415}
    assert letter.bottomRight == {"x": 0.5293772311516867, "y": 0.9167082877754368}
    assert letter.bottomLeft == {"x": 0.4454336178993411, "y": 0.9255111602245953}
    assert letter.angleDegrees == -5.986527
    assert letter.angleRadians == -0.1044846
    assert letter.confidence == 1


def test_key():
    """Test the Letter.key method"""
    letter = Letter(**correct_letter_params)
    assert letter.key() == {
        "PK": {"S": "IMAGE#00001"},
        "SK": {"S": "LINE#00001#WORD#00001#LETTER#00001"},
    }


def test_to_item():
    """Test the Letter.to_item method"""
    letter = Letter(**correct_letter_params)
    assert letter.to_item() == {
        "PK": {"S": "IMAGE#00001"},
        "SK": {"S": "LINE#00001#WORD#00001#LETTER#00001"},
        "Type": {"S": "LETTER"},
        "Text": {"S": "0"},
        "BoundingBox": {
            "M": {
                "x": {"N": "0.445433617899341100"},
                "y": {"N": "0.916708287775436800"},
                "width": {"N": "0.086887262432857050"},
                "height": {"N": "0.022867568333804766"},
            }
        },
        "TopRight": {
            "M": {
                "x": {"N": "0.532320880332198200"},
                "y": {"N": "0.930772983660083000"},
            }
        },
        "TopLeft": {
            "M": {
                "x": {"N": "0.448377267079852540"},
                "y": {"N": "0.939575856109241500"},
            }
        },
        "BottomRight": {
            "M": {
                "x": {"N": "0.529377231151686700"},
                "y": {"N": "0.916708287775436800"},
            }
        },
        "BottomLeft": {
            "M": {
                "x": {"N": "0.445433617899341100"},
                "y": {"N": "0.925511160224595300"},
            }
        },
        "AngleDegrees": {"N": "-5.9865270000"},
        "AngleRadians": {"N": "-0.1044846000"},
        "Confidence": {"N": "1.00"},
    }


def test_repr():
    """Test the Letter.__repr__ method"""
    letter = Letter(**correct_letter_params)
    assert repr(letter) == "Letter(id=1, text='0')"


def test_iter():
    """Test the Letter.__iter__ method"""
    letter = Letter(**correct_letter_params)
    assert dict(letter) == {
        "image_id": 1,
        "line_id": 1,
        "word_id": 1,
        "id": 1,
        "text": "0",
        "boundingBox": {
            "height": 0.022867568333804766,
            "width": 0.08688726243285705,
            "x": 0.4454336178993411,
            "y": 0.9167082877754368,
        },
        "topRight": {"x": 0.5323208803321982, "y": 0.930772983660083},
        "topLeft": {"x": 0.44837726707985254, "y": 0.9395758561092415},
        "bottomRight": {"x": 0.5293772311516867, "y": 0.9167082877754368},
        "bottomLeft": {"x": 0.4454336178993411, "y": 0.9255111602245953},
        "angleDegrees": -5.986527,
        "angleRadians": -0.1044846,
        "confidence": 1.00,
    }


def test_eq():
    letter1 = Letter(**correct_letter_params)
    letter2 = Letter(**correct_letter_params)
    assert letter1 == letter2, "The two Letter objects should be equal"


def test_itemToLetter():
    """Test the itemToLetter function"""
    item = {
        "PK": {"S": "IMAGE#00001"},
        "SK": {"S": "LINE#00001#WORD#00001#LETTER#00001"},
        "Type": {"S": "LETTER"},
        "Text": {"S": "0"},
        "BoundingBox": {
            "M": {
                "x": {"N": "0.445433617899341100"},
                "y": {"N": "0.916708287775436800"},
                "width": {"N": "0.086887262432857050"},
                "height": {"N": "0.022867568333804766"},
            }
        },
        "TopRight": {
            "M": {
                "x": {"N": "0.532320880332198200"},
                "y": {"N": "0.930772983660083000"},
            }
        },
        "TopLeft": {
            "M": {
                "x": {"N": "0.448377267079852540"},
                "y": {"N": "0.939575856109241500"},
            }
        },
        "BottomRight": {
            "M": {
                "x": {"N": "0.529377231151686700"},
                "y": {"N": "0.916708287775436800"},
            }
        },
        "BottomLeft": {
            "M": {
                "x": {"N": "0.445433617899341100"},
                "y": {"N": "0.925511160224595300"},
            }
        },
        "AngleDegrees": {"N": "-5.9865270000"},
        "AngleRadians": {"N": "-0.1044846000"},
        "Confidence": {"N": "1.00"},
    }
    assert Letter(**correct_letter_params) == itemToLetter(item)
