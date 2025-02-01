import pytest
from dynamo import Letter, itemToLetter
import math


@pytest.fixture
def example_letter():
    # fmt: off
    return Letter( image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=1, word_id=2, id=3, text="0", bounding_box={ "x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, }, top_right={"x": 15.0, "y": 20.0}, top_left={"x": 10.0, "y": 20.0}, bottom_right={"x": 15.0, "y": 22.0}, bottom_left={"x": 10.0, "y": 22.0}, angle_degrees=1.0, angle_radians=5.0, confidence=0.90, )
    # fmt: on


@pytest.mark.unit
def test_init(example_letter):
    """Test the Letter constructor"""
    assert example_letter.image_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert example_letter.line_id == 1
    assert example_letter.word_id == 2
    assert example_letter.id == 3
    assert example_letter.text == "0"
    assert example_letter.bounding_box == {
        "x": 10.0,
        "y": 20.0,
        "width": 5.0,
        "height": 2.0,
    }
    assert example_letter.top_right == {"x": 15.0, "y": 20.0}
    assert example_letter.top_left == {"x": 10.0, "y": 20.0}
    assert example_letter.bottom_right == {"x": 15.0, "y": 22.0}
    assert example_letter.bottom_left == {"x": 10.0, "y": 22.0}
    assert example_letter.angle_degrees == 1.0
    assert example_letter.angle_radians == 5.0
    assert example_letter.confidence == 0.90


@pytest.mark.unit
def test_init_bad_uuid():
    """Test the Letter constructor with a bad UUID"""
    # fmt: off
    with pytest.raises(ValueError, match="uuid must be a string"):
        Letter( image_id=123, line_id=1, word_id=2, id=3, text="0", bounding_box={"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, top_right={"x": 15.0, "y": 20.0}, top_left={"x": 10.0, "y": 20.0}, bottom_right={"x": 15.0, "y": 22.0}, bottom_left={"x": 10.0, "y": 22.0}, angle_degrees=1.0, angle_radians=5.0, confidence=0.90, )
    with pytest.raises(ValueError, match="uuid must be a valid UUID"):
        Letter( image_id="not-a-uuid", line_id=1, word_id=2, id=3, text="0", bounding_box={"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, top_right={"x": 15.0, "y": 20.0}, top_left={"x": 10.0, "y": 20.0}, bottom_right={"x": 15.0, "y": 22.0}, bottom_left={"x": 10.0, "y": 22.0}, angle_degrees=1.0, angle_radians=5.0, confidence=0.90, )
    # fmt: on


@pytest.mark.unit
@pytest.mark.parametrize(
    "invalid_int",
    [
        0,
        -1,
        None,
        "string",
        1.5,
    ],
)
def test_init_invalid_line_id(invalid_int):
    """Test constructor fails when line_id is not a valid positive integer."""
    with pytest.raises(ValueError, match=r"line_id must be"):
        # fmt: off
        Letter( image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=invalid_int, word_id=1, id=1, text="H", bounding_box={"x": 1, "y": 2, "width": 3, "height": 4}, top_right={"x": 2, "y": 2}, top_left={"x": 1, "y": 2}, bottom_right={"x": 2, "y": 3}, bottom_left={"x": 1, "y": 3}, angle_degrees=0.0, angle_radians=0.0, confidence=0.5, )
        # fmt: on


@pytest.mark.unit
@pytest.mark.parametrize(
    "invalid_int",
    [
        0,
        -1,
        None,
        "string",
        1.5,
    ],
)
def test_init_invalid_word_id(invalid_int):
    """Test constructor fails when word_id is not a valid positive integer."""
    with pytest.raises(ValueError, match=r"word_id must be"):
        # fmt: off
        Letter( image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=1, word_id=invalid_int, id=1, text="H", bounding_box={"x": 1, "y": 2, "width": 3, "height": 4}, top_right={"x": 2, "y": 2}, top_left={"x": 1, "y": 2}, bottom_right={"x": 2, "y": 3}, bottom_left={"x": 1, "y": 3}, angle_degrees=0.0, angle_radians=0.0, confidence=0.5, )
        # fmt: on


@pytest.mark.unit
@pytest.mark.parametrize(
    "invalid_int",
    [
        0,
        -1,
        None,
        "string",
        1.5,
    ],
)
def test_init_invalid_id(invalid_int):
    """Test constructor fails when id is not a valid positive integer."""
    with pytest.raises(ValueError, match=r"id must be"):
        # fmt: off
        Letter( image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=1, word_id=1, id=invalid_int, text="H", bounding_box={"x": 1, "y": 2, "width": 3, "height": 4}, top_right={"x": 2, "y": 2}, top_left={"x": 1, "y": 2}, bottom_right={"x": 2, "y": 3}, bottom_left={"x": 1, "y": 3}, angle_degrees=0.0, angle_radians=0.0, confidence=0.5, )
        # fmt: on


@pytest.mark.unit
def test_init_invalid_text():
    """Test constructor fails when text is not a string."""
    # fmt: off
    with pytest.raises(ValueError, match=r"text must be a string"):
        Letter( image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=1, word_id=1, id=1, text=123, bounding_box={"x": 1, "y": 2, "width": 3, "height": 4}, top_right={"x": 2, "y": 2}, top_left={"x": 1, "y": 2}, bottom_right={"x": 2, "y": 3}, bottom_left={"x": 1, "y": 3}, angle_degrees=0.0, angle_radians=0.0, confidence=0.5, )
    with pytest.raises(ValueError, match=r"text must be exactly one character"):
        Letter( image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=1, word_id=1, id=1, text="string-longer-than-1-char", bounding_box={"x": 1, "y": 2, "width": 3, "height": 4}, top_right={"x": 2, "y": 2}, top_left={"x": 1, "y": 2}, bottom_right={"x": 2, "y": 3}, bottom_left={"x": 1, "y": 3}, angle_degrees=0.0, angle_radians=0.0, confidence=0.5, )

    # fmt: on


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_box",
    [
        {},
        {"x": 1, "width": 3, "height": 4},  # missing "y"
        {"y": 2, "width": 3, "height": 4},  # missing "x"
        {"x": "str", "y": 2, "width": 3, "height": 4},  # not float/int
    ],
)
def test_init_invalid_bounding_box(bad_box):
    """Test constructor fails when bounding_box is not valid."""
    with pytest.raises(ValueError):
        # fmt: off
        Letter( image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=1, word_id=1, id=1, text="H", bounding_box=bad_box, top_right={"x": 2, "y": 2}, top_left={"x": 1, "y": 2}, bottom_right={"x": 2, "y": 3}, bottom_left={"x": 1, "y": 3}, angle_degrees=0.0, angle_radians=0.0, confidence=0.5, )
        # fmt: on


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_point",
    [
        {},
        {"x": 1},  # missing "y"
        {"y": 2},  # missing "x"
        {"x": "str", "y": 2},
    ],
)
def test_init_invalid_top_right(bad_point):
    """Test constructor fails when top_right is not valid."""
    with pytest.raises(ValueError):
        # fmt: off
        Letter( image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=1, word_id=1, id=1, text="H", bounding_box={"x": 1, "y": 2, "width": 3, "height": 4}, top_right=bad_point, top_left={"x": 1, "y": 2}, bottom_right={"x": 2, "y": 3}, bottom_left={"x": 1, "y": 3}, angle_degrees=0.0, angle_radians=0.0, confidence=0.5, )
        # fmt: on


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_point",
    [
        {},
        {"x": 1},  # missing "y"
        {"y": 2},  # missing "x"
        {"x": "str", "y": 2},
    ],
)
def test_init_invalid_top_left(bad_point):
    """Test constructor fails when top_left is not valid."""
    with pytest.raises(ValueError):
        # fmt: off
        Letter( image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=1, word_id=1, id=1, text="H", bounding_box={"x": 1, "y": 2, "width": 3, "height": 4}, top_right={"x": 2, "y": 2}, top_left=bad_point, bottom_right={"x": 2, "y": 3}, bottom_left={"x": 1, "y": 3}, angle_degrees=0.0, angle_radians=0.0, confidence=0.5, )
        # fmt: on


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_point",
    [
        {},
        {"x": 1},  # missing "y"
        {"y": 2},  # missing "x"
        {"x": "str", "y": 2},
    ],
)
def test_init_invalid_bottom_right(bad_point):
    """Test constructor fails when bottom_right is not valid."""
    with pytest.raises(ValueError):
        # fmt: off
        Letter( image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=1, word_id=1, id=1, text="H", bounding_box={"x": 1, "y": 2, "width": 3, "height": 4}, top_right={"x": 2, "y": 2}, top_left={"x": 1, "y": 2}, bottom_right=bad_point, bottom_left={"x": 1, "y": 3}, angle_degrees=0.0, angle_radians=0.0, confidence=0.5, )
        # fmt: on


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_point",
    [
        {},
        {"x": 1},  # missing "y"
        {"y": 2},  # missing "x"
        {"x": "str", "y": 2},
    ],
)
def test_init_invalid_bottom_left(bad_point):
    """Test constructor fails when bottom_left is not valid."""
    with pytest.raises(ValueError):
        # fmt: off
        Letter( image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=1, word_id=1, id=1, text="H", bounding_box={"x": 1, "y": 2, "width": 3, "height": 4}, top_right={"x": 2, "y": 2}, top_left={"x": 1, "y": 2}, bottom_right={"x": 2, "y": 3}, bottom_left=bad_point, angle_degrees=0.0, angle_radians=0.0, confidence=0.5, )
        # fmt: on


@pytest.mark.unit
@pytest.mark.parametrize("bad_confidence", [-0.1, 0.0, 1.0001, 2, "high", None])
def test_init_invalid_confidence(bad_confidence):
    """Test constructor fails when confidence is not within (0, 1]."""
    with pytest.raises(ValueError):
        # fmt: off
        Letter( image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=1, word_id=1, id=1, text="H", bounding_box={"x": 1, "y": 2, "width": 3, "height": 4}, top_right={"x": 2, "y": 2}, top_left={"x": 1, "y": 2}, bottom_right={"x": 2, "y": 3}, bottom_left={"x": 1, "y": 3}, angle_degrees=10.0, angle_radians=0.1, confidence=bad_confidence, )
        # fmt: on


@pytest.mark.unit
def test_init_invalid_angels():
    """Test constructor fails when angle_degrees and angle_radians are not valid."""
    # fmt: off
    with pytest.raises(ValueError, match="angle_degrees must be a float or int"):
        Letter( image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=1, word_id=1, id=1, text="H", bounding_box={"x": 1, "y": 2, "width": 3, "height": 4}, top_right={"x": 2, "y": 2}, top_left={"x": 1, "y": 2}, bottom_right={"x": 2, "y": 3}, bottom_left={"x": 1, "y": 3}, angle_degrees="not-a-number", angle_radians=0.0, confidence=0.5, )
    with pytest.raises(ValueError, match="angle_radians must be a float or int"):
        Letter( image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=1, word_id=1, id=1, text="H", bounding_box={"x": 1, "y": 2, "width": 3, "height": 4}, top_right={"x": 2, "y": 2}, top_left={"x": 1, "y": 2}, bottom_right={"x": 2, "y": 3}, bottom_left={"x": 1, "y": 3}, angle_degrees=0.0, angle_radians="not-a-number", confidence=0.5, )
    # fmt: on


@pytest.mark.unit
def test_key(example_letter):
    """Test the Letter.key method"""
    assert example_letter.key() == {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "LINE#00001#WORD#00002#LETTER#00003"},
    }


@pytest.mark.unit
def test_to_item(example_letter):
    """Test the Letter.to_item method"""
    assert example_letter.to_item() == {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "LINE#00001#WORD#00002#LETTER#00003"},
        "TYPE": {"S": "LETTER"},
        "text": {"S": "0"},
        "bounding_box": {
            "M": {
                "height": {"N": "2.00000000000000000000"},
                "width": {"N": "5.00000000000000000000"},
                "x": {"N": "10.00000000000000000000"},
                "y": {"N": "20.00000000000000000000"},
            }
        },
        "top_right": {
            "M": {
                "x": {"N": "15.00000000000000000000"},
                "y": {"N": "20.00000000000000000000"},
            }
        },
        "top_left": {
            "M": {
                "x": {"N": "10.00000000000000000000"},
                "y": {"N": "20.00000000000000000000"},
            }
        },
        "bottom_right": {
            "M": {
                "x": {"N": "15.00000000000000000000"},
                "y": {"N": "22.00000000000000000000"},
            }
        },
        "bottom_left": {
            "M": {
                "x": {"N": "10.00000000000000000000"},
                "y": {"N": "22.00000000000000000000"},
            }
        },
        "angle_degrees": {"N": "1.000000000000000000"},
        "angle_radians": {"N": "5.000000000000000000"},
        "confidence": {"N": "0.90"},
    }


@pytest.mark.unit
def test_repr(example_letter):
    """Test the Letter __repr__ method"""
    # fmt: off
    assert (
        repr(example_letter) 
        == "Letter("
        "id=3, "
            "text='0', "
            "bounding_box=("
                "x= 10.0, "
                "y= 20.0, "
                "width= 5.0, "
                "height= 2.0"
            "), "
            "top_right=("
                "x= 15.0, "
                "y= 20.0"
            "), "
            "top_left=("
                "x= 10.0, "
                "y= 20.0"
            "), "
            "bottom_right=("
                "x= 15.0, "
                "y= 22.0"
            "), "
            "bottom_left=("
                "x= 10.0, "
                "y= 22.0"
            "), "
            "angle_degrees=1.0, "
            "angle_radians=5.0, "
            "confidence=0.9"
        ")"
    )
    # fmt: on


@pytest.mark.unit
def test_iter(example_letter):
    """Test the Letter.__iter__ method"""
    assert dict(example_letter) == {
        "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "line_id": 1,
        "word_id": 2,
        "id": 3,
        "text": "0",
        "bounding_box": {
            "x": 10.0,
            "y": 20.0,
            "width": 5.0,
            "height": 2.0,
        },
        "top_right": {"x": 15.0, "y": 20.0},
        "top_left": {"x": 10.0, "y": 20.0},
        "bottom_right": {"x": 15.0, "y": 22.0},
        "bottom_left": {"x": 10.0, "y": 22.0},
        "angle_degrees": 1,
        "angle_radians": 5,
        "confidence": 0.90,
    }
    assert Letter(**dict(example_letter)) == example_letter


@pytest.mark.unit
def test_eq_same(example_letter):
    """Test __eq__ with the same Letter object."""
    assert example_letter == example_letter


@pytest.mark.unit
def test_eq_different_type(example_letter):
    """Test __eq__ returns False when comparing to a different type."""
    assert example_letter != "some string"


@pytest.mark.unit
def test_eq_different_letter(example_letter):
    """Test __eq__ returns False when comparing two Letter objects with different fields."""
    diff_letter = Letter(
        image_id="aaaaaaaa-2fad-4e00-92c8-b593da3a8ed3",
        line_id=1,
        word_id=2,
        id=3,
        text="0",
        bounding_box={
            "x": 10.0,
            "y": 20.0,
            "width": 5.0,
            "height": 2.0,
        },
        top_right={"x": 15.0, "y": 20.0},
        top_left={"x": 10.0, "y": 20.0},
        bottom_right={"x": 15.0, "y": 22.0},
        bottom_left={"x": 10.0, "y": 22.0},
        angle_degrees=1.0,
        angle_radians=5.0,
        confidence=0.90,
    )
    assert example_letter != diff_letter


@pytest.mark.unit
def test_itemToLetter(example_letter):
    """Test the itemToLetter function"""
    assert itemToLetter(example_letter.to_item()) == example_letter
    # Missing keys
    with pytest.raises(ValueError, match="^Item is missing required keys: "):
        itemToLetter({})
    # Bad value types
    with pytest.raises(ValueError, match="^Error converting item to Letter:"):
        itemToLetter(
            {
                "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
                "SK": {"S": "LINE#00001#WORD#00002#LETTER#00003"},
                "TYPE": {"S": "LETTER"},
                "text": {"N": "0"},  # Should be a string
                "bounding_box": {
                    "M": {
                        "height": {"N": "2.000000000000000000"},
                        "width": {"N": "5.000000000000000000"},
                        "x": {"N": "10.000000000000000000"},
                        "y": {"N": "20.000000000000000000"},
                    }
                },
                "top_right": {
                    "M": {
                        "x": {"N": "15.000000000000000000"},
                        "y": {"N": "20.000000000000000000"},
                    }
                },
                "top_left": {
                    "M": {
                        "x": {"N": "10.000000000000000000"},
                        "y": {"N": "20.000000000000000000"},
                    }
                },
                "bottom_right": {
                    "M": {
                        "x": {"N": "15.000000000000000000"},
                        "y": {"N": "22.000000000000000000"},
                    }
                },
                "bottom_left": {
                    "M": {
                        "x": {"N": "10.000000000000000000"},
                        "y": {"N": "22.000000000000000000"},
                    }
                },
                "angle_degrees": {"N": "1.0000000000"},
                "angle_radians": {"N": "5.0000000000"},
                "confidence": {"N": "0.90"},
            }
        )


@pytest.mark.unit
def test_calculate_centroid(example_letter):
    """Test the Letter.centroid method"""
    assert example_letter.calculate_centroid() == (12.5, 21.0)


@pytest.mark.unit
def create_test_letter():
    """
    A helper function that returns a Letter object
    with easily verifiable points for testing.
    """
    return Letter(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=1,
        word_id=1,
        id=1,
        text="A",
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
        (5, -2),  # Translate right by 5, up by -2
        (0, 0),  # No translation
        (-3, 10),  # Translate left by 3, down by 10
    ],
)
def test_letter_translate(dx, dy):
    """
    Test that Letter.translate(dx, dy) shifts the corner points correctly
    and does NOT update bounding_box or angles.
    """
    letter = create_test_letter()

    # Original corners and bounding box
    orig_top_right = letter.top_right.copy()
    orig_top_left = letter.top_left.copy()
    orig_bottom_right = letter.bottom_right.copy()
    orig_bottom_left = letter.bottom_left.copy()
    orig_bb = letter.bounding_box.copy()  # bounding_box is not updated in translate

    # Perform the translation
    letter.translate(dx, dy)

    # Check corners
    assert letter.top_right["x"] == pytest.approx(orig_top_right["x"] + dx)
    assert letter.top_right["y"] == pytest.approx(orig_top_right["y"] + dy)
    assert letter.top_left["x"] == pytest.approx(orig_top_left["x"] + dx)
    assert letter.top_left["y"] == pytest.approx(orig_top_left["y"] + dy)
    assert letter.bottom_right["x"] == pytest.approx(orig_bottom_right["x"] + dx)
    assert letter.bottom_right["y"] == pytest.approx(orig_bottom_right["y"] + dy)
    assert letter.bottom_left["x"] == pytest.approx(orig_bottom_left["x"] + dx)
    assert letter.bottom_left["y"] == pytest.approx(orig_bottom_left["y"] + dy)

    # bounding_box should not change
    assert letter.bounding_box == orig_bb

    # Angles should not change
    assert letter.angle_degrees == 0.0
    assert letter.angle_radians == 0.0


@pytest.mark.unit
@pytest.mark.parametrize(
    "sx, sy",
    [
        (2, 3),  # Scale 2x horizontally, 3x vertically
        (1, 1),  # No scaling
        (0.5, 2),  # Scale down horizontally, up vertically
    ],
)
def test_letter_scale(sx, sy):
    """
    Test that Letter.scale(sx, sy) scales both the corner points and the bounding_box,
    and does NOT modify angles.
    """
    letter = create_test_letter()

    # Original corners and bounding box
    orig_top_right = letter.top_right.copy()
    orig_top_left = letter.top_left.copy()
    orig_bottom_right = letter.bottom_right.copy()
    orig_bottom_left = letter.bottom_left.copy()
    orig_bb = letter.bounding_box.copy()

    letter.scale(sx, sy)

    # Check corners
    assert letter.top_right["x"] == pytest.approx(orig_top_right["x"] * sx)
    assert letter.top_right["y"] == pytest.approx(orig_top_right["y"] * sy)
    assert letter.top_left["x"] == pytest.approx(orig_top_left["x"] * sx)
    assert letter.top_left["y"] == pytest.approx(orig_top_left["y"] * sy)
    assert letter.bottom_right["x"] == pytest.approx(orig_bottom_right["x"] * sx)
    assert letter.bottom_right["y"] == pytest.approx(orig_bottom_right["y"] * sy)
    assert letter.bottom_left["x"] == pytest.approx(orig_bottom_left["x"] * sx)
    assert letter.bottom_left["y"] == pytest.approx(orig_bottom_left["y"] * sy)

    # Check bounding_box
    assert letter.bounding_box["x"] == pytest.approx(orig_bb["x"] * sx)
    assert letter.bounding_box["y"] == pytest.approx(orig_bb["y"] * sy)
    assert letter.bounding_box["width"] == pytest.approx(orig_bb["width"] * sx)
    assert letter.bounding_box["height"] == pytest.approx(orig_bb["height"] * sy)

    # Angles should not change
    assert letter.angle_degrees == 0.0
    assert letter.angle_radians == 0.0


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
def test_letter_rotate_limited_range(angle, use_radians, should_raise):
    """
    Test that Letter.rotate(angle, origin_x, origin_y, use_radians) only rotates if angle
    is in [-90, 90] degrees or [-π/2, π/2] radians. Otherwise, raises ValueError.
    """
    letter = create_test_letter()
    orig_corners = {
        "top_right": letter.top_right.copy(),
        "top_left": letter.top_left.copy(),
        "bottom_right": letter.bottom_right.copy(),
        "bottom_left": letter.bottom_left.copy(),
    }
    orig_angle_degrees = letter.angle_degrees
    orig_angle_radians = letter.angle_radians

    if should_raise:
        with pytest.raises(ValueError):
            letter.rotate(angle, 0, 0, use_radians=use_radians)

        # Corners and angles should remain unchanged after the exception
        assert letter.top_right == orig_corners["top_right"]
        assert letter.top_left == orig_corners["top_left"]
        assert letter.bottom_right == orig_corners["bottom_right"]
        assert letter.bottom_left == orig_corners["bottom_left"]
        assert letter.angle_degrees == orig_angle_degrees
        assert letter.angle_radians == orig_angle_radians

    else:
        # Rotation should succeed without error
        letter.rotate(angle, 0, 0, use_radians=use_radians)

        # bounding_box remains the same
        assert letter.bounding_box["x"] == 10.0
        assert letter.bounding_box["y"] == 20.0
        assert letter.bounding_box["width"] == 5.0
        assert letter.bounding_box["height"] == 2.0

        # Some corners must change unless angle=0
        if angle not in (0, 0.0):
            corners_changed = (
                any(
                    letter.top_right[k] != orig_corners["top_right"][k]
                    for k in ["x", "y"]
                )
                or any(
                    letter.top_left[k] != orig_corners["top_left"][k]
                    for k in ["x", "y"]
                )
                or any(
                    letter.bottom_right[k] != orig_corners["bottom_right"][k]
                    for k in ["x", "y"]
                )
                or any(
                    letter.bottom_left[k] != orig_corners["bottom_left"][k]
                    for k in ["x", "y"]
                )
            )
            assert corners_changed, "Expected corners to change after valid rotation."
        else:
            # angle=0 => corners don't change
            assert letter.top_right == orig_corners["top_right"]
            assert letter.top_left == orig_corners["top_left"]
            assert letter.bottom_right == orig_corners["bottom_right"]
            assert letter.bottom_left == orig_corners["bottom_left"]

        # Angles should have incremented
        if use_radians:
            # angle_radians should be old + angle
            assert letter.angle_radians == pytest.approx(
                orig_angle_radians + angle, abs=1e-9
            )
            # angle_degrees should be old + angle*(180/π)
            deg_from_radians = angle * 180.0 / math.pi
            assert letter.angle_degrees == pytest.approx(
                orig_angle_degrees + deg_from_radians, abs=1e-9
            )
        else:
            # angle_degrees should be old + angle
            assert letter.angle_degrees == pytest.approx(
                orig_angle_degrees + angle, abs=1e-9
            )
            # angle_radians should be old + radians(angle)
            assert letter.angle_radians == pytest.approx(
                orig_angle_radians + math.radians(angle), abs=1e-9
            )
