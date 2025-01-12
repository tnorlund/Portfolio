import pytest
from dynamo import Word, itemToWord


def test_init():
    word = Word(
        1,
        2,
        3,
        "07\/03\/2024",
        {
            "y": 0.9167082878750482,
            "width": 0.08690182470506236,
            "x": 0.4454263367632384,
            "height": 0.022867568134581906,
        },
        {"y": 0.9307722198001792, "x": 0.5323281614683008},
        {"x": 0.44837726658954413, "y": 0.9395758560096301},
        {"y": 0.9167082878750482, "x": 0.529377231641995},
        {"x": 0.4454263367632384, "y": 0.9255119240844992},
        -5.986527,
        -0.10448461,
        1,
    )
    assert word.image_id == 1
    assert word.line_id == 2
    assert word.id == 3
    assert word.text == "07\/03\/2024"
    assert word.boundingBox == {
        "y": 0.9167082878750482,
        "width": 0.08690182470506236,
        "x": 0.4454263367632384,
        "height": 0.022867568134581906,
    }
    assert word.topRight == {"y": 0.9307722198001792, "x": 0.5323281614683008}
    assert word.topLeft == {"x": 0.44837726658954413, "y": 0.9395758560096301}
    assert word.bottomRight == {"y": 0.9167082878750482, "x": 0.529377231641995}
    assert word.bottomLeft == {"x": 0.4454263367632384, "y": 0.9255119240844992}
    assert word.angleDegrees == -5.986527
    assert word.angleRadians == -0.10448461
    assert word.confidence == 1
    # Test with tags
    word = Word(
        1,
        2,
        3,
        "07\/03\/2024",
        {
            "y": 0.9167082878750482,
            "width": 0.08690182470506236,
            "x": 0.4454263367632384,
            "height": 0.022867568134581906,
        },
        {"y": 0.9307722198001792, "x": 0.5323281614683008},
        {"x": 0.44837726658954413, "y": 0.9395758560096301},
        {"y": 0.9167082878750482, "x": 0.529377231641995},
        {"x": 0.4454263367632384, "y": 0.9255119240844992},
        -5.986527,
        -0.10448461,
        1,
        ["tag1", "tag2"],
    )
    assert word.tags == ["tag1", "tag2"]


correct_word_params = {
    "image_id": 1,
    "line_id": 2,
    "id": 3,
    "text": "07\/03\/2024",
    "boundingBox": {
        "y": 0.9167082878750482,
        "width": 0.08690182470506236,
        "x": 0.4454263367632384,
        "height": 0.022867568134581906,
    },
    "topRight": {"y": 0.9307722198001792, "x": 0.5323281614683008},
    "topLeft": {"x": 0.44837726658954413, "y": 0.9395758560096301},
    "bottomRight": {"y": 0.9167082878750482, "x": 0.529377231641995},
    "bottomLeft": {"x": 0.4454263367632384, "y": 0.9255119240844992},
    "angleDegrees": -5.986527,
    "angleRadians": -0.10448461,
    "confidence": 1,
}


def test_key():
    """Test the Word key method"""
    word = Word(**correct_word_params)
    key = word.key()
    assert key == {
        "PK": {"S": "IMAGE#00001"},
        "SK": {"S": "LINE#00002#WORD#00003"},
    }, "The key should be {'PK': 'IMAGE#00001', 'SK': 'LINE#00002#WORD#00003'}"


def test_to_item():
    """Test the Word to_item method"""
    # Test with no tags
    word = Word(**correct_word_params)
    item = word.to_item()
    assert item == {
        "PK": {"S": "IMAGE#00001"},
        "SK": {"S": "LINE#00002#WORD#00003"},
        "Type": {"S": "WORD"},
        "Text": {"S": "07\/03\/2024"},
        "BoundingBox": {
            "M": {
                "x": {"N": "0.445426336763238400"},
                "y": {"N": "0.916708287875048200"},
                "width": {"N": "0.086901824705062360"},
                "height": {"N": "0.022867568134581906"},
            }
        },
        "TopRight": {
            "M": {
                "x": {"N": "0.532328161468300800"},
                "y": {"N": "0.930772219800179200"},
            }
        },
        "TopLeft": {
            "M": {
                "x": {"N": "0.448377266589544130"},
                "y": {"N": "0.939575856009630100"},
            }
        },
        "BottomRight": {
            "M": {
                "x": {"N": "0.529377231641995000"},
                "y": {"N": "0.916708287875048200"},
            }
        },
        "BottomLeft": {
            "M": {
                "x": {"N": "0.445426336763238400"},
                "y": {"N": "0.925511924084499200"},
            }
        },
        "AngleDegrees": {"N": "-5.9865270000"},
        "AngleRadians": {"N": "-0.1044846100"},
        "Confidence": {"N": "1.00"},
    }
    # Test with tags
    word = Word(**correct_word_params, tags=["tag1", "tag2"])
    item = word.to_item()
    assert item == {
        "PK": {"S": "IMAGE#00001"},
        "SK": {"S": "LINE#00002#WORD#00003"},
        "Type": {"S": "WORD"},
        "Text": {"S": "07\/03\/2024"},
        "BoundingBox": {
            "M": {
                "x": {"N": "0.445426336763238400"},
                "y": {"N": "0.916708287875048200"},
                "width": {"N": "0.086901824705062360"},
                "height": {"N": "0.022867568134581906"},
            }
        },
        "TopRight": {
            "M": {
                "x": {"N": "0.532328161468300800"},
                "y": {"N": "0.930772219800179200"},
            }
        },
        "TopLeft": {
            "M": {
                "x": {"N": "0.448377266589544130"},
                "y": {"N": "0.939575856009630100"},
            }
        },
        "BottomRight": {
            "M": {
                "x": {"N": "0.529377231641995000"},
                "y": {"N": "0.916708287875048200"},
            }
        },
        "BottomLeft": {
            "M": {
                "x": {"N": "0.445426336763238400"},
                "y": {"N": "0.925511924084499200"},
            }
        },
        "AngleDegrees": {"N": "-5.9865270000"},
        "AngleRadians": {"N": "-0.1044846100"},
        "Confidence": {"N": "1.00"},
        "Tags": {"SS": ["tag1", "tag2"]},
    }


def test_repr():
    """Test the Word __repr__ method"""
    word = Word(**correct_word_params)
    assert repr(word) == "Word(id=3, text='07\\/03\\/2024')"


def test_iter():
    """Test the Word __iter__ method"""
    word = Word(**correct_word_params)
    assert dict(word) == {
        "image_id": 1,
        "line_id": 2,
        "id": 3,
        "text": "07\/03\/2024",
        "boundingBox": {
            "x": 0.4454263367632384,
            "y": 0.9167082878750482,
            "width": 0.08690182470506236,
            "height": 0.022867568134581906,
        },
        "topRight": {"x": 0.5323281614683008, "y": 0.9307722198001792},
        "topLeft": {"x": 0.44837726658954413, "y": 0.9395758560096301},
        "bottomRight": {"x": 0.529377231641995, "y": 0.9167082878750482},
        "bottomLeft": {"x": 0.4454263367632384, "y": 0.9255119240844992},
        "angleDegrees": -5.986527,
        "angleRadians": -0.10448461,
        "confidence": 1,
        "tags": [],
    }


def test_eq():
    """Test the Word __eq__ method"""
    word1 = Word(**correct_word_params)
    word2 = Word(**correct_word_params)
    assert word1 == word2


def test_itemToWord():
    """Test the itemToWord function"""
    item = {
        "PK": {"S": "IMAGE#00001"},
        "SK": {"S": "LINE#00002#WORD#00003"},
        "Type": {"S": "WORD"},
        "Text": {"S": "07\/03\/2024"},
        "BoundingBox": {
            "M": {
                "x": {"N": "0.445426336763238400"},
                "y": {"N": "0.916708287875048200"},
                "width": {"N": "0.086901824705062360"},
                "height": {"N": "0.022867568134581906"},
            }
        },
        "TopRight": {
            "M": {
                "x": {"N": "0.532328161468300800"},
                "y": {"N": "0.930772219800179200"},
            }
        },
        "TopLeft": {
            "M": {
                "x": {"N": "0.448377266589544130"},
                "y": {"N": "0.939575856009630100"},
            }
        },
        "BottomRight": {
            "M": {
                "x": {"N": "0.529377231641995000"},
                "y": {"N": "0.916708287875048200"},
            }
        },
        "BottomLeft": {
            "M": {
                "x": {"N": "0.445426336763238400"},
                "y": {"N": "0.925511924084499200"},
            }
        },
        "AngleDegrees": {"N": "-5.9865270000"},
        "AngleRadians": {"N": "-0.1044846100"},
        "Confidence": {"N": "1.00"},
    }
    word = itemToWord(item)
    assert word == Word(**correct_word_params)
