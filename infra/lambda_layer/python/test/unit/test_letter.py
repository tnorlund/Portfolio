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
def test_calculate_centroid(example_letter):
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

    assert letter.bottom_right["x"] == pytest.approx(orig_bottom_right["x"] + dx)
    assert letter.bottom_right["y"] == pytest.approx(orig_bottom_right["y"] + dy)

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
    Test that Letter.rotate(angle, origin_x, origin_y, use_radians) rotates the letter's
    corners, recalculates the axis-aligned bounding box from those rotated corners,
    and updates the angle accumulators appropriately.
    If the angle is outside the allowed range, a ValueError is raised and no changes occur.
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

        # Verify the recalculated bounding box matches the expected bounding box.
        assert letter.bounding_box["x"] == pytest.approx(expected_bb["x"], rel=1e-6)
        assert letter.bounding_box["y"] == pytest.approx(expected_bb["y"], rel=1e-6)
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
            expected_angle_degrees = orig_angle_degrees + angle * 180.0 / math.pi
        else:
            expected_angle_degrees = orig_angle_degrees + angle
            expected_angle_radians = orig_angle_radians + math.radians(angle)
        assert letter.angle_radians == pytest.approx(expected_angle_radians, abs=1e-9)
        assert letter.angle_degrees == pytest.approx(expected_angle_degrees, abs=1e-9)


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
                "top_right": {"x": 15.0 + 0.2 * (20.0 - 20.0), "y": 20.0},  # (15,20)
                "top_left": {"x": 10.0 + 0.2 * (20.0 - 20.0), "y": 20.0},  # (10,20)
                "bottom_right": {
                    "x": 15.0 + 0.2 * (22.0 - 20.0),
                    "y": 22.0,
                },  # (15.4,22)
                "bottom_left": {
                    "x": 10.0 + 0.2 * (22.0 - 20.0),
                    "y": 22.0,
                },  # (10.4,22)
            },
        ),
        # Test 2: Vertical shear only (shy nonzero, shx=0)
        (
            0.0,
            0.2,
            10.0,
            20.0,
            {
                "top_right": {"x": 15.0, "y": 20.0 + 0.2 * (15.0 - 10.0)},  # (15,21)
                "top_left": {"x": 10.0, "y": 20.0 + 0.2 * (10.0 - 10.0)},  # (10,20)
                "bottom_right": {"x": 15.0, "y": 22.0 + 0.2 * (15.0 - 10.0)},  # (15,23)
                "bottom_left": {"x": 10.0, "y": 22.0 + 0.2 * (10.0 - 10.0)},  # (10,22)
            },
        ),
        # Test 3: Combined shear
        (
            0.1,
            0.1,
            12.0,
            21.0,
            {
                # For each corner, calculate:
                # new_x = original_x + 0.1*(original_y - 21.0)
                # new_y = original_y + 0.1*(original_x - 12.0)
                "top_right": {
                    "x": 15.0 + 0.1 * (20.0 - 21.0),
                    "y": 20.0 + 0.1 * (15.0 - 12.0),
                },  # (15 - 0.1, 20 + 0.3) = (14.9, 20.3)
                "top_left": {
                    "x": 10.0 + 0.1 * (20.0 - 21.0),
                    "y": 20.0 + 0.1 * (10.0 - 12.0),
                },  # (10 - 0.1, 20 - 0.2) = (9.9, 19.8)
                "bottom_right": {
                    "x": 15.0 + 0.1 * (22.0 - 21.0),
                    "y": 22.0 + 0.1 * (15.0 - 12.0),
                },  # (15 + 0.1, 22 + 0.3) = (15.1, 22.3)
                "bottom_left": {
                    "x": 10.0 + 0.1 * (22.0 - 21.0),
                    "y": 22.0 + 0.1 * (10.0 - 12.0),
                },  # (10 + 0.1, 22 - 0.2) = (10.1, 21.8)
            },
        ),
    ],
)
def test_shear(shx, shy, pivot_x, pivot_y, expected_corners):
    """
    Test that the shear(shx, shy, pivot_x, pivot_y) method correctly shears
    the corners of the line and recalculates the bounding box.
    """
    letter = create_test_letter()

    # Apply shear transformation
    letter.shear(shx, shy, pivot_x, pivot_y)

    # Check each corner against the expected values
    for corner_name in ["top_right", "top_left", "bottom_right", "bottom_left"]:
        for coord in ["x", "y"]:
            expected_value = expected_corners[corner_name][coord]
            actual_value = letter.__dict__[corner_name][coord]
            assert actual_value == pytest.approx(
                expected_value
            ), f"{corner_name} {coord} expected {expected_value}, got {actual_value}"

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
    assert letter.bounding_box["height"] == pytest.approx(expected_bb["height"])


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
def test_eq():
    """Test the Letter.__eq__ method"""
    # fmt: off
    l1 = Letter( image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=1, word_id=2, id=3, text="0", bounding_box={ "x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, }, top_right={"x": 15.0, "y": 20.0}, top_left={"x": 10.0, "y": 20.0}, bottom_right={"x": 15.0, "y": 22.0}, bottom_left={"x": 10.0, "y": 22.0}, angle_degrees=1.0, angle_radians=5.0, confidence=0.90, )
    l2 = Letter( image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed4", line_id=1, word_id=2, id=3, text="0", bounding_box={ "x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, }, top_right={"x": 15.0, "y": 20.0}, top_left={"x": 10.0, "y": 20.0}, bottom_right={"x": 15.0, "y": 22.0}, bottom_left={"x": 10.0, "y": 22.0}, angle_degrees=1.0, angle_radians=5.0, confidence=0.90, ) # different image_id
    l3 = Letter( image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=2, word_id=2, id=3, text="0", bounding_box={ "x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, }, top_right={"x": 15.0, "y": 20.0}, top_left={"x": 10.0, "y": 20.0}, bottom_right={"x": 15.0, "y": 22.0}, bottom_left={"x": 10.0, "y": 22.0}, angle_degrees=1.0, angle_radians=5.0, confidence=0.90, ) # different line_id
    l4 = Letter( image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=1, word_id=1, id=3, text="0", bounding_box={ "x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, }, top_right={"x": 15.0, "y": 20.0}, top_left={"x": 10.0, "y": 20.0}, bottom_right={"x": 15.0, "y": 22.0}, bottom_left={"x": 10.0, "y": 22.0}, angle_degrees=1.0, angle_radians=5.0, confidence=0.90, ) # different word_id
    l5 = Letter( image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=1, word_id=2, id=4, text="0", bounding_box={ "x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, }, top_right={"x": 15.0, "y": 20.0}, top_left={"x": 10.0, "y": 20.0}, bottom_right={"x": 15.0, "y": 22.0}, bottom_left={"x": 10.0, "y": 22.0}, angle_degrees=1.0, angle_radians=5.0, confidence=0.90, ) # different id
    l6 = Letter( image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=1, word_id=2, id=3, text="1", bounding_box={ "x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, }, top_right={"x": 15.0, "y": 20.0}, top_left={"x": 10.0, "y": 20.0}, bottom_right={"x": 15.0, "y": 22.0}, bottom_left={"x": 10.0, "y": 22.0}, angle_degrees=1.0, angle_radians=5.0, confidence=0.90, ) # different text
    l7 = Letter( image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=1, word_id=2, id=3, text="0", bounding_box={ "x": 11.0, "y": 20.0, "width": 5.0, "height": 2.0, }, top_right={"x": 15.0, "y": 20.0}, top_left={"x": 10.0, "y": 20.0}, bottom_right={"x": 15.0, "y": 22.0}, bottom_left={"x": 10.0, "y": 22.0}, angle_degrees=1.0, angle_radians=5.0, confidence=0.90, ) # different bounding_box
    l8 = Letter( image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=1, word_id=2, id=3, text="0", bounding_box={ "x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, }, top_right={"x": 16.0, "y": 20.0}, top_left={"x": 10.0, "y": 20.0}, bottom_right={"x": 15.0, "y": 22.0}, bottom_left={"x": 10.0, "y": 22.0}, angle_degrees=1.0, angle_radians=5.0, confidence=0.90, ) # different top_right
    l9 = Letter( image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=1, word_id=2, id=3, text="0", bounding_box={ "x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, }, top_right={"x": 15.0, "y": 21.0}, top_left={"x": 10.0, "y": 20.0}, bottom_right={"x": 15.0, "y": 22.0}, bottom_left={"x": 10.0, "y": 22.0}, angle_degrees=1.0, angle_radians=5.0, confidence=0.90, ) # different top_left
    l10 = Letter( image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=1, word_id=2, id=3, text="0", bounding_box={ "x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, }, top_right={"x": 15.0, "y": 20.0}, top_left={"x": 11.0, "y": 20.0}, bottom_right={"x": 15.0, "y": 22.0}, bottom_left={"x": 10.0, "y": 22.0}, angle_degrees=1.0, angle_radians=5.0, confidence=0.90, ) # different bottom_right
    l11 = Letter( image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=1, word_id=2, id=3, text="0", bounding_box={ "x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, }, top_right={"x": 15.0, "y": 20.0}, top_left={"x": 10.0, "y": 20.0}, bottom_right={"x": 16.0, "y": 22.0}, bottom_left={"x": 10.0, "y": 22.0}, angle_degrees=1.0, angle_radians=5.0, confidence=0.90, ) # different bottom_left
    l12 = Letter( image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=1, word_id=2, id=3, text="0", bounding_box={ "x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, }, top_right={"x": 15.0, "y": 20.0}, top_left={"x": 10.0, "y": 20.0}, bottom_right={"x": 15.0, "y": 22.0}, bottom_left={"x": 10.0, "y": 22.0}, angle_degrees=2.0, angle_radians=5.0, confidence=0.90, ) # different angle_degrees
    l13 = Letter( image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=1, word_id=2, id=3, text="0", bounding_box={ "x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, }, top_right={"x": 15.0, "y": 20.0}, top_left={"x": 10.0, "y": 20.0}, bottom_right={"x": 15.0, "y": 22.0}, bottom_left={"x": 10.0, "y": 22.0}, angle_degrees=1.0, angle_radians=6.0, confidence=0.90, ) # different angle_radians
    l14 = Letter( image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", line_id=1, word_id=2, id=3, text="0", bounding_box={ "x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, }, top_right={"x": 15.0, "y": 20.0}, top_left={"x": 10.0, "y": 20.0}, bottom_right={"x": 15.0, "y": 22.0}, bottom_left={"x": 10.0, "y": 22.0}, angle_degrees=1.0, angle_radians=5.0, confidence=0.91, ) # different confidence
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
