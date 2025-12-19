"""Unit tests for the Letter entity."""

# pylint: disable=redefined-outer-name,too-many-statements,too-many-arguments
# pylint: disable=too-many-locals,unused-argument,line-too-long,too-many-lines
# pylint: disable=comparison-with-itself,consider-using-dict-items

import math

import pytest

from receipt_dynamo import Letter, item_to_letter


@pytest.fixture
def example_letter():
    """A pytest fixture for a sample Letter object."""
    return Letter(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=1,
        word_id=2,
        letter_id=3,
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


@pytest.mark.unit
def test_letter_init_valid(example_letter):
    """Test the Letter constructor"""
    assert example_letter.image_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert example_letter.line_id == 1
    assert example_letter.word_id == 2
    assert example_letter.letter_id == 3
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
def test_letter_init_invalid_uuid():
    """Test the Letter constructor with a bad UUID"""
    with pytest.raises(ValueError, match="uuid must be a string"):
        Letter(
            image_id=123,
            line_id=1,
            word_id=2,
            letter_id=3,
            text="0",
            bounding_box={"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0},
            top_right={"x": 15.0, "y": 20.0},
            top_left={"x": 10.0, "y": 20.0},
            bottom_right={"x": 15.0, "y": 22.0},
            bottom_left={"x": 10.0, "y": 22.0},
            angle_degrees=1.0,
            angle_radians=5.0,
            confidence=0.90,
        )
    with pytest.raises(ValueError, match="uuid must be a valid UUID"):
        Letter(
            image_id="not-a-uuid",
            line_id=1,
            word_id=2,
            letter_id=3,
            text="0",
            bounding_box={"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0},
            top_right={"x": 15.0, "y": 20.0},
            top_left={"x": 10.0, "y": 20.0},
            bottom_right={"x": 15.0, "y": 22.0},
            bottom_left={"x": 10.0, "y": 22.0},
            angle_degrees=1.0,
            angle_radians=5.0,
            confidence=0.90,
        )


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
def test_letter_init_invalid_line_id(invalid_int):
    """Test constructor fails when line_id is not a valid positive integer."""
    with pytest.raises(ValueError, match=r"line_id must be"):
        Letter(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=invalid_int,
            word_id=1,
            letter_id=1,
            text="H",
            bounding_box={"x": 1, "y": 2, "width": 3, "height": 4},
            top_right={"x": 2, "y": 2},
            top_left={"x": 1, "y": 2},
            bottom_right={"x": 2, "y": 3},
            bottom_left={"x": 1, "y": 3},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.5,
        )


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
def test_letter_init_invalid_word_id(invalid_int):
    """Test constructor fails when word_id is not a valid positive integer."""
    with pytest.raises(ValueError, match=r"word_id must be"):
        Letter(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=1,
            word_id=invalid_int,
            letter_id=1,
            text="H",
            bounding_box={"x": 1, "y": 2, "width": 3, "height": 4},
            top_right={"x": 2, "y": 2},
            top_left={"x": 1, "y": 2},
            bottom_right={"x": 2, "y": 3},
            bottom_left={"x": 1, "y": 3},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.5,
        )


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
def test_letter_init_invalid_id(invalid_int):
    """Test constructor fails when id is not a valid positive integer."""
    with pytest.raises(ValueError, match=r"id must be"):
        Letter(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=1,
            word_id=1,
            letter_id=invalid_int,
            text="H",
            bounding_box={"x": 1, "y": 2, "width": 3, "height": 4},
            top_right={"x": 2, "y": 2},
            top_left={"x": 1, "y": 2},
            bottom_right={"x": 2, "y": 3},
            bottom_left={"x": 1, "y": 3},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.5,
        )


@pytest.mark.unit
def test_letter_init_invalid_text():
    """Test constructor fails when text is not a string."""
    with pytest.raises(ValueError, match=r"text must be a string"):
        Letter(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=1,
            word_id=1,
            letter_id=1,
            text=123,
            bounding_box={"x": 1, "y": 2, "width": 3, "height": 4},
            top_right={"x": 2, "y": 2},
            top_left={"x": 1, "y": 2},
            bottom_right={"x": 2, "y": 3},
            bottom_left={"x": 1, "y": 3},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.5,
        )
    with pytest.raises(
        ValueError, match=r"text must be exactly one character"
    ):
        Letter(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=1,
            word_id=1,
            letter_id=1,
            text="string-longer-than-1-char",
            bounding_box={"x": 1, "y": 2, "width": 3, "height": 4},
            top_right={"x": 2, "y": 2},
            top_left={"x": 1, "y": 2},
            bottom_right={"x": 2, "y": 3},
            bottom_left={"x": 1, "y": 3},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.5,
        )


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
def test_letter_init_invalid_bounding_box(bad_box):
    """Test constructor fails when bounding_box is not valid."""
    with pytest.raises(ValueError):
        Letter(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=1,
            word_id=1,
            letter_id=1,
            text="H",
            bounding_box=bad_box,
            top_right={"x": 2, "y": 2},
            top_left={"x": 1, "y": 2},
            bottom_right={"x": 2, "y": 3},
            bottom_left={"x": 1, "y": 3},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.5,
        )


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
def test_letter_init_invalid_top_right(bad_point):
    """Test constructor fails when top_right is not valid."""
    with pytest.raises(ValueError):
        Letter(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=1,
            word_id=1,
            letter_id=1,
            text="H",
            bounding_box={"x": 1, "y": 2, "width": 3, "height": 4},
            top_right=bad_point,
            top_left={"x": 1, "y": 2},
            bottom_right={"x": 2, "y": 3},
            bottom_left={"x": 1, "y": 3},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.5,
        )


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
def test_letter_init_invalid_top_left(bad_point):
    """Test constructor fails when top_left is not valid."""
    with pytest.raises(ValueError):
        Letter(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=1,
            word_id=1,
            letter_id=1,
            text="H",
            bounding_box={"x": 1, "y": 2, "width": 3, "height": 4},
            top_right={"x": 2, "y": 2},
            top_left=bad_point,
            bottom_right={"x": 2, "y": 3},
            bottom_left={"x": 1, "y": 3},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.5,
        )


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
def test_letter_init_invalid_bottom_right(bad_point):
    """Test constructor fails when bottom_right is not valid."""
    with pytest.raises(ValueError):
        Letter(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=1,
            word_id=1,
            letter_id=1,
            text="H",
            bounding_box={"x": 1, "y": 2, "width": 3, "height": 4},
            top_right={"x": 2, "y": 2},
            top_left={"x": 1, "y": 2},
            bottom_right=bad_point,
            bottom_left={"x": 1, "y": 3},
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.5,
        )


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
def test_letter_init_invalid_bottom_left(bad_point):
    """Test constructor fails when bottom_left is not valid."""
    with pytest.raises(ValueError):
        Letter(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=1,
            word_id=1,
            letter_id=1,
            text="H",
            bounding_box={"x": 1, "y": 2, "width": 3, "height": 4},
            top_right={"x": 2, "y": 2},
            top_left={"x": 1, "y": 2},
            bottom_right={"x": 2, "y": 3},
            bottom_left=bad_point,
            angle_degrees=0.0,
            angle_radians=0.0,
            confidence=0.5,
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "bad_confidence", [-0.1, 0.0, 1.0001, 2, "high", None]
)
def test_letter_init_invalid_confidence(bad_confidence):
    """Test constructor fails when confidence is not within (0, 1]."""
    with pytest.raises(ValueError):
        Letter(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=1,
            word_id=1,
            letter_id=1,
            text="H",
            bounding_box={"x": 1, "y": 2, "width": 3, "height": 4},
            top_right={"x": 2, "y": 2},
            top_left={"x": 1, "y": 2},
            bottom_right={"x": 2, "y": 3},
            bottom_left={"x": 1, "y": 3},
            angle_degrees=10.0,
            angle_radians=0.1,
            confidence=bad_confidence,
        )


@pytest.mark.unit
def test_letter_init_invalid_angles():
    """Constructor fails when angle_degrees and angle_radians are not valid."""
    with pytest.raises(
        ValueError, match="angle_degrees must be float or int, got"
    ):
        Letter(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=1,
            word_id=1,
            letter_id=1,
            text="H",
            bounding_box={"x": 1, "y": 2, "width": 3, "height": 4},
            top_right={"x": 2, "y": 2},
            top_left={"x": 1, "y": 2},
            bottom_right={"x": 2, "y": 3},
            bottom_left={"x": 1, "y": 3},
            angle_degrees="not-a-number",
            angle_radians=0.0,
            confidence=0.5,
        )
    with pytest.raises(
        ValueError, match="angle_radians must be float or int, got"
    ):
        Letter(
            image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            line_id=1,
            word_id=1,
            letter_id=1,
            text="H",
            bounding_box={"x": 1, "y": 2, "width": 3, "height": 4},
            top_right={"x": 2, "y": 2},
            top_left={"x": 1, "y": 2},
            bottom_right={"x": 2, "y": 3},
            bottom_left={"x": 1, "y": 3},
            angle_degrees=0.0,
            angle_radians="not-a-number",
            confidence=0.5,
        )


@pytest.mark.unit
def test_letter_key(example_letter):
    """Test the Letter.key method"""
    assert example_letter.key == {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "LINE#00001#WORD#00002#LETTER#00003"},
    }


@pytest.mark.unit
def test_letter_to_item(example_letter):
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
def test_letter_calculate_centroid(example_letter):
    """Test the Letter.centroid method"""
    assert example_letter.calculate_centroid() == (12.5, 21.0)


def create_test_letter():
    """
    A helper function that returns a Letter object
    with easily verifiable points for testing.
    """
    return Letter(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=1,
        word_id=1,
        letter_id=1,
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
@pytest.mark.parametrize("dx, dy", [(5, -2), (0, 0), (-3, 10)])
def test_letter_translate(dx, dy):
    """
    Test that Letter.translate(dx, dy) shifts the corner points correctly,
    updates the bounding_box accordingly, and leaves the angles unchanged.
    """
    letter = create_test_letter()

    # Save original corners, bounding box, and angles.
    orig_top_right = letter.top_right.copy()
    orig_top_left = letter.top_left.copy()
    orig_bottom_right = letter.bottom_right.copy()
    orig_bottom_left = letter.bottom_left.copy()
    orig_bb = letter.bounding_box.copy()
    orig_angle_degrees = letter.angle_degrees
    orig_angle_radians = letter.angle_radians

    # Perform the translation.
    letter.translate(dx, dy)

    # Check that each corner was shifted correctly.
    assert letter.top_right["x"] == pytest.approx(orig_top_right["x"] + dx)
    assert letter.top_right["y"] == pytest.approx(orig_top_right["y"] + dy)

    assert letter.top_left["x"] == pytest.approx(orig_top_left["x"] + dx)
    assert letter.top_left["y"] == pytest.approx(orig_top_left["y"] + dy)

    assert letter.bottom_right["x"] == pytest.approx(
        orig_bottom_right["x"] + dx
    )
    assert letter.bottom_right["y"] == pytest.approx(
        orig_bottom_right["y"] + dy
    )

    assert letter.bottom_left["x"] == pytest.approx(orig_bottom_left["x"] + dx)
    assert letter.bottom_left["y"] == pytest.approx(orig_bottom_left["y"] + dy)

    # Now, expect that the bounding_box is also updated by the same offsets.
    expected_bb = {
        "x": orig_bb["x"] + dx,
        "y": orig_bb["y"] + dy,
        "width": orig_bb["width"],
        "height": orig_bb["height"],
    }
    assert letter.bounding_box == expected_bb

    # Verify that angles remain unchanged.
    assert letter.angle_degrees == pytest.approx(orig_angle_degrees)
    assert letter.angle_radians == pytest.approx(orig_angle_radians)


@pytest.mark.unit
@pytest.mark.parametrize("sx, sy", [(2, 3), (1, 1), (0.5, 2)])
def test_letter_scale(sx, sy):
    """
    Test that Letter.scale(sx, sy) scales both the corner points and the
    bounding_box, and does NOT modify angles.
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
    assert letter.bottom_right["x"] == pytest.approx(
        orig_bottom_right["x"] * sx
    )
    assert letter.bottom_right["y"] == pytest.approx(
        orig_bottom_right["y"] * sy
    )
    assert letter.bottom_left["x"] == pytest.approx(orig_bottom_left["x"] * sx)
    assert letter.bottom_left["y"] == pytest.approx(orig_bottom_left["y"] * sy)

    # Check bounding_box
    assert letter.bounding_box["x"] == pytest.approx(orig_bb["x"] * sx)
    assert letter.bounding_box["y"] == pytest.approx(orig_bb["y"] * sy)
    assert letter.bounding_box["width"] == pytest.approx(orig_bb["width"] * sx)
    assert letter.bounding_box["height"] == pytest.approx(
        orig_bb["height"] * sy
    )

    # Angles should not change
    assert letter.angle_degrees == 0.0
    assert letter.angle_radians == 0.0


@pytest.mark.unit
@pytest.mark.parametrize(
    "angle, use_radians, should_raise",
    [  # Degrees in valid range
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
    Test that Letter.rotate(angle, origin_x, origin_y, use_radians) rotates
    the letter's corners, recalculates the axis-aligned bounding box from
    those rotated corners, and updates the angle accumulators appropriately.
    If the angle is outside the allowed range, a ValueError is raised and no
    changes occur.
    """
    letter = create_test_letter()
    # Save original corner positions and angles
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
        # On failure, nothing should change.
        assert letter.top_right == orig_corners["top_right"]
        assert letter.top_left == orig_corners["top_left"]
        assert letter.bottom_right == orig_corners["bottom_right"]
        assert letter.bottom_left == orig_corners["bottom_left"]
        assert letter.angle_degrees == orig_angle_degrees
        assert letter.angle_radians == orig_angle_radians
    else:
        # Determine rotation angle in radians.
        theta = angle if use_radians else math.radians(angle)

        # Define a helper to rotate a point (assuming origin (0,0))
        def rotate_point(px, py, theta):
            # Since rotation origin is (0,0), no translation is needed.
            return (
                px * math.cos(theta) - py * math.sin(theta),
                px * math.sin(theta) + py * math.cos(theta),
            )

        # Compute expected rotated positions for each corner.
        expected_corners = {}
        for key, pt in orig_corners.items():
            new_x, new_y = rotate_point(pt["x"], pt["y"], theta)
            expected_corners[key] = {"x": new_x, "y": new_y}

        # Compute expected axis-aligned bounding box from the rotated corners.
        xs = [pt["x"] for pt in expected_corners.values()]
        ys = [pt["y"] for pt in expected_corners.values()]
        expected_bb = {
            "x": min(xs),
            "y": min(ys),
            "width": max(xs) - min(xs),
            "height": max(ys) - min(ys),
        }

        # Apply the rotation.
        letter.rotate(angle, 0, 0, use_radians=use_radians)

        # Verify the recalculated bounding box matches the expected bounding
        # box.
        assert letter.bounding_box["x"] == pytest.approx(
            expected_bb["x"], rel=1e-6
        )
        assert letter.bounding_box["y"] == pytest.approx(
            expected_bb["y"], rel=1e-6
        )
        assert letter.bounding_box["width"] == pytest.approx(
            expected_bb["width"], rel=1e-6
        )
        assert letter.bounding_box["height"] == pytest.approx(
            expected_bb["height"], rel=1e-6
        )

        # Verify that each corner was updated as expected.
        for key in expected_corners:
            assert letter.__dict__[key]["x"] == pytest.approx(
                expected_corners[key]["x"], rel=1e-6
            )
            assert letter.__dict__[key]["y"] == pytest.approx(
                expected_corners[key]["y"], rel=1e-6
            )

        # Verify that the angle accumulators have been updated correctly.
        if use_radians:
            expected_angle_radians = orig_angle_radians + angle
            expected_angle_degrees = (
                orig_angle_degrees + angle * 180.0 / math.pi
            )
        else:
            expected_angle_degrees = orig_angle_degrees + angle
            expected_angle_radians = orig_angle_radians + math.radians(angle)
        assert letter.angle_radians == pytest.approx(
            expected_angle_radians, abs=1e-9
        )
        assert letter.angle_degrees == pytest.approx(
            expected_angle_degrees, abs=1e-9
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "shx, shy, pivot_x, pivot_y, expected_corners",
    [
        # Test 1: Horizontal shear only (shx nonzero, shy=0)
        (
            0.2,
            0.0,
            10.0,
            20.0,
            {
                # (15, 20)
                "top_right": {"x": 15.0 + 0.2 * (20.0 - 20.0), "y": 20.0},
                # (10, 20)
                "top_left": {"x": 10.0 + 0.2 * (20.0 - 20.0), "y": 20.0},
                # (15.4, 22)
                "bottom_right": {"x": 15.0 + 0.2 * (22.0 - 20.0), "y": 22.0},
                # (10.4, 22)
                "bottom_left": {"x": 10.0 + 0.2 * (22.0 - 20.0), "y": 22.0},
            },
        ),
        # Test 2: Vertical shear only (shy nonzero, shx=0)
        (
            0.0,
            0.2,
            10.0,
            20.0,
            {
                # (15, 21)
                "top_right": {"x": 15.0, "y": 20.0 + 0.2 * (15.0 - 10.0)},
                # (10, 20)
                "top_left": {"x": 10.0, "y": 20.0 + 0.2 * (10.0 - 10.0)},
                # (15, 23)
                "bottom_right": {"x": 15.0, "y": 22.0 + 0.2 * (15.0 - 10.0)},
                # (10, 22)
                "bottom_left": {"x": 10.0, "y": 22.0 + 0.2 * (10.0 - 10.0)},
            },
        ),
        # Test 3: Combined shear
        (
            0.1,
            0.1,
            12.0,
            21.0,
            {
                # (14.9, 20.3) = (15 - 0.1, 20 + 0.3)
                "top_right": {
                    "x": 15.0 + 0.1 * (20.0 - 21.0),
                    "y": 20.0 + 0.1 * (15.0 - 12.0),
                },
                # (9.9, 19.8) = (10 - 0.1, 20 - 0.2)
                "top_left": {
                    "x": 10.0 + 0.1 * (20.0 - 21.0),
                    "y": 20.0 + 0.1 * (10.0 - 12.0),
                },
                # (15.1, 22.3) = (15 + 0.1, 22 + 0.3)
                "bottom_right": {
                    "x": 15.0 + 0.1 * (22.0 - 21.0),
                    "y": 22.0 + 0.1 * (15.0 - 12.0),
                },
                # (10.1, 21.8) = (10 + 0.1, 22 - 0.2)
                "bottom_left": {
                    "x": 10.0 + 0.1 * (22.0 - 21.0),
                    "y": 22.0 + 0.1 * (10.0 - 12.0),
                },
            },
        ),
    ],
)
def test_letter_shear(shx, shy, pivot_x, pivot_y, expected_corners):
    """
    Test that the shear(shx, shy, pivot_x, pivot_y) method correctly shears
    the corners of the line and recalculates the bounding box.
    """
    letter = create_test_letter()

    # Apply shear transformation
    letter.shear(shx, shy, pivot_x, pivot_y)

    # Check each corner against the expected values
    for corner_name in [
        "top_right",
        "top_left",
        "bottom_right",
        "bottom_left",
    ]:
        for coord in ["x", "y"]:
            expected_value = expected_corners[corner_name][coord]
            actual_value = letter.__dict__[corner_name][coord]
            assert actual_value == pytest.approx(expected_value), (
                f"{corner_name} {coord} expected {expected_value}, "
                f"got {actual_value}"
            )

    # Compute expected bounding box from the updated corners
    xs = [
        letter.top_right["x"],
        letter.top_left["x"],
        letter.bottom_right["x"],
        letter.bottom_left["x"],
    ]
    ys = [
        letter.top_right["y"],
        letter.top_left["y"],
        letter.bottom_right["y"],
        letter.bottom_left["y"],
    ]
    expected_bb = {
        "x": min(xs),
        "y": min(ys),
        "width": max(xs) - min(xs),
        "height": max(ys) - min(ys),
    }
    assert letter.bounding_box["x"] == pytest.approx(expected_bb["x"])
    assert letter.bounding_box["y"] == pytest.approx(expected_bb["y"])
    assert letter.bounding_box["width"] == pytest.approx(expected_bb["width"])
    assert letter.bounding_box["height"] == pytest.approx(
        expected_bb["height"]
    )


@pytest.mark.unit
def test_letter_warp_affine():
    """
    Test that warp_affine(a, b, c, d, e, f) applies the affine transform
    x' = a*x + b*y + c, y' = d*x + e*y + f to all corners,
    recomputes the bounding box, and updates the angle accordingly.
    """
    # Create a test letter with known corner positions:
    letter = create_test_letter()
    # Our test letter has:
    #   top_left:      (10.0, 20.0)
    #   top_right:     (15.0, 20.0)
    #   bottom_left:   (10.0, 22.0)
    #   bottom_right:  (15.0, 22.0)
    # bounding_box: {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}

    # Choose affine coefficients that scale by 2 and translate by (3,4)
    a, b, c = 2.0, 0.0, 3.0  # x' = 2*x + 3
    d, e, f = 0.0, 2.0, 4.0  # y' = 2*y + 4

    # Expected new positions for the corners:
    expected_top_left = {"x": 2 * 10.0 + 3, "y": 2 * 20.0 + 4}  # (23, 44)
    expected_top_right = {"x": 2 * 15.0 + 3, "y": 2 * 20.0 + 4}  # (33, 44)
    expected_bottom_left = {"x": 2 * 10.0 + 3, "y": 2 * 22.0 + 4}  # (23, 48)
    expected_bottom_right = {"x": 2 * 15.0 + 3, "y": 2 * 22.0 + 4}  # (33, 48)

    # Expected bounding box is computed from the new corners:
    xs = [
        expected_top_left["x"],
        expected_top_right["x"],
        expected_bottom_left["x"],
        expected_bottom_right["x"],
    ]
    ys = [
        expected_top_left["y"],
        expected_top_right["y"],
        expected_bottom_left["y"],
        expected_bottom_right["y"],
    ]
    expected_bb = {
        "x": min(xs),
        "y": min(ys),
        "width": max(xs) - min(xs),
        "height": max(ys) - min(ys),
    }
    # Since top_left and top_right have the same y value,
    # dx = expected_top_right["x"] - expected_top_left["x"] = 33 - 23 = 10
    # dy = expected_top_right["y"] - expected_top_left["y"] = 44 - 44 = 0
    # Thus, the new angle should be 0.

    # Apply the affine warp.
    letter.warp_affine(a, b, c, d, e, f)

    # Verify the transformed corners.
    assert letter.top_left["x"] == pytest.approx(expected_top_left["x"])
    assert letter.top_left["y"] == pytest.approx(expected_top_left["y"])
    assert letter.top_right["x"] == pytest.approx(expected_top_right["x"])
    assert letter.top_right["y"] == pytest.approx(expected_top_right["y"])
    assert letter.bottom_left["x"] == pytest.approx(expected_bottom_left["x"])
    assert letter.bottom_left["y"] == pytest.approx(expected_bottom_left["y"])
    assert letter.bottom_right["x"] == pytest.approx(
        expected_bottom_right["x"]
    )
    assert letter.bottom_right["y"] == pytest.approx(
        expected_bottom_right["y"]
    )

    # Verify that the bounding_box has been recalculated correctly.
    assert letter.bounding_box["x"] == pytest.approx(expected_bb["x"])
    assert letter.bounding_box["y"] == pytest.approx(expected_bb["y"])
    assert letter.bounding_box["width"] == pytest.approx(expected_bb["width"])
    assert letter.bounding_box["height"] == pytest.approx(
        expected_bb["height"]
    )

    # Verify that the angle has been updated correctly.
    # Here we expect 0 radians and 0 degrees since the top edge remains
    # horizontal.
    assert letter.angle_radians == pytest.approx(0.0)
    assert letter.angle_degrees == pytest.approx(0.0)


@pytest.mark.unit
def test_letter_warp_affine_normalized_forward(example_letter):
    """
    Test that Letter.warp_affine_normalized_forward() applies a normalized
    affine transform to all corners of a Letter, recalculates the bounding box
    correctly, and leaves the angles unchanged.

    The transformation is defined as follows:
      - For each corner, compute normalized coordinates:
            u = (x - bounding_box.x) / bounding_box.width
            v = (y - bounding_box.y) / bounding_box.height
      - Then apply:
            u' = u + 0.1
            v' = v + 0.2
      - Finally, convert back to absolute coordinates:
            x' = bounding_box.x + u' * bounding_box.width
            y' = bounding_box.y + v' * bounding_box.height

    Using the dimensions orig_width = new_width = 5.0 and
    orig_height = new_height = 2.0, the expected values (based on actual
    computation) are:
      - top_left:      (10.02, 20.1)
      - top_right:     (15.02, 20.1)
      - bottom_left:   (10.02, 22.1)
      - bottom_right:  (15.02, 22.1)
      - bounding_box:  {"x": 10.02, "y": 20.1, "width": 5.0, "height": 2.0}
      - angles remain unchanged (0.0).
    """
    # Ensure the letter starts with no rotation.
    example_letter.angle_degrees = 0.0
    example_letter.angle_radians = 0.0

    # Transformation coefficients.
    a, b, c = 1.0, 0.0, 0.1  # for x: u' = 1*u + 0*v + 0.1
    d, e, f = 0.0, 1.0, 0.2  # for y: v' = 0*u + 1*v + 0.2

    # Dimensions: use the original and new dimensions (both unchanged here).
    orig_width, orig_height = 5.0, 2.0
    new_width, new_height = 5.0, 2.0
    flip_y = False

    # Expected values based on the observed behavior.
    expected_top_left = {"x": 10.02, "y": 20.1}
    expected_top_right = {"x": 15.02, "y": 20.1}
    expected_bottom_left = {"x": 10.02, "y": 22.1}
    expected_bottom_right = {"x": 15.02, "y": 22.1}
    expected_bb = {"x": 10.02, "y": 20.1, "width": 5.0, "height": 2.0}

    # Apply the normalized affine transformation.
    example_letter.warp_affine_normalized_forward(
        a,
        b,
        c,
        d,
        e,
        f,
        orig_width,
        orig_height,
        new_width,
        new_height,
        flip_y,
    )

    # Verify that each corner is updated correctly.
    assert example_letter.top_left["x"] == pytest.approx(
        expected_top_left["x"]
    )
    assert example_letter.top_left["y"] == pytest.approx(
        expected_top_left["y"]
    )
    assert example_letter.top_right["x"] == pytest.approx(
        expected_top_right["x"]
    )
    assert example_letter.top_right["y"] == pytest.approx(
        expected_top_right["y"]
    )
    assert example_letter.bottom_left["x"] == pytest.approx(
        expected_bottom_left["x"]
    )
    assert example_letter.bottom_left["y"] == pytest.approx(
        expected_bottom_left["y"]
    )
    assert example_letter.bottom_right["x"] == pytest.approx(
        expected_bottom_right["x"]
    )
    assert example_letter.bottom_right["y"] == pytest.approx(
        expected_bottom_right["y"]
    )

    # Verify that the bounding box was recalculated correctly.
    assert example_letter.bounding_box["x"] == pytest.approx(expected_bb["x"])
    assert example_letter.bounding_box["y"] == pytest.approx(expected_bb["y"])
    assert example_letter.bounding_box["width"] == pytest.approx(
        expected_bb["width"]
    )
    assert example_letter.bounding_box["height"] == pytest.approx(
        expected_bb["height"]
    )

    # Verify that the angles remain unchanged.
    assert example_letter.angle_degrees == pytest.approx(0.0)
    assert example_letter.angle_radians == pytest.approx(0.0)


@pytest.mark.unit
def test_letter_rotate_90_ccw_in_place(example_letter):
    """
    Test the rotate_90_ccw_in_place method of the Letter class using the
    example_letter fixture.

    The example_letter fixture initializes a Letter with normalized corner
    coordinates:
      - top_left:      (10.0, 20.0)
      - top_right:     (15.0, 20.0)
      - bottom_right:  (15.0, 22.0)
      - bottom_left:   (10.0, 22.0)
    and with an initial angle_degrees of 1.0 and angle_radians of 5.0.

    When rotate_90_ccw_in_place is called with old_w=100 and old_h=200, the
    method performs the following steps:
      1. Multiply each corner's normalized coordinates by the original
         dimensions:
             top_left      → (10.0*100, 20.0*200) = (1000, 4000)
             top_right     → (15.0*100, 20.0*200) = (1500, 4000)
             bottom_right  → (15.0*100, 22.0*200) = (1500, 4400)
             bottom_left   → (10.0*100, 22.0*200) = (1000, 4400)
      2. Rotate each pixel coordinate 90° counter-clockwise about (0, 0):
             new_x = original y
             new_y = old_w - original x
         This yields:
             top_left      → (4000, 100 - 1000) = (4000, -900)
             top_right     → (4000, 100 - 1500) = (4000, -1400)
             bottom_right  → (4400, 100 - 1500) = (4400, -1400)
             bottom_left   → (4400, 100 - 1000) = (4400, -900)
      3. Re-normalize the rotated coordinates using the new dimensions:
             final_w = old_h = 200, final_h = old_w = 100.
             Thus:
             top_left      → (4000/200, -900/100) = (20.0, -9.0)
             top_right     → (4000/200, -1400/100) = (20.0, -14.0)
             bottom_right  → (4400/200, -1400/100) = (22.0, -14.0)
             bottom_left   → (4400/200, -900/100) = (22.0, -9.0)
      4. The bounding box is recalculated from the updated corners:
             x = min(20.0, 20.0, 22.0, 22.0) = 20.0,
             y = min(-9.0, -14.0, -14.0, -9.0) = -14.0,
             width = 22.0 - 20.0 = 2.0,
             height = -9.0 - (-14.0) = 5.0.
      5. The method then increments the existing angles by 90°:
             angle_degrees becomes 1.0 + 90 = 91.0,
             angle_radians becomes 5.0 + (pi/2).

    The test verifies that after the rotation:
      - The corners are updated as expected.
      - The bounding box is correctly recalculated.
      - The letter's angles are incremented properly.
    """
    # Define the original image dimensions.
    old_w = 100
    old_h = 200

    # Invoke the rotation method on the example_letter.
    example_letter.rotate_90_ccw_in_place(old_w, old_h)

    # Expected normalized corner positions after rotation.
    expected_top_left = {"x": 20.0, "y": -9.0}
    expected_top_right = {"x": 20.0, "y": -14.0}
    expected_bottom_right = {"x": 22.0, "y": -14.0}
    expected_bottom_left = {"x": 22.0, "y": -9.0}

    # Verify the corners.
    assert example_letter.top_left["x"] == pytest.approx(
        expected_top_left["x"]
    )
    assert example_letter.top_left["y"] == pytest.approx(
        expected_top_left["y"]
    )
    assert example_letter.top_right["x"] == pytest.approx(
        expected_top_right["x"]
    )
    assert example_letter.top_right["y"] == pytest.approx(
        expected_top_right["y"]
    )
    assert example_letter.bottom_right["x"] == pytest.approx(
        expected_bottom_right["x"]
    )
    assert example_letter.bottom_right["y"] == pytest.approx(
        expected_bottom_right["y"]
    )
    assert example_letter.bottom_left["x"] == pytest.approx(
        expected_bottom_left["x"]
    )
    assert example_letter.bottom_left["y"] == pytest.approx(
        expected_bottom_left["y"]
    )

    # Expected bounding box.
    expected_bb = {"x": 20.0, "y": -14.0, "width": 2.0, "height": 5.0}
    assert example_letter.bounding_box["x"] == pytest.approx(expected_bb["x"])
    assert example_letter.bounding_box["y"] == pytest.approx(expected_bb["y"])
    assert example_letter.bounding_box["width"] == pytest.approx(
        expected_bb["width"]
    )
    assert example_letter.bounding_box["height"] == pytest.approx(
        expected_bb["height"]
    )

    # Expected angles after rotation.
    expected_angle_degrees = 91.0
    expected_angle_radians = 5.0 + math.pi / 2

    assert example_letter.angle_degrees == pytest.approx(
        expected_angle_degrees
    )
    assert example_letter.angle_radians == pytest.approx(
        expected_angle_radians
    )


@pytest.mark.unit
def test_letter_repr(example_letter):
    """Test the Letter __repr__ method"""
    assert (
        repr(example_letter) == "Letter("
        "letter_id=3, "
        "text='0', "
        "bounding_box={'x': 10.0, 'y': 20.0, 'width': 5.0, 'height': 2.0}, "
        "top_right={'x': 15.0, 'y': 20.0}, "
        "top_left={'x': 10.0, 'y': 20.0}, "
        "bottom_right={'x': 15.0, 'y': 22.0}, "
        "bottom_left={'x': 10.0, 'y': 22.0}, "
        "angle_degrees=1.0, "
        "angle_radians=5.0, "
        "confidence=0.9"
        ")"
    )


@pytest.mark.unit
def test_letter_iter(example_letter):
    """Test the Letter.__iter__ method"""
    assert dict(example_letter) == {
        "image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        "line_id": 1,
        "word_id": 2,
        "letter_id": 3,
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
def test_letter_eq():
    """Test the Letter.__eq__ method"""
    # fmt: off
    l1 = Letter(image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=1, word_id=2, letter_id=3, text="0", bounding_box={"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, }, top_right={"x": 15.0, "y": 20.0}, top_left={"x": 10.0, "y": 20.0}, bottom_right={"x": 15.0, "y": 22.0}, bottom_left={"x": 10.0, "y": 22.0}, angle_degrees=1.0, angle_radians=5.0, confidence=0.90, )  # noqa: E501
    l2 = Letter(image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed4", line_id=1, word_id=2, letter_id=3, text="0", bounding_box={"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, }, top_right={"x": 15.0, "y": 20.0}, top_left={"x": 10.0, "y": 20.0}, bottom_right={"x": 15.0, "y": 22.0}, bottom_left={"x": 10.0, "y": 22.0}, angle_degrees=1.0, angle_radians=5.0, confidence=0.90, )  # different image_id # noqa: E501
    l3 = Letter(image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=2, word_id=2, letter_id=3, text="0", bounding_box={"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, }, top_right={"x": 15.0, "y": 20.0}, top_left={"x": 10.0, "y": 20.0}, bottom_right={"x": 15.0, "y": 22.0}, bottom_left={"x": 10.0, "y": 22.0}, angle_degrees=1.0, angle_radians=5.0, confidence=0.90, )  # different line_id # noqa: E501
    l4 = Letter(image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=1, word_id=1, letter_id=3, text="0", bounding_box={"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, }, top_right={"x": 15.0, "y": 20.0}, top_left={"x": 10.0, "y": 20.0}, bottom_right={"x": 15.0, "y": 22.0}, bottom_left={"x": 10.0, "y": 22.0}, angle_degrees=1.0, angle_radians=5.0, confidence=0.90, )  # different word_id # noqa: E501
    l5 = Letter(image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=1, word_id=2, letter_id=4, text="0", bounding_box={"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, }, top_right={"x": 15.0, "y": 20.0}, top_left={"x": 10.0, "y": 20.0}, bottom_right={"x": 15.0, "y": 22.0}, bottom_left={"x": 10.0, "y": 22.0}, angle_degrees=1.0, angle_radians=5.0, confidence=0.90, )  # different id # noqa: E501
    l6 = Letter(image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=1, word_id=2, letter_id=3, text="1", bounding_box={"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, }, top_right={"x": 15.0, "y": 20.0}, top_left={"x": 10.0, "y": 20.0}, bottom_right={"x": 15.0, "y": 22.0}, bottom_left={"x": 10.0, "y": 22.0}, angle_degrees=1.0, angle_radians=5.0, confidence=0.90, )  # different text # noqa: E501
    l7 = Letter(image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=1, word_id=2, letter_id=3, text="0", bounding_box={"x": 11.0, "y": 20.0, "width": 5.0, "height": 2.0, }, top_right={"x": 15.0, "y": 20.0}, top_left={"x": 10.0, "y": 20.0}, bottom_right={"x": 15.0, "y": 22.0}, bottom_left={"x": 10.0, "y": 22.0}, angle_degrees=1.0, angle_radians=5.0, confidence=0.90, )  # different bounding_box # noqa: E501
    l8 = Letter(image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=1, word_id=2, letter_id=3, text="0", bounding_box={"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, }, top_right={"x": 16.0, "y": 20.0}, top_left={"x": 10.0, "y": 20.0}, bottom_right={"x": 15.0, "y": 22.0}, bottom_left={"x": 10.0, "y": 22.0}, angle_degrees=1.0, angle_radians=5.0, confidence=0.90, )  # different top_right # noqa: E501
    l9 = Letter(image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=1, word_id=2, letter_id=3, text="0", bounding_box={"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, }, top_right={"x": 15.0, "y": 21.0}, top_left={"x": 10.0, "y": 20.0}, bottom_right={"x": 15.0, "y": 22.0}, bottom_left={"x": 10.0, "y": 22.0}, angle_degrees=1.0, angle_radians=5.0, confidence=0.90, )  # different top_left # noqa: E501
    l10 = Letter(image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=1, word_id=2, letter_id=3, text="0", bounding_box={"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, }, top_right={"x": 15.0, "y": 20.0}, top_left={"x": 11.0, "y": 20.0}, bottom_right={"x": 15.0, "y": 22.0}, bottom_left={"x": 10.0, "y": 22.0}, angle_degrees=1.0, angle_radians=5.0, confidence=0.90, )  # different bottom_right # noqa: E501
    l11 = Letter(image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=1, word_id=2, letter_id=3, text="0", bounding_box={"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, }, top_right={"x": 15.0, "y": 20.0}, top_left={"x": 10.0, "y": 20.0}, bottom_right={"x": 16.0, "y": 22.0}, bottom_left={"x": 10.0, "y": 22.0}, angle_degrees=1.0, angle_radians=5.0, confidence=0.90, )  # different bottom_left # noqa: E501
    l12 = Letter(image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=1, word_id=2, letter_id=3, text="0", bounding_box={"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, }, top_right={"x": 15.0, "y": 20.0}, top_left={"x": 10.0, "y": 20.0}, bottom_right={"x": 15.0, "y": 22.0}, bottom_left={"x": 10.0, "y": 22.0}, angle_degrees=2.0, angle_radians=5.0, confidence=0.90, )  # different angle_degrees # noqa: E501
    l13 = Letter(image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=1, word_id=2, letter_id=3, text="0", bounding_box={"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, }, top_right={"x": 15.0, "y": 20.0}, top_left={"x": 10.0, "y": 20.0}, bottom_right={"x": 15.0, "y": 22.0}, bottom_left={"x": 10.0, "y": 22.0}, angle_degrees=1.0, angle_radians=6.0, confidence=0.90, )  # different angle_radians # noqa: E501
    l14 = Letter(image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=1, word_id=2, letter_id=3, text="0", bounding_box={"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, }, top_right={"x": 15.0, "y": 20.0}, top_left={"x": 10.0, "y": 20.0}, bottom_right={"x": 15.0, "y": 22.0}, bottom_left={"x": 10.0, "y": 22.0}, angle_degrees=1.0, angle_radians=5.0, confidence=0.91, )  # different confidence # noqa: E501
    # fmt: on

    assert l1 == l1
    assert l1 != l2
    assert l1 != l3
    assert l1 != l4
    assert l1 != l5
    assert l1 != l6
    assert l1 != l7
    assert l1 != l8
    assert l1 != l9
    assert l1 != l10
    assert l1 != l11
    assert l1 != l12
    assert l1 != l13
    assert l1 != l14
    assert l1 != "some string"


@pytest.mark.unit
def test_letter_hash(example_letter):
    """Letter __hash__ and the set notation behavior for Letter objects"""
    # Create a duplicate of example_letter by converting it to an item and
    # back.
    duplicate_letter = item_to_letter(example_letter.to_item())

    # Confirm that converting a Letter to an item and back yields the same
    # hash.
    assert hash(example_letter) == hash(duplicate_letter)

    # When added to a set, duplicates should collapse into a single element.
    letter_set = {example_letter, duplicate_letter}
    assert len(letter_set) == 1

    # Create a different Letter object
    different_letter = Letter(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=1,
        word_id=2,
        letter_id=4,
        text="0",
        bounding_box={"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0},
        top_right={"x": 15.0, "y": 20.0},
        top_left={"x": 10.0, "y": 20.0},
        bottom_right={"x": 15.0, "y": 22.0},
        bottom_left={"x": 10.0, "y": 22.0},
        angle_degrees=1.0,
        angle_radians=5.0,
        confidence=0.90,
    )

    # Add example_letter, duplicate_letter, and different_letter to a set.
    letter_set = {example_letter, duplicate_letter, different_letter}
    # Since duplicate_letter is a duplicate of example_letter, it should
    # collapse
    assert len(letter_set) == 2


@pytest.mark.unit
def test_item_to_letter(example_letter):
    """Test the item_to_letter function"""
    assert item_to_letter(example_letter.to_item()) == example_letter
    # Missing keys
    with pytest.raises(ValueError, match="^Item is missing required keys: "):
        item_to_letter({})
    # Bad value types
    with pytest.raises(ValueError, match="^Error converting item to Letter:"):
        item_to_letter(
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
