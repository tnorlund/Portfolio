"""Unit tests for the Letter entity."""

# pylint: disable=duplicate-code,redefined-outer-name,too-many-arguments

import math
from copy import deepcopy

import pytest

from receipt_dynamo import Letter, item_to_letter

IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
OTHER_IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed4"
LETTER_KWARGS = {
    "image_id": IMAGE_ID,
    "line_id": 1,
    "word_id": 2,
    "letter_id": 3,
    "text": "0",
    "bounding_box": {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0},
    "top_right": {"x": 15.0, "y": 20.0},
    "top_left": {"x": 10.0, "y": 20.0},
    "bottom_right": {"x": 15.0, "y": 22.0},
    "bottom_left": {"x": 10.0, "y": 22.0},
    "angle_degrees": 1.0,
    "angle_radians": 5.0,
    "confidence": 0.90,
}
CORNERS = ("top_left", "top_right", "bottom_left", "bottom_right")


def letter_kwargs(**overrides):
    """Return fresh constructor kwargs for a Letter."""
    kwargs = deepcopy(LETTER_KWARGS)
    kwargs.update(overrides)
    return kwargs


def make_letter(**overrides):
    """Create a Letter with defaults overridden by the caller."""
    return Letter(**letter_kwargs(**overrides))


def make_transform_letter():
    """Create a Letter with zero angles for transformation tests."""
    return make_letter(angle_degrees=0.0, angle_radians=0.0, confidence=1.0)


def assert_point(actual, expected):
    """Assert two point dictionaries are approximately equal."""
    assert actual["x"] == pytest.approx(expected["x"])
    assert actual["y"] == pytest.approx(expected["y"])


def assert_box(actual, expected):
    """Assert two bounding-box dictionaries are approximately equal."""
    for key, value in expected.items():
        assert actual[key] == pytest.approx(value)


def box_from_corners(points):
    """Compute a bounding box from point dictionaries."""
    xs = [point["x"] for point in points]
    ys = [point["y"] for point in points]
    return {
        "x": min(xs),
        "y": min(ys),
        "width": max(xs) - min(xs),
        "height": max(ys) - min(ys),
    }


def expected_rotated_geometry(angle, use_radians):
    """Independently calculate rotated corners and their bounding box."""
    theta = angle if use_radians else math.radians(angle)
    cosine = math.cos(theta)
    sine = math.sin(theta)
    corners = {
        corner: {
            "x": LETTER_KWARGS[corner]["x"] * cosine
            - LETTER_KWARGS[corner]["y"] * sine,
            "y": LETTER_KWARGS[corner]["x"] * sine
            + LETTER_KWARGS[corner]["y"] * cosine,
        }
        for corner in CORNERS
    }
    return corners, box_from_corners(corners.values())


@pytest.fixture
def example_letter():
    """Provide a representative Letter."""
    return make_letter()


@pytest.mark.unit
def test_letter_init_valid(example_letter):
    """The constructor preserves every supplied field."""
    assert dict(example_letter) == LETTER_KWARGS


@pytest.mark.unit
@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"image_id": 1}, "uuid must be a string"),
        ({"image_id": "bad-uuid"}, "uuid must be a valid UUID"),
        ({"line_id": "bad"}, "line_id must be an integer"),
        ({"line_id": None}, "line_id must be an integer"),
        ({"line_id": 1.5}, "line_id must be an integer"),
        ({"line_id": 0}, "line_id must be positive"),
        ({"line_id": -1}, "line_id must be positive"),
        ({"word_id": "bad"}, "word_id must be an integer"),
        ({"word_id": None}, "word_id must be an integer"),
        ({"word_id": 1.5}, "word_id must be an integer"),
        ({"word_id": 0}, "word_id must be positive"),
        ({"word_id": -1}, "word_id must be positive"),
        ({"letter_id": "bad"}, "letter_id must be an integer"),
        ({"letter_id": None}, "letter_id must be an integer"),
        ({"letter_id": 1.5}, "letter_id must be an integer"),
        ({"letter_id": 0}, "letter_id must be positive"),
        ({"letter_id": -1}, "letter_id must be positive"),
        ({"text": 1}, "text must be a string"),
        ({"text": ""}, "text must be exactly one character"),
        ({"text": "AB"}, "text must be exactly one character"),
        ({"bounding_box": 1}, "bounding_box must be a dict"),
        ({"bounding_box": {}}, "bounding_box must contain the key 'x'"),
        (
            {"bounding_box": {"y": 2.0, "width": 3.0, "height": 4.0}},
            "bounding_box must contain the key 'x'",
        ),
        (
            {"bounding_box": {"x": 1.0, "width": 3.0, "height": 4.0}},
            "bounding_box must contain the key 'y'",
        ),
        (
            {"bounding_box": {"x": 1.0, "y": 2.0, "width": 3.0}},
            "bounding_box must contain the key 'height'",
        ),
        (
            {
                "bounding_box": {
                    "x": "bad",
                    "y": 2.0,
                    "width": 3.0,
                    "height": 4.0,
                }
            },
            r"bounding_box\['x'\] must be a number",
        ),
        ({"angle_degrees": "bad"}, "angle_degrees must be float or int"),
        ({"angle_radians": "bad"}, "angle_radians must be float or int"),
        ({"confidence": "bad"}, "confidence must be float or int"),
        ({"confidence": None}, "confidence must be float or int"),
        ({"confidence": -0.1}, "confidence must be between 0 and 1"),
        ({"confidence": 0.0}, "confidence must be between 0 and 1"),
        ({"confidence": 1.1}, "confidence must be between 0 and 1"),
    ],
)
def test_letter_init_invalid_values(overrides, message):
    """Constructor rejects invalid identifiers, text, and geometry."""
    with pytest.raises(ValueError, match=message):
        make_letter(**overrides)


@pytest.mark.unit
@pytest.mark.parametrize("corner", CORNERS)
@pytest.mark.parametrize(
    ("bad_point", "message"),
    [
        (1, "point must be a dictionary"),
        ({"y": 1.0}, "point must contain the key 'x'"),
        ({"x": 1.0}, "point must contain the key 'y'"),
        (
            {"x": "bad", "y": 1.0},
            r"point\['x'\] must be a number",
        ),
    ],
)
def test_letter_init_invalid_corners(corner, bad_point, message):
    """Constructor validates every corner consistently."""
    with pytest.raises(ValueError, match=message):
        make_letter(**{corner: bad_point})


@pytest.mark.unit
def test_letter_init_normalizes_numeric_values():
    """Integer angles and confidence are normalized to floats."""
    letter = make_letter(angle_degrees=1, angle_radians=2, confidence=1)
    assert letter.angle_degrees == 1.0
    assert letter.angle_radians == 2.0
    assert letter.confidence == 1.0


@pytest.mark.unit
def test_letter_key(example_letter):
    """The key encodes the complete containment hierarchy."""
    assert example_letter.key == {
        "PK": {"S": f"IMAGE#{IMAGE_ID}"},
        "SK": {"S": "LINE#00001#WORD#00002#LETTER#00003"},
    }


@pytest.mark.unit
def test_letter_to_item(example_letter):
    """Serialization returns the complete DynamoDB item."""
    assert example_letter.to_item() == {
        "PK": {"S": f"IMAGE#{IMAGE_ID}"},
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
    """Centroid is the mean of the four corners."""
    assert example_letter.calculate_centroid() == (12.5, 21.0)


@pytest.mark.unit
@pytest.mark.parametrize("dx, dy", [(5, -2), (0, 0), (-3, 10)])
def test_letter_translate(dx, dy):
    """translate shifts all corners and the bounding-box origin."""
    letter = make_transform_letter()
    original = deepcopy(dict(letter))

    letter.translate(dx, dy)

    for corner in CORNERS:
        assert_point(
            getattr(letter, corner),
            {
                "x": original[corner]["x"] + dx,
                "y": original[corner]["y"] + dy,
            },
        )
    assert letter.bounding_box == {
        "x": original["bounding_box"]["x"] + dx,
        "y": original["bounding_box"]["y"] + dy,
        "width": original["bounding_box"]["width"],
        "height": original["bounding_box"]["height"],
    }


@pytest.mark.unit
@pytest.mark.parametrize("sx, sy", [(2, 3), (1, 1), (0.5, 2)])
def test_letter_scale(sx, sy):
    """scale changes all geometry without changing the angle."""
    letter = make_transform_letter()
    original = deepcopy(dict(letter))

    letter.scale(sx, sy)

    for corner in CORNERS:
        assert_point(
            getattr(letter, corner),
            {
                "x": original[corner]["x"] * sx,
                "y": original[corner]["y"] * sy,
            },
        )
    assert_box(
        letter.bounding_box,
        {
            "x": original["bounding_box"]["x"] * sx,
            "y": original["bounding_box"]["y"] * sy,
            "width": original["bounding_box"]["width"] * sx,
            "height": original["bounding_box"]["height"] * sy,
        },
    )
    assert letter.angle_degrees == 0.0
    assert letter.angle_radians == 0.0


@pytest.mark.unit
@pytest.mark.parametrize(
    ("angle", "use_radians", "should_raise"),
    [
        (90, False, False),
        (-90, False, False),
        (45, False, False),
        (0, False, False),
        (91, False, True),
        (-91, False, True),
        (180, False, True),
        (math.pi / 2, True, False),
        (-math.pi / 2, True, False),
        (0.5, True, False),
        (math.pi / 2 + 0.01, True, True),
        (-math.pi / 2 - 0.01, True, True),
        (math.pi, True, True),
    ],
)
def test_letter_rotate_limited_range(angle, use_radians, should_raise):
    """rotate validates its range and keeps corners, box, and angle aligned."""
    letter = make_transform_letter()
    original = deepcopy(dict(letter))

    if should_raise:
        with pytest.raises(ValueError):
            letter.rotate(angle, 0, 0, use_radians=use_radians)
        for corner in CORNERS:
            assert getattr(letter, corner) == original[corner]
        assert letter.bounding_box == original["bounding_box"]
        assert letter.angle_degrees == original["angle_degrees"]
        assert letter.angle_radians == original["angle_radians"]
        return

    expected_corners, expected_box = expected_rotated_geometry(
        angle, use_radians
    )
    letter.rotate(angle, 0, 0, use_radians=use_radians)

    for corner, expected in expected_corners.items():
        assert_point(getattr(letter, corner), expected)
    assert_box(letter.bounding_box, expected_box)
    expected_radians = angle if use_radians else math.radians(angle)
    expected_degrees = math.degrees(angle) if use_radians else angle
    assert letter.angle_radians == pytest.approx(expected_radians)
    assert letter.angle_degrees == pytest.approx(expected_degrees)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("shx", "shy", "pivot_x", "pivot_y", "expected_corners"),
    [
        (
            0.2,
            0.0,
            10.0,
            20.0,
            {
                "top_left": {"x": 10.0, "y": 20.0},
                "top_right": {"x": 15.0, "y": 20.0},
                "bottom_left": {"x": 10.4, "y": 22.0},
                "bottom_right": {"x": 15.4, "y": 22.0},
            },
        ),
        (
            0.0,
            0.2,
            10.0,
            20.0,
            {
                "top_left": {"x": 10.0, "y": 20.0},
                "top_right": {"x": 15.0, "y": 21.0},
                "bottom_left": {"x": 10.0, "y": 22.0},
                "bottom_right": {"x": 15.0, "y": 23.0},
            },
        ),
        (
            0.1,
            0.1,
            12.0,
            21.0,
            {
                "top_left": {"x": 9.9, "y": 19.8},
                "top_right": {"x": 14.9, "y": 20.3},
                "bottom_left": {"x": 10.1, "y": 21.8},
                "bottom_right": {"x": 15.1, "y": 22.3},
            },
        ),
    ],
)
def test_letter_shear(shx, shy, pivot_x, pivot_y, expected_corners):
    """shear transforms every corner and rebuilds the bounding box."""
    letter = make_transform_letter()

    letter.shear(shx, shy, pivot_x, pivot_y)

    for corner, expected in expected_corners.items():
        assert_point(getattr(letter, corner), expected)
    assert_box(
        letter.bounding_box, box_from_corners(expected_corners.values())
    )


@pytest.mark.unit
def test_letter_warp_affine():
    """warp_affine transforms corners and updates the box and angle."""
    letter = make_transform_letter()
    expected = {
        "top_left": {"x": 23.0, "y": 44.0},
        "top_right": {"x": 33.0, "y": 44.0},
        "bottom_left": {"x": 23.0, "y": 48.0},
        "bottom_right": {"x": 33.0, "y": 48.0},
    }

    letter.warp_affine(2.0, 0.0, 3.0, 0.0, 2.0, 4.0)

    for corner, point in expected.items():
        assert_point(getattr(letter, corner), point)
    assert_box(letter.bounding_box, box_from_corners(expected.values()))
    assert letter.angle_radians == pytest.approx(0.0)
    assert letter.angle_degrees == pytest.approx(0.0)


@pytest.mark.unit
def test_letter_warp_affine_normalized_forward():
    """Normalized affine transforms preserve the angle when requested."""
    letter = make_transform_letter()
    expected = {
        "top_left": {"x": 10.02, "y": 20.1},
        "top_right": {"x": 15.02, "y": 20.1},
        "bottom_left": {"x": 10.02, "y": 22.1},
        "bottom_right": {"x": 15.02, "y": 22.1},
    }

    letter.warp_affine_normalized_forward(
        1.0, 0.0, 0.1, 0.0, 1.0, 0.2, 5.0, 2.0, 5.0, 2.0, False
    )

    for corner, point in expected.items():
        assert_point(getattr(letter, corner), point)
    assert_box(letter.bounding_box, box_from_corners(expected.values()))
    assert letter.angle_degrees == pytest.approx(0.0)
    assert letter.angle_radians == pytest.approx(0.0)


@pytest.mark.unit
def test_letter_rotate_90_ccw_in_place():
    """The 90-degree rotation updates geometry and accumulates angles."""
    letter = make_letter()
    expected = {
        "top_left": {"x": 20.0, "y": -9.0},
        "top_right": {"x": 20.0, "y": -14.0},
        "bottom_left": {"x": 22.0, "y": -9.0},
        "bottom_right": {"x": 22.0, "y": -14.0},
    }

    letter.rotate_90_ccw_in_place(100, 200)

    for corner, point in expected.items():
        assert_point(getattr(letter, corner), point)
    assert_box(letter.bounding_box, box_from_corners(expected.values()))
    assert letter.angle_degrees == pytest.approx(91.0)
    assert letter.angle_radians == pytest.approx(5.0 + math.pi / 2)


@pytest.mark.unit
def test_letter_repr(example_letter):
    """repr exposes the identifier and all geometry fields."""
    assert repr(example_letter) == (
        "Letter(letter_id=3, text='0', "
        "bounding_box={'x': 10.0, 'y': 20.0, 'width': 5.0, 'height': 2.0}, "
        "top_right={'x': 15.0, 'y': 20.0}, "
        "top_left={'x': 10.0, 'y': 20.0}, "
        "bottom_right={'x': 15.0, 'y': 22.0}, "
        "bottom_left={'x': 10.0, 'y': 22.0}, angle_degrees=1.0, "
        "angle_radians=5.0, confidence=0.9)"
    )


@pytest.mark.unit
def test_letter_iter(example_letter):
    """Iteration yields complete constructor arguments."""
    letter_dict = dict(example_letter)
    assert letter_dict == LETTER_KWARGS
    assert Letter(**letter_dict) == example_letter


@pytest.mark.unit
@pytest.mark.parametrize(
    "overrides",
    [
        {"image_id": OTHER_IMAGE_ID},
        {"line_id": 2},
        {"word_id": 3},
        {"letter_id": 4},
        {"text": "1"},
        {"bounding_box": {"x": 11.0, "y": 20.0, "width": 5.0, "height": 2.0}},
        {"top_right": {"x": 16.0, "y": 20.0}},
        {"top_left": {"x": 11.0, "y": 20.0}},
        {"bottom_right": {"x": 16.0, "y": 22.0}},
        {"bottom_left": {"x": 11.0, "y": 22.0}},
        {"angle_degrees": 2.0},
        {"angle_radians": 6.0},
        {"confidence": 0.91},
    ],
)
def test_letter_eq_detects_field_differences(overrides):
    """Equality includes every constructor field."""
    assert make_letter() != make_letter(**overrides)


@pytest.mark.unit
def test_letter_eq_matches_values_and_rejects_other_types():
    """Equal values compare equal while unrelated types do not."""
    assert make_letter() == make_letter()
    assert make_letter() != "some string"


@pytest.mark.unit
def test_letter_hash(example_letter):
    """Equal letters hash equally and collapse in sets."""
    duplicate = item_to_letter(example_letter.to_item())
    different = make_letter(letter_id=4)

    assert hash(example_letter) == hash(duplicate)
    assert len({example_letter, duplicate}) == 1
    assert len({example_letter, duplicate, different}) == 2


@pytest.mark.unit
def test_item_to_letter_round_trip(example_letter):
    """DynamoDB conversion preserves every Letter field."""
    assert item_to_letter(example_letter.to_item()) == example_letter


@pytest.mark.unit
def test_item_to_letter_rejects_invalid_items(example_letter):
    """DynamoDB conversion distinguishes missing and malformed fields."""
    with pytest.raises(ValueError, match="^Item is missing required keys"):
        item_to_letter({})

    bad_item = example_letter.to_item()
    bad_item["text"] = {"N": "0"}
    with pytest.raises(ValueError, match="^Error converting item to Letter"):
        item_to_letter(bad_item)

    bad_key = example_letter.to_item()
    bad_key["SK"] = {"S": "LETTER#00003"}
    with pytest.raises(ValueError, match="Invalid SK format for Letter"):
        item_to_letter(bad_key)
