import pytest
from dynamo import Word, itemToWord
import math

correct_word_params = {
    "image_id": 1,
    "line_id": 2,
    "id": 3,
    "text": "test_string",
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
    assert word.text == "test_string"
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
        "text": {"S": "test_string"},
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
        "text": {"S": "test_string"},
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
    assert repr(word) == "Word(id=3, text='test_string')"


def test_iter():
    """Test the Word __iter__ method"""
    word = Word(**correct_word_params)
    assert dict(word) == {
        "image_id": 1,
        "line_id": 2,
        "id": 3,
        "text": "test_string",
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
        "text": {"S": "test_string"},
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


def create_test_word():
    """
    A helper function that returns a Word object
    with easily verifiable points for testing.
    """
    return Word(
        id=1,
        text="Hello",
        tags=["example"],
        image_id=1,
        line_id=1,
        # In your actual Word class, you'd need bounding box / corners
        # if you plan to scale/rotate/translate them the same way as Line/Letter.
        # We'll assume Word also has these attributes:
        bounding_box={"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0},
        top_right={"x": 15.0, "y": 20.0},
        top_left={"x": 10.0, "y": 20.0},
        bottom_right={"x": 15.0, "y": 22.0},
        bottom_left={"x": 10.0, "y": 22.0},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=1.0
    )


@pytest.mark.parametrize(
    "dx, dy",
    [
        (5, -2),
        (0, 0),
        (-3, 10),
    ],
)
def test_word_translate(dx, dy):
    """
    Test that Word.translate(dx, dy) shifts corner points correctly
    without updating bounding_box or angles.
    """
    word = create_test_word()

    orig_top_right = word.top_right.copy()
    orig_top_left = word.top_left.copy()
    orig_bottom_right = word.bottom_right.copy()
    orig_bottom_left = word.bottom_left.copy()
    orig_bb = word.bounding_box.copy()

    word.translate(dx, dy)

    assert word.top_right["x"] == pytest.approx(orig_top_right["x"] + dx)
    assert word.top_right["y"] == pytest.approx(orig_top_right["y"] + dy)
    assert word.top_left["x"] == pytest.approx(orig_top_left["x"] + dx)
    assert word.top_left["y"] == pytest.approx(orig_top_left["y"] + dy)
    assert word.bottom_right["x"] == pytest.approx(orig_bottom_right["x"] + dx)
    assert word.bottom_right["y"] == pytest.approx(orig_bottom_right["y"] + dy)
    assert word.bottom_left["x"] == pytest.approx(orig_bottom_left["x"] + dx)
    assert word.bottom_left["y"] == pytest.approx(orig_bottom_left["y"] + dy)

    # bounding_box should NOT change
    assert word.bounding_box == orig_bb

    # Angles should not change
    assert word.angle_degrees == 0.0
    assert word.angle_radians == 0.0


@pytest.mark.parametrize(
    "sx, sy",
    [
        (2, 3),
        (1, 1),
        (0.5, 2),
    ],
)
def test_word_scale(sx, sy):
    """
    Test that Word.scale(sx, sy) scales corner points
    and bounding_box, but does NOT modify angles.
    """
    word = create_test_word()

    orig_top_right = word.top_right.copy()
    orig_top_left = word.top_left.copy()
    orig_bottom_right = word.bottom_right.copy()
    orig_bottom_left = word.bottom_left.copy()
    orig_bb = word.bounding_box.copy()

    word.scale(sx, sy)

    # Check corners
    assert word.top_right["x"] == pytest.approx(orig_top_right["x"] * sx)
    assert word.top_right["y"] == pytest.approx(orig_top_right["y"] * sy)
    assert word.top_left["x"] == pytest.approx(orig_top_left["x"] * sx)
    assert word.top_left["y"] == pytest.approx(orig_top_left["y"] * sy)
    assert word.bottom_right["x"] == pytest.approx(orig_bottom_right["x"] * sx)
    assert word.bottom_right["y"] == pytest.approx(orig_bottom_right["y"] * sy)
    assert word.bottom_left["x"] == pytest.approx(orig_bottom_left["x"] * sx)
    assert word.bottom_left["y"] == pytest.approx(orig_bottom_left["y"] * sy)

    # Check bounding_box
    assert word.bounding_box["x"] == pytest.approx(orig_bb["x"] * sx)
    assert word.bounding_box["y"] == pytest.approx(orig_bb["y"] * sy)
    assert word.bounding_box["width"] == pytest.approx(orig_bb["width"] * sx)
    assert word.bounding_box["height"] == pytest.approx(orig_bb["height"] * sy)

    assert word.angle_degrees == 0.0
    assert word.angle_radians == 0.0


@pytest.mark.parametrize(
    "angle, use_radians, should_raise",
    [
        # Degrees in valid range
        (90, False, False),
        (-90, False, False),
        (45, False, False),
        (0, False, False),
        # Degrees outside valid range => expect ValueError
        (91, False, True),
        (-91, False, True),
        (180, False, True),
        # Radians in valid range ([-π/2, π/2])
        (math.pi / 2, True, False),
        (-math.pi / 2, True, False),
        (0, True, False),
        (0.5, True, False),
        # Radians outside valid range => expect ValueError
        (math.pi / 2 + 0.01, True, True),
        (-math.pi / 2 - 0.01, True, True),
        (math.pi, True, True),
    ],
)
def test_word_rotate_limited_range(angle, use_radians, should_raise):
    """
    Test that Word.rotate(angle, origin_x, origin_y, use_radians) only rotates if angle
    is in [-90, 90] degrees or [-π/2, π/2] radians. Otherwise, raises ValueError.
    """
    word = create_test_word()
    orig_corners = {
        "top_right": word.top_right.copy(),
        "top_left": word.top_left.copy(),
        "bottom_right": word.bottom_right.copy(),
        "bottom_left": word.bottom_left.copy(),
    }
    orig_angle_degrees = word.angle_degrees
    orig_angle_radians = word.angle_radians

    if should_raise:
        with pytest.raises(ValueError):
            word.rotate(angle, 0, 0, use_radians=use_radians)

        assert word.top_right == orig_corners["top_right"]
        assert word.top_left == orig_corners["top_left"]
        assert word.bottom_right == orig_corners["bottom_right"]
        assert word.bottom_left == orig_corners["bottom_left"]
        assert word.angle_degrees == orig_angle_degrees
        assert word.angle_radians == orig_angle_radians

    else:
        word.rotate(angle, 0, 0, use_radians=use_radians)

        # bounding_box remains unchanged
        assert word.bounding_box["x"] == 10.0
        assert word.bounding_box["y"] == 20.0
        assert word.bounding_box["width"] == 5.0
        assert word.bounding_box["height"] == 2.0

        if angle not in (0, 0.0):
            corners_changed = (
                any(
                    word.top_right[k] != orig_corners["top_right"][k]
                    for k in ["x", "y"]
                )
                or any(
                    word.top_left[k] != orig_corners["top_left"][k] for k in ["x", "y"]
                )
                or any(
                    word.bottom_right[k] != orig_corners["bottom_right"][k]
                    for k in ["x", "y"]
                )
                or any(
                    word.bottom_left[k] != orig_corners["bottom_left"][k]
                    for k in ["x", "y"]
                )
            )
            assert corners_changed, "Expected corners to change after valid rotation."
        else:
            assert word.top_right == orig_corners["top_right"]
            assert word.top_left == orig_corners["top_left"]
            assert word.bottom_right == orig_corners["bottom_right"]
            assert word.bottom_left == orig_corners["bottom_left"]

        if use_radians:
            # angle_radians => old + angle
            assert word.angle_radians == pytest.approx(
                orig_angle_radians + angle, abs=1e-9
            )
            # angle_degrees => old + angle * (180/π)
            deg_from_radians = angle * 180.0 / math.pi
            assert word.angle_degrees == pytest.approx(
                orig_angle_degrees + deg_from_radians, abs=1e-9
            )
        else:
            # angle_degrees => old + angle
            assert word.angle_degrees == pytest.approx(
                orig_angle_degrees + angle, abs=1e-9
            )
            # angle_radians => old + radians(angle)
            assert word.angle_radians == pytest.approx(
                orig_angle_radians + math.radians(angle), abs=1e-9
            )
