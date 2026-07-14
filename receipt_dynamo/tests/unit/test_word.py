"""Unit tests for the Word entity."""

# pylint: disable=duplicate-code,redefined-outer-name,too-many-arguments

import math
from copy import deepcopy

import pytest

from receipt_dynamo import Word, item_to_word

IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
OTHER_IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed4"
WORD_KWARGS = {
    "image_id": IMAGE_ID,
    "line_id": 2,
    "word_id": 3,
    "text": "test_string",
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


def word_kwargs(**overrides):
    """Return fresh Word kwargs so tests cannot mutate shared dictionaries."""
    kwargs = deepcopy(WORD_KWARGS)
    kwargs.update(overrides)
    return kwargs


def make_word(**overrides):
    """Create a Word with defaults overridden by the caller."""
    return Word(**word_kwargs(**overrides))


def make_transform_word():
    """Create a Word with zero angles for transformation tests."""
    return make_word(angle_degrees=0.0, angle_radians=0.0, confidence=1.0)


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
            "x": WORD_KWARGS[corner]["x"] * cosine
            - WORD_KWARGS[corner]["y"] * sine,
            "y": WORD_KWARGS[corner]["x"] * sine
            + WORD_KWARGS[corner]["y"] * cosine,
        }
        for corner in CORNERS
    }
    return corners, box_from_corners(corners.values())


@pytest.fixture
def example_word():
    """Provide a representative Word."""
    return make_word()


@pytest.fixture
def normalized_word():
    """Provide a Word whose coordinates are normalized to an image."""
    return make_word(
        bounding_box={"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.4},
        top_left={"x": 0.1, "y": 0.6},
        top_right={"x": 0.4, "y": 0.6},
        bottom_left={"x": 0.1, "y": 0.2},
        bottom_right={"x": 0.4, "y": 0.2},
        angle_degrees=0.0,
        angle_radians=0.0,
    )


@pytest.mark.unit
def test_word_init_valid(example_word):
    """The constructor preserves all supplied fields and defaults."""
    assert dict(example_word) == {**WORD_KWARGS, "extracted_data": None}


@pytest.mark.unit
@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"image_id": 1}, "uuid must be a string"),
        ({"image_id": "bad-uuid"}, "uuid must be a valid UUID"),
        ({"line_id": "bad"}, "line_id must be an integer"),
        ({"line_id": -1}, "line_id must be non-negative"),
        ({"word_id": "bad"}, "word_id must be an integer"),
        ({"word_id": -1}, "word_id must be non-negative"),
        ({"text": 1}, "text must be a string"),
        ({"bounding_box": 1}, "bounding_box must be a dict"),
        (
            {"bounding_box": {"x": 1.0, "y": 2.0, "width": 3.0}},
            "bounding_box must contain the key 'height'",
        ),
        ({"angle_degrees": "bad"}, "angle_degrees must be float or int"),
        ({"angle_radians": "bad"}, "angle_radians must be float or int"),
        ({"confidence": "bad"}, "confidence must be float or int"),
        ({"confidence": 0.0}, "confidence must be between 0 and 1"),
        ({"confidence": 1.1}, "confidence must be between 0 and 1"),
        ({"extracted_data": 1}, "extracted_data must be dict"),
        (
            {"extracted_data": {"type": "merchant"}},
            "extracted_data missing required keys",
        ),
    ],
)
def test_word_init_invalid_values(overrides, message):
    """Constructor rejects invalid scalar and bounding-box values."""
    with pytest.raises(ValueError, match=message):
        make_word(**overrides)


@pytest.mark.unit
@pytest.mark.parametrize("corner", CORNERS)
@pytest.mark.parametrize(
    ("bad_point", "message"),
    [
        (1, "point must be a dictionary"),
        ({"x": 1.0}, "point must contain the key 'y'"),
    ],
)
def test_word_init_invalid_corners(corner, bad_point, message):
    """Constructor validates every corner consistently."""
    with pytest.raises(ValueError, match=message):
        make_word(**{corner: bad_point})


@pytest.mark.unit
def test_word_optional_values():
    """Constructor normalizes numeric fields and accepts extracted data."""
    assert make_word(confidence=1).confidence == 1.0
    data = {"type": "merchant", "value": "Corner Shop"}
    assert make_word(extracted_data=data).extracted_data == data


@pytest.mark.unit
def test_word_keys(example_word):
    """Primary and secondary keys encode the containing line and word."""
    assert example_word.key == {
        "PK": {"S": f"IMAGE#{IMAGE_ID}"},
        "SK": {"S": "LINE#00002#WORD#00003"},
    }
    assert example_word.gsi2_key() == {
        "GSI2PK": {"S": f"IMAGE#{IMAGE_ID}"},
        "GSI2SK": {"S": "LINE#00002#WORD#00003"},
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("extracted_data", "expected"),
    [
        (None, {"NULL": True}),
        ({}, {"NULL": True}),
        (
            {"type": "merchant", "value": "Corner Shop"},
            {
                "M": {
                    "type": {"S": "merchant"},
                    "value": {"S": "Corner Shop"},
                }
            },
        ),
    ],
)
def test_word_to_item(extracted_data, expected):
    """Serialization includes geometry, keys, and optional extracted data."""
    item = make_word(extracted_data=extracted_data).to_item()

    assert item == {
        "PK": {"S": f"IMAGE#{IMAGE_ID}"},
        "SK": {"S": "LINE#00002#WORD#00003"},
        "TYPE": {"S": "WORD"},
        "GSI2PK": {"S": f"IMAGE#{IMAGE_ID}"},
        "GSI2SK": {"S": "LINE#00002#WORD#00003"},
        "text": {"S": "test_string"},
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
        "extracted_data": expected,
    }


@pytest.mark.unit
@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, (0.25, 0.4)),
        ({"width": 1000, "height": 500}, (250.0, 200.0)),
        (
            {"width": 1000, "height": 500, "flip_y": True},
            (250.0, 300.0),
        ),
    ],
)
def test_word_calculate_centroid(normalized_word, kwargs, expected):
    """Centroid calculation supports normalized and image coordinates."""
    assert normalized_word.calculate_centroid(**kwargs) == pytest.approx(
        expected
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, (0.1, 0.2, 0.3, 0.4)),
        ({"width": 1000, "height": 500}, (100.0, 100.0, 0.3, 0.4)),
        (
            {"width": 1000, "height": 500, "flip_y": True},
            (100.0, 400.0, 0.3, 0.4),
        ),
    ],
)
def test_word_calculate_bounding_box(normalized_word, kwargs, expected):
    """Bounding-box calculation scales its origin and supports y flipping."""
    assert normalized_word.calculate_bounding_box(**kwargs) == pytest.approx(
        expected
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    ("kwargs", "expected"),
    [
        ({}, ((0.1, 0.6), (0.4, 0.6), (0.1, 0.2), (0.4, 0.2))),
        (
            {"width": 1000, "height": 500},
            ((100.0, 300.0), (400.0, 300.0), (100.0, 100.0), (400.0, 100.0)),
        ),
        (
            {"width": 1000, "height": 500, "flip_y": True},
            ((100.0, 200.0), (400.0, 200.0), (100.0, 400.0), (400.0, 400.0)),
        ),
    ],
)
def test_word_calculate_corners(normalized_word, kwargs, expected):
    """Corner calculation supports normalized, scaled, and flipped output."""
    actual = normalized_word.calculate_corners(**kwargs)
    for actual_point, expected_point in zip(actual, expected):
        assert actual_point == pytest.approx(expected_point)


@pytest.mark.unit
@pytest.mark.parametrize(
    ("method_name", "kwargs"),
    [
        ("calculate_centroid", {"width": 1000}),
        ("calculate_centroid", {"height": 500}),
        ("calculate_bounding_box", {"width": 1000}),
        ("calculate_bounding_box", {"height": 500}),
        ("calculate_corners", {"width": 1000}),
        ("calculate_corners", {"height": 500}),
    ],
)
def test_word_coordinate_helpers_require_both_dimensions(
    normalized_word, method_name, kwargs
):
    """Image-coordinate helpers reject incomplete dimensions."""
    with pytest.raises(ValueError, match="Both width and height"):
        getattr(normalized_word, method_name)(**kwargs)


@pytest.mark.unit
@pytest.mark.parametrize("dx, dy", [(5, -2), (0, 0), (-3, 10)])
def test_word_translate(dx, dy):
    """translate shifts all corners and the bounding-box origin."""
    word = make_transform_word()
    original = deepcopy(dict(word))

    word.translate(dx, dy)

    for corner in CORNERS:
        assert_point(
            getattr(word, corner),
            {
                "x": original[corner]["x"] + dx,
                "y": original[corner]["y"] + dy,
            },
        )
    assert word.bounding_box == {
        "x": original["bounding_box"]["x"] + dx,
        "y": original["bounding_box"]["y"] + dy,
        "width": original["bounding_box"]["width"],
        "height": original["bounding_box"]["height"],
    }


@pytest.mark.unit
@pytest.mark.parametrize("sx, sy", [(2, 3), (1, 1), (0.5, 2)])
def test_word_scale(sx, sy):
    """scale changes all geometry without changing the angle."""
    word = make_transform_word()
    original = deepcopy(dict(word))

    word.scale(sx, sy)

    for corner in CORNERS:
        assert_point(
            getattr(word, corner),
            {
                "x": original[corner]["x"] * sx,
                "y": original[corner]["y"] * sy,
            },
        )
    assert_box(
        word.bounding_box,
        {
            "x": original["bounding_box"]["x"] * sx,
            "y": original["bounding_box"]["y"] * sy,
            "width": original["bounding_box"]["width"] * sx,
            "height": original["bounding_box"]["height"] * sy,
        },
    )
    assert word.angle_degrees == 0.0
    assert word.angle_radians == 0.0


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
def test_word_rotate_limited_range(angle, use_radians, should_raise):
    """rotate validates its range and keeps corners, box, and angle aligned."""
    word = make_transform_word()
    original = deepcopy(dict(word))

    if should_raise:
        with pytest.raises(ValueError):
            word.rotate(angle, 0, 0, use_radians=use_radians)
        for corner in CORNERS:
            assert getattr(word, corner) == original[corner]
        assert word.bounding_box == original["bounding_box"]
        assert word.angle_degrees == original["angle_degrees"]
        assert word.angle_radians == original["angle_radians"]
        return

    expected_corners, expected_box = expected_rotated_geometry(
        angle, use_radians
    )
    word.rotate(angle, 0, 0, use_radians=use_radians)

    for corner, expected in expected_corners.items():
        assert_point(getattr(word, corner), expected)
    assert_box(word.bounding_box, expected_box)
    expected_radians = angle if use_radians else math.radians(angle)
    expected_degrees = math.degrees(angle) if use_radians else angle
    assert word.angle_radians == pytest.approx(expected_radians)
    assert word.angle_degrees == pytest.approx(expected_degrees)


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
def test_word_shear(shx, shy, pivot_x, pivot_y, expected_corners):
    """shear transforms every corner and rebuilds the bounding box."""
    word = make_transform_word()

    word.shear(shx, shy, pivot_x, pivot_y)

    for corner, expected in expected_corners.items():
        assert_point(getattr(word, corner), expected)
    assert_box(word.bounding_box, box_from_corners(expected_corners.values()))


@pytest.mark.unit
def test_word_warp_affine():
    """warp_affine transforms corners and updates the box and angle."""
    word = make_transform_word()
    expected = {
        "top_left": {"x": 23.0, "y": 44.0},
        "top_right": {"x": 33.0, "y": 44.0},
        "bottom_left": {"x": 23.0, "y": 48.0},
        "bottom_right": {"x": 33.0, "y": 48.0},
    }

    word.warp_affine(2.0, 0.0, 3.0, 0.0, 2.0, 4.0)

    for corner, point in expected.items():
        assert_point(getattr(word, corner), point)
    assert_box(word.bounding_box, box_from_corners(expected.values()))
    assert word.angle_radians == pytest.approx(0.0)
    assert word.angle_degrees == pytest.approx(0.0)


@pytest.mark.unit
def test_word_warp_affine_normalized_forward():
    """Normalized affine transforms preserve the angle when requested."""
    word = make_transform_word()
    expected = {
        "top_left": {"x": 10.02, "y": 20.1},
        "top_right": {"x": 15.02, "y": 20.1},
        "bottom_left": {"x": 10.02, "y": 22.1},
        "bottom_right": {"x": 15.02, "y": 22.1},
    }

    word.warp_affine_normalized_forward(
        1.0, 0.0, 0.1, 0.0, 1.0, 0.2, 5.0, 2.0, 5.0, 2.0, False
    )

    for corner, point in expected.items():
        assert_point(getattr(word, corner), point)
    assert_box(word.bounding_box, box_from_corners(expected.values()))
    assert word.angle_degrees == pytest.approx(0.0)
    assert word.angle_radians == pytest.approx(0.0)


@pytest.mark.unit
def test_word_rotate_90_ccw_in_place():
    """The normalized 90-degree rotation updates all geometry and angles."""
    word = make_transform_word()
    expected = {
        "top_left": {"x": 20.0, "y": -9.0},
        "top_right": {"x": 20.0, "y": -14.0},
        "bottom_left": {"x": 22.0, "y": -9.0},
        "bottom_right": {"x": 22.0, "y": -14.0},
    }

    word.rotate_90_ccw_in_place(100, 200)

    for corner, point in expected.items():
        assert_point(getattr(word, corner), point)
    assert_box(word.bounding_box, box_from_corners(expected.values()))
    assert word.angle_degrees == pytest.approx(90.0)
    assert word.angle_radians == pytest.approx(math.pi / 2)


@pytest.mark.unit
def test_word_repr(example_word):
    """repr exposes the entity-specific identifier and geometry."""
    assert repr(example_word) == (
        "Word(word_id=3, text='test_string', "
        "bounding_box={'x': 10.0, 'y': 20.0, 'width': 5.0, 'height': 2.0}, "
        "top_right={'x': 15.0, 'y': 20.0}, "
        "top_left={'x': 10.0, 'y': 20.0}, "
        "bottom_right={'x': 15.0, 'y': 22.0}, "
        "bottom_left={'x': 10.0, 'y': 22.0}, angle_degrees=1.0, "
        "angle_radians=5.0, confidence=0.9)"
    )


@pytest.mark.unit
def test_word_iter(example_word):
    """Iteration yields complete constructor arguments."""
    word_dict = dict(example_word)
    assert word_dict == {**WORD_KWARGS, "extracted_data": None}
    assert Word(**word_dict) == example_word


@pytest.mark.unit
@pytest.mark.parametrize(
    "overrides",
    [
        {"image_id": OTHER_IMAGE_ID},
        {"line_id": 4},
        {"word_id": 4},
        {"text": "different"},
        {"bounding_box": {"x": 11.0, "y": 20.0, "width": 5.0, "height": 2.0}},
        {"top_right": {"x": 16.0, "y": 20.0}},
        {"top_left": {"x": 11.0, "y": 20.0}},
        {"bottom_right": {"x": 16.0, "y": 22.0}},
        {"bottom_left": {"x": 11.0, "y": 22.0}},
        {"angle_degrees": 2.0},
        {"angle_radians": 6.0},
        {"confidence": 0.91},
        {"extracted_data": {"type": "merchant", "value": "Shop"}},
    ],
)
def test_word_eq_detects_field_differences(overrides):
    """Equality includes every constructor field."""
    assert make_word() != make_word(**overrides)


@pytest.mark.unit
def test_word_eq_matches_values_and_rejects_other_types():
    """Equal values compare equal while unrelated types do not."""
    assert make_word() == make_word()
    assert make_word() != "test_string"


@pytest.mark.unit
def test_word_hash(example_word):
    """Equal words hash equally and collapse in sets."""
    duplicate = item_to_word(example_word.to_item())
    different = make_word(word_id=4)

    assert hash(example_word) == hash(duplicate)
    assert len({example_word, duplicate}) == 1
    assert len({example_word, duplicate, different}) == 2


@pytest.mark.unit
@pytest.mark.parametrize(
    "extracted_data",
    [None, {"type": "merchant", "value": "Corner Shop"}],
)
def test_item_to_word_round_trip(extracted_data):
    """DynamoDB conversion preserves optional and required fields."""
    word = make_word(extracted_data=extracted_data)
    assert item_to_word(word.to_item()) == word


@pytest.mark.unit
def test_item_to_word_rejects_invalid_items(example_word):
    """DynamoDB conversion distinguishes missing and malformed fields."""
    with pytest.raises(ValueError, match="^Item is missing required keys"):
        item_to_word({})

    bad_item = example_word.to_item()
    bad_item["text"] = {"N": "100"}
    with pytest.raises(ValueError, match="^Error converting item to Word"):
        item_to_word(bad_item)

    bad_key = example_word.to_item()
    bad_key["SK"] = {"S": "WORD#00003"}
    with pytest.raises(ValueError, match="Invalid SK format for Word"):
        item_to_word(bad_key)
