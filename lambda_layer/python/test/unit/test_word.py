import pytest
from dynamo import Word, itemToWord

correct_word_params = {
    "image_id": 1,
    "line_id": 2,
    "id": 3,
    "text": "07\/03\/2024",
    "bounding_box": {
        "y": 0.9167082878750482,
        "width": 0.08690182470506236,
        "x": 0.4454263367632384,
        "height": 0.022867568134581906,
    },
    "top_right": {"y": 0.9307722198001792, "x": 0.5323281614683008},
    "top_left": {"x": 0.44837726658954413, "y": 0.9395758560096301},
    "bottom_right": {"y": 0.9167082878750482, "x": 0.529377231641995},
    "bottom_left": {"x": 0.4454263367632384, "y": 0.9255119240844992},
    "angle_degrees": -5.986527,
    "angle_radians": -0.10448461,
    "confidence": 1,
}

def test_init():
    word = Word(**correct_word_params)
    assert word.image_id == 1
    assert word.line_id == 2
    assert word.id == 3
    assert word.text == "07\/03\/2024"
    assert word.bounding_box == {
        "y": 0.9167082878750482,
        "width": 0.08690182470506236,
        "x": 0.4454263367632384,
        "height": 0.022867568134581906,
    }
    assert word.top_right == {"y": 0.9307722198001792, "x": 0.5323281614683008}
    assert word.top_left == {"x": 0.44837726658954413, "y": 0.9395758560096301}
    assert word.bottom_right == {"y": 0.9167082878750482, "x": 0.529377231641995}
    assert word.bottom_left == {"x": 0.4454263367632384, "y": 0.9255119240844992}
    assert word.angle_degrees == -5.986527
    assert word.angle_radians == -0.10448461
    assert word.confidence == 1
    # Test with tags
    word = Word(**correct_word_params, tags=["tag1", "tag2"])
    assert word.tags == ["tag1", "tag2"]


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
    assert word.to_item() == {
        "PK": {"S": "IMAGE#00001"},
        "SK": {"S": "LINE#00002#WORD#00003"},
        "TYPE": {"S": "WORD"},
        "text": {"S": "07\/03\/2024"},
        "bounding_box": {
            "M": {
                "x": {"N": "0.445426336763238400"},
                "y": {"N": "0.916708287875048200"},
                "width": {"N": "0.086901824705062360"},
                "height": {"N": "0.022867568134581906"},
            }
        },
        "top_right": {
            "M": {
                "x": {"N": "0.532328161468300800"},
                "y": {"N": "0.930772219800179200"},
            }
        },
        "top_left": {
            "M": {
                "x": {"N": "0.448377266589544130"},
                "y": {"N": "0.939575856009630100"},
            }
        },
        "bottom_right": {
            "M": {
                "x": {"N": "0.529377231641995000"},
                "y": {"N": "0.916708287875048200"},
            }
        },
        "bottom_left": {
            "M": {
                "x": {"N": "0.445426336763238400"},
                "y": {"N": "0.925511924084499200"},
            }
        },
        "angle_degrees": {"N": "-5.9865270000"},
        "angle_radians": {"N": "-0.1044846100"},
        "confidence": {"N": "1.00"},
    }, "to_item without tags"
    # Test with tags
    word = Word(**correct_word_params, tags=["tag1", "tag2"])
    item = word.to_item()
    assert item == {
        "PK": {"S": "IMAGE#00001"},
        "SK": {"S": "LINE#00002#WORD#00003"},
        "TYPE": {"S": "WORD"},
        "text": {"S": "07\/03\/2024"},
        "bounding_box": {
            "M": {
                "x": {"N": "0.445426336763238400"},
                "y": {"N": "0.916708287875048200"},
                "width": {"N": "0.086901824705062360"},
                "height": {"N": "0.022867568134581906"},
            }
        },
        "top_right": {
            "M": {
                "x": {"N": "0.532328161468300800"},
                "y": {"N": "0.930772219800179200"},
            }
        },
        "top_left": {
            "M": {
                "x": {"N": "0.448377266589544130"},
                "y": {"N": "0.939575856009630100"},
            }
        },
        "bottom_right": {
            "M": {
                "x": {"N": "0.529377231641995000"},
                "y": {"N": "0.916708287875048200"},
            }
        },
        "bottom_left": {
            "M": {
                "x": {"N": "0.445426336763238400"},
                "y": {"N": "0.925511924084499200"},
            }
        },
        "angle_degrees": {"N": "-5.9865270000"},
        "angle_radians": {"N": "-0.1044846100"},
        "confidence": {"N": "1.00"},
        "tags": {"SS": ["tag1", "tag2"]},
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
        "bounding_box": {
            "x": 0.4454263367632384,
            "y": 0.9167082878750482,
            "width": 0.08690182470506236,
            "height": 0.022867568134581906,
        },
        "top_right": {"x": 0.5323281614683008, "y": 0.9307722198001792},
        "top_left": {"x": 0.44837726658954413, "y": 0.9395758560096301},
        "bottom_right": {"x": 0.529377231641995, "y": 0.9167082878750482},
        "bottom_left": {"x": 0.4454263367632384, "y": 0.9255119240844992},
        "angle_degrees": -5.986527,
        "angle_radians": -0.10448461,
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
        "TYPE": {"S": "WORD"},
        "text": {"S": "07\/03\/2024"},
        "bounding_box": {
            "M": {
                "x": {"N": "0.445426336763238400"},
                "y": {"N": "0.916708287875048200"},
                "width": {"N": "0.086901824705062360"},
                "height": {"N": "0.022867568134581906"},
            }
        },
        "top_right": {
            "M": {
                "x": {"N": "0.532328161468300800"},
                "y": {"N": "0.930772219800179200"},
            }
        },
        "top_left": {
            "M": {
                "x": {"N": "0.448377266589544130"},
                "y": {"N": "0.939575856009630100"},
            }
        },
        "bottom_right": {
            "M": {
                "x": {"N": "0.529377231641995000"},
                "y": {"N": "0.916708287875048200"},
            }
        },
        "bottom_left": {
            "M": {
                "x": {"N": "0.445426336763238400"},
                "y": {"N": "0.925511924084499200"},
            }
        },
        "angle_degrees": {"N": "-5.9865270000"},
        "angle_radians": {"N": "-0.1044846100"},
        "confidence": {"N": "1.00"},
    }
    word = itemToWord(item)
    assert word == Word(**correct_word_params)
