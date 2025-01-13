import pytest
from dynamo import Letter, itemToLetter
import math

correct_letter_params = {
    "image_id": 1,
    "line_id": 1,
    "word_id": 1,
    "id": 1,
    "text": "0",
    "bounding_box": {
        "height": 0.022867568333804766,
        "width": 0.08688726243285705,
        "x": 0.4454336178993411,
        "y": 0.9167082877754368,
    },
    "top_right": {"x": 0.5323208803321982, "y": 0.930772983660083},
    "top_left": {"x": 0.44837726707985254, "y": 0.9395758561092415},
    "bottom_right": {"x": 0.5293772311516867, "y": 0.9167082877754368},
    "bottom_left": {"x": 0.4454336178993411, "y": 0.9255111602245953},
    "angle_degrees": -5.986527,
    "angle_radians": -0.1044846,
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
    assert letter.bounding_box == {
        "height": 0.022867568333804766,
        "width": 0.08688726243285705,
        "x": 0.4454336178993411,
        "y": 0.9167082877754368,
    }
    assert letter.top_right == {"x": 0.5323208803321982, "y": 0.930772983660083}
    assert letter.top_left == {"x": 0.44837726707985254, "y": 0.9395758561092415}
    assert letter.bottom_right == {"x": 0.5293772311516867, "y": 0.9167082877754368}
    assert letter.bottom_left == {"x": 0.4454336178993411, "y": 0.9255111602245953}
    assert letter.angle_degrees == -5.986527
    assert letter.angle_radians == -0.1044846
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
        "TYPE": {"S": "LETTER"},
        "text": {"S": "0"},
        "bounding_box": {
            "M": {
                "x": {"N": "0.445433617899341100"},
                "y": {"N": "0.916708287775436800"},
                "width": {"N": "0.086887262432857050"},
                "height": {"N": "0.022867568333804766"},
            }
        },
        "top_right": {
            "M": {
                "x": {"N": "0.532320880332198200"},
                "y": {"N": "0.930772983660083000"},
            }
        },
        "top_left": {
            "M": {
                "x": {"N": "0.448377267079852540"},
                "y": {"N": "0.939575856109241500"},
            }
        },
        "bottom_right": {
            "M": {
                "x": {"N": "0.529377231151686700"},
                "y": {"N": "0.916708287775436800"},
            }
        },
        "bottom_left": {
            "M": {
                "x": {"N": "0.445433617899341100"},
                "y": {"N": "0.925511160224595300"},
            }
        },
        "angle_degrees": {"N": "-5.9865270000"},
        "angle_radians": {"N": "-0.1044846000"},
        "confidence": {"N": "1.00"},
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
        "bounding_box": {
            "height": 0.022867568333804766,
            "width": 0.08688726243285705,
            "x": 0.4454336178993411,
            "y": 0.9167082877754368,
        },
        "top_right": {"x": 0.5323208803321982, "y": 0.930772983660083},
        "top_left": {"x": 0.44837726707985254, "y": 0.9395758561092415},
        "bottom_right": {"x": 0.5293772311516867, "y": 0.9167082877754368},
        "bottom_left": {"x": 0.4454336178993411, "y": 0.9255111602245953},
        "angle_degrees": -5.986527,
        "angle_radians": -0.1044846,
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
        "TYPE": {"S": "LETTER"},
        "text": {"S": "0"},
        "bounding_box": {
            "M": {
                "x": {"N": "0.445433617899341100"},
                "y": {"N": "0.916708287775436800"},
                "width": {"N": "0.086887262432857050"},
                "height": {"N": "0.022867568333804766"},
            }
        },
        "top_right": {
            "M": {
                "x": {"N": "0.532320880332198200"},
                "y": {"N": "0.930772983660083000"},
            }
        },
        "top_left": {
            "M": {
                "x": {"N": "0.448377267079852540"},
                "y": {"N": "0.939575856109241500"},
            }
        },
        "bottom_right": {
            "M": {
                "x": {"N": "0.529377231151686700"},
                "y": {"N": "0.916708287775436800"},
            }
        },
        "bottom_left": {
            "M": {
                "x": {"N": "0.445433617899341100"},
                "y": {"N": "0.925511160224595300"},
            }
        },
        "angle_degrees": {"N": "-5.9865270000"},
        "angle_radians": {"N": "-0.1044846000"},
        "confidence": {"N": "1.00"},
    }
    assert Letter(**correct_letter_params) == itemToLetter(item)


def create_test_letter():
    """
    A helper function that returns a Letter object
    with easily verifiable points for testing.
    """
    return Letter(
        image_id=1,
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
