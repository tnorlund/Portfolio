import pytest
from dynamo import Word, itemToWord
import math


@pytest.fixture
def example_word():
    return Word(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        2,
        3,
        "test_string",
        {
            "x": 10.0,
            "y": 20.0,
            "width": 5.0,
            "height": 2.0,
        },
        {"x": 15.0, "y": 20.0},
        {"x": 10.0, "y": 20.0},
        {"x": 15.0, "y": 22.0},
        {"x": 10.0, "y": 22.0},
        1.0,
        5.0,
        0.90,
    )


@pytest.fixture
def example_word_with_tags():
    return Word(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        2,
        3,
        "test_string",
        {
            "x": 10.0,
            "y": 20.0,
            "width": 5.0,
            "height": 2.0,
        },
        {"x": 15.0, "y": 20.0},
        {"x": 10.0, "y": 20.0},
        {"x": 15.0, "y": 22.0},
        {"x": 10.0, "y": 22.0},
        1.0,
        5.0,
        0.90,
        ["tag1", "tag2"],
    )

@pytest.mark.unit
def test_init(example_word, example_word_with_tags):
    assert example_word.image_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert example_word.line_id == 2
    assert example_word.id == 3
    assert example_word.text == "test_string"
    assert example_word.bounding_box == {
        "x": 10.0,
        "y": 20.0,
        "width": 5.0,
        "height": 2.0,
    }
    assert example_word.top_right == {"x": 15.0, "y": 20.0}
    assert example_word.top_left == {"x": 10.0, "y": 20.0}
    assert example_word.bottom_right == {"x": 15.0, "y": 22.0}
    assert example_word.bottom_left == {"x": 10.0, "y": 22.0}
    assert example_word.angle_degrees == 1
    assert example_word.angle_radians == 5
    assert example_word.confidence == 0.90
    assert example_word.tags == []
    assert example_word_with_tags.tags == ["tag1", "tag2"]

@pytest.mark.unit
def test_key(example_word):
    """Test the Word key method"""
    assert example_word.key() == {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "LINE#00002#WORD#00003"},
    }

@pytest.mark.unit
def test_to_item(example_word, example_word_with_tags):
    """Test the Word to_item method"""
    # Test with no tags
    item = example_word.to_item()
    assert item["PK"] == {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"}
    assert item["SK"] == {"S": "LINE#00002#WORD#00003"}
    assert item["TYPE"] == {"S": "WORD"}
    assert item["text"] == {"S": "test_string"}
    assert item["bounding_box"] == {
        "M": {
            "height": {"N": "2.000000000000000000"},
            "width": {"N": "5.000000000000000000"},
            "x": {"N": "10.000000000000000000"},
            "y": {"N": "20.000000000000000000"},
        }
    }
    assert item["top_right"] == {
        "M": {
            "x": {"N": "15.000000000000000000"},
            "y": {"N": "20.000000000000000000"},
        }
    }
    assert item["top_left"] == {
        "M": {
            "x": {"N": "10.000000000000000000"},
            "y": {"N": "20.000000000000000000"},
        }
    }
    assert item["bottom_right"] == {
        "M": {
            "x": {"N": "15.000000000000000000"},
            "y": {"N": "22.000000000000000000"},
        }
    }
    assert item["bottom_left"] == {
        "M": {
            "x": {"N": "10.000000000000000000"},
            "y": {"N": "22.000000000000000000"},
        }
    }
    assert item["angle_degrees"] == {"N": "1.0000000000"}
    assert item["angle_radians"] == {"N": "5.0000000000"}
    assert item["confidence"] == {"N": "0.90"}
    assert "histogram" in item
    assert "num_chars" in item
    # Test with tags
    assert example_word_with_tags.to_item()["tags"] == {"SS": ["tag1", "tag2"]}

@pytest.mark.unit
def test_repr(example_word):
    """Test the Word __repr__ method"""
    assert repr(example_word) == "Word(id=3, text='test_string')"

@pytest.mark.unit
def test_iter(example_word, example_word_with_tags):
    """Test the Word __iter__ method"""
    word_dict = dict(example_word)
    expected_keys = {
        "image_id",
        "line_id",
        "id",
        "text",
        "bounding_box",
        "top_right",
        "top_left",
        "bottom_right",
        "bottom_left",
        "angle_degrees",
        "angle_radians",
        "confidence",
        "histogram",
        "num_chars",
        "tags",
    }
    assert set(word_dict.keys()) == expected_keys
    assert word_dict["image_id"] == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert word_dict["line_id"] == 2
    assert word_dict["id"] == 3
    assert word_dict["text"] == "test_string"
    assert word_dict["bounding_box"] == {
        "x": 10.0,
        "y": 20.0,
        "width": 5.0,
        "height": 2.0,
    }
    assert word_dict["top_right"] == {"x": 15.0, "y": 20.0}
    assert word_dict["top_left"] == {"x": 10.0, "y": 20.0}
    assert word_dict["bottom_right"] == {"x": 15.0, "y": 22.0}
    assert word_dict["bottom_left"] == {"x": 10.0, "y": 22.0}
    assert word_dict["angle_degrees"] == 1
    assert word_dict["angle_radians"] == 5
    assert word_dict["confidence"] == 0.90
    assert dict(example_word_with_tags)["tags"] == ["tag1", "tag2"]

@pytest.mark.unit
def test_eq():
    """Test the Word __eq__ method"""
    # fmt: off
    w1 = Word("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 2, 3, "test_string", {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.90, ["tag1", "tag2"])
    w2 = Word("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 2, 3, "test_string", {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.90, ["tag1", "tag2"])
    w3 = Word("3f52804b-2fad-4e00-92c8-b593da3a8ed4", 2, 3, "test_string", {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.90, ["tag1", "tag2"]) # Different Image ID
    w4 = Word("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 3, 3, "test_string", {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.90, ["tag1", "tag2"]) # Different Line ID
    w5 = Word("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 2, 4, "test_string", {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.90, ["tag1", "tag2"]) # Different Word ID
    w6 = Word("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 2, 3, "Test_string", {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.90, ["tag1", "tag2"]) # Different Text
    w7 = Word("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 2, 3, "test_string", {"x": 20.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.90, ["tag1", "tag2"]) # Different Bounding Box
    w8 = Word("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 2, 3, "test_string", {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 20.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.90, ["tag1", "tag2"]) # Different Top Right
    w9 = Word("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 2, 3, "test_string", {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 15.0, "y": 20.0}, {"x": 20.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.90, ["tag1", "tag2"]) # Different Top Left
    w10 = Word("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 2, 3, "test_string", {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 20.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.90, ["tag1", "tag2"]) # Different Bottom Right
    w11 = Word("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 2, 3, "test_string", {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 20.0, "y": 22.0}, 1.0, 5.0, 0.90, ["tag1", "tag2"]) # Different Bottom Left
    w12 = Word("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 2, 3, "test_string", {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 2.0, 5.0, 0.90, ["tag1", "tag2"]) # Different Angle Degrees
    w13 = Word("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 2, 3, "test_string", {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 6.0, 0.90, ["tag1", "tag2"]) # Different Angle Radians
    w14 = Word("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 2, 3, "test_string", {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.91, ["tag1", "tag2"]) # Different Confidence
    w15 = Word("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 2, 3, "test_string", {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.90, ["tag1"]) # Different Tags
    # fmt: on

    assert w1 == w2
    assert w1 != w3
    assert w1 != w4
    assert w1 != w5
    assert w1 != w6
    assert w1 != w7
    assert w1 != w8
    assert w1 != w9
    assert w1 != w10
    assert w1 != w11
    assert w1 != w12
    assert w1 != w13
    assert w1 != w14
    assert w1 != w15

@pytest.mark.unit
def test_itemToWord(example_word, example_word_with_tags):
    """Test the itemToWord function"""
    itemToWord(example_word.to_item()) == example_word
    itemToWord(example_word_with_tags.to_item()) == example_word_with_tags

def create_test_word():
    """
    A helper function that returns a Word object
    with easily verifiable points for testing.
    """
    return Word(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        id=1,
        text="Hello",
        tags=["example"],
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
        confidence=1.0,
    )

@pytest.mark.unit
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

@pytest.mark.unit
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

@pytest.mark.unit
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
