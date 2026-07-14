"""Unit tests for the Line entity."""

# pylint: disable=redefined-outer-name,too-many-arguments,line-too-long

import math
from copy import deepcopy

import pytest

from receipt_dynamo import Line, item_to_line

IMAGE_ID = "3f52804b-2fad-4e00-92c8-b593da3a8ed3"

LINE_KWARGS = {
    "image_id": IMAGE_ID,
    "line_id": 1,
    "text": "Test",
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


def line_kwargs(**overrides):
    """Return fresh Line kwargs so tests cannot mutate shared dictionaries."""
    kwargs = deepcopy(LINE_KWARGS)
    kwargs.update(overrides)
    return kwargs


def make_line(**overrides):
    """Create a Line with defaults overridden by the caller."""
    return Line(**line_kwargs(**overrides))


def make_transform_line():
    """Create a Line with zero angles for transformation tests."""
    return make_line(angle_degrees=0.0, angle_radians=0.0, confidence=1.0)


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


@pytest.fixture
def example_line():
    """A pytest fixture for a sample Line object."""
    return make_line()


@pytest.mark.unit
def test_line_init_valid(example_line):
    """Test the Line constructor."""
    assert dict(example_line) == LINE_KWARGS


@pytest.mark.unit
@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"image_id": 1}, "uuid must be a string"),
        ({"image_id": "not-a-uuid"}, "uuid must be a valid UUID"),
        ({"line_id": "not-an-int"}, "line_id must be an integer"),
        ({"line_id": -1}, "line_id must be positive"),
        ({"text": 1}, "text must be a string"),
        ({"bounding_box": 1}, "bounding_box must be a dictionary"),
        (
            {"bounding_box": {"x": 10.0, "y": 20.0, "width": 5.0}},
            "bounding_box must contain the key 'height'",
        ),
        ({"top_left": 1}, "point must be a dictionary"),
        ({"top_left": {"x": 10.0}}, "point must contain the key 'y'"),
        ({"top_right": 1}, "point must be a dictionary"),
        ({"top_right": {"x": 15.0}}, "point must contain the key 'y'"),
        ({"bottom_left": 1}, "point must be a dictionary"),
        ({"bottom_left": {"x": 10.0}}, "point must contain the key 'y'"),
        ({"bottom_right": 1}, "point must be a dictionary"),
        ({"bottom_right": {"x": 15.0}}, "point must contain the key 'y'"),
        ({"angle_degrees": "1.0"}, "angle_degrees must be float or int, got"),
        ({"angle_radians": "5.0"}, "angle_radians must be float or int, got"),
        ({"confidence": "0.90"}, "confidence must be float or int, got"),
        ({"confidence": -0.90}, "confidence must be between 0 and 1, got"),
    ],
)
def test_line_init_invalid_values(overrides, message):
    """Constructor rejects invalid scalar and geometry values."""
    with pytest.raises(ValueError, match=message):
        make_line(**overrides)


@pytest.mark.unit
def test_line_init_normalizes_integer_confidence():
    """Constructor accepts integer confidence and normalizes it to float."""
    assert make_line(confidence=1).confidence == 1.0


@pytest.mark.unit
def test_line_key(example_line):
    """Test the Line.key method."""
    assert example_line.key == {
        "PK": {"S": f"IMAGE#{IMAGE_ID}"},
        "SK": {"S": "LINE#00001"},
    }


@pytest.mark.unit
def test_line_gsi1_key(example_line):
    """Test the Line.gsi1_key property."""
    assert example_line.gsi1_key() == {
        "GSI1PK": {"S": f"IMAGE#{IMAGE_ID}"},
        "GSI1SK": {"S": "LINE#00001"},
    }


@pytest.mark.unit
def test_line_to_item(example_line):
    """Test the Line.to_item() method."""
    assert example_line.to_item() == {
        "PK": {"S": f"IMAGE#{IMAGE_ID}"},
        "SK": {"S": "LINE#00001"},
        "TYPE": {"S": "LINE"},
        "GSI1PK": {"S": f"IMAGE#{IMAGE_ID}"},
        "GSI1SK": {"S": "LINE#00001"},
        "text": {"S": "Test"},
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
def test_line_calculate_centroid(example_line):
    """Test the Line.calculate_centroid() method."""
    assert example_line.calculate_centroid() == (12.5, 21.0)


@pytest.mark.unit
@pytest.mark.parametrize("dx, dy", [(5, -2), (0, 0), (-3, 10)])
def test_line_translate(dx, dy):
    """translate(dx, dy) shifts all corners and the bounding box."""
    line = make_transform_line()
    original = deepcopy(dict(line))

    line.translate(dx, dy)

    for corner in CORNERS:
        assert_point(
            getattr(line, corner),
            {
                "x": original[corner]["x"] + dx,
                "y": original[corner]["y"] + dy,
            },
        )
    assert line.bounding_box == {
        "x": original["bounding_box"]["x"] + dx,
        "y": original["bounding_box"]["y"] + dy,
        "width": original["bounding_box"]["width"],
        "height": original["bounding_box"]["height"],
    }


@pytest.mark.unit
@pytest.mark.parametrize("sx, sy", [(2, 3), (1, 1), (0.5, 2)])
def test_line_scale(sx, sy):
    """scale(sx, sy) scales corners and bounding box without changing angles."""
    line = make_transform_line()
    original = deepcopy(dict(line))

    line.scale(sx, sy)

    for corner in CORNERS:
        assert_point(
            getattr(line, corner),
            {
                "x": original[corner]["x"] * sx,
                "y": original[corner]["y"] * sy,
            },
        )
    assert_box(
        line.bounding_box,
        {
            "x": original["bounding_box"]["x"] * sx,
            "y": original["bounding_box"]["y"] * sy,
            "width": original["bounding_box"]["width"] * sx,
            "height": original["bounding_box"]["height"] * sy,
        },
    )
    assert line.angle_degrees == 0.0
    assert line.angle_radians == 0.0


@pytest.mark.unit
@pytest.mark.parametrize(
    "angle, use_radians, should_raise",
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
        (0, True, False),
        (0.5, True, False),
        (math.pi / 2 + 0.01, True, True),
        (-math.pi / 2 - 0.01, True, True),
        (math.pi, True, True),
    ],
)
def test_line_rotate_limited_range(angle, use_radians, should_raise):
    """rotate only accepts angles in the supported range and updates geometry."""
    line = make_transform_line()
    original = deepcopy(dict(line))

    if should_raise:
        with pytest.raises(ValueError):
            line.rotate(angle, 0, 0, use_radians=use_radians)
        for corner in CORNERS:
            assert getattr(line, corner) == original[corner]
        assert line.angle_degrees == original["angle_degrees"]
        assert line.angle_radians == original["angle_radians"]
        return

    line.rotate(angle, 0, 0, use_radians=use_radians)

    assert line.bounding_box == box_from_corners(
        [getattr(line, corner) for corner in CORNERS]
    )
    if angle not in (0, 0.0):
        assert any(
            getattr(line, corner) != original[corner] for corner in CORNERS
        )
    else:
        for corner in CORNERS:
            assert getattr(line, corner) == original[corner]
    if use_radians:
        assert line.angle_radians == pytest.approx(angle, abs=1e-9)
        assert line.angle_degrees == pytest.approx(
            math.degrees(angle), abs=1e-9
        )
    else:
        assert line.angle_degrees == pytest.approx(angle, abs=1e-9)
        assert line.angle_radians == pytest.approx(
            math.radians(angle), abs=1e-9
        )


@pytest.mark.unit
@pytest.mark.parametrize(
    "shx, shy, pivot_x, pivot_y, expected_corners",
    [
        (
            0.2,
            0.0,
            10.0,
            20.0,
            {
                "top_right": {"x": 15.0, "y": 20.0},
                "top_left": {"x": 10.0, "y": 20.0},
                "bottom_right": {"x": 15.4, "y": 22.0},
                "bottom_left": {"x": 10.4, "y": 22.0},
            },
        ),
        (
            0.0,
            0.2,
            10.0,
            20.0,
            {
                "top_right": {"x": 15.0, "y": 21.0},
                "top_left": {"x": 10.0, "y": 20.0},
                "bottom_right": {"x": 15.0, "y": 23.0},
                "bottom_left": {"x": 10.0, "y": 22.0},
            },
        ),
        (
            0.1,
            0.1,
            12.0,
            21.0,
            {
                "top_right": {"x": 14.9, "y": 20.3},
                "top_left": {"x": 9.9, "y": 19.8},
                "bottom_right": {"x": 15.1, "y": 22.3},
                "bottom_left": {"x": 10.1, "y": 21.8},
            },
        ),
    ],
)
def test_line_shear(shx, shy, pivot_x, pivot_y, expected_corners):
    """shear(shx, shy, pivot_x, pivot_y) shears corners and bounding box."""
    line = make_transform_line()

    line.shear(shx, shy, pivot_x, pivot_y)

    for corner, expected in expected_corners.items():
        assert_point(getattr(line, corner), expected)
    assert_box(line.bounding_box, box_from_corners(expected_corners.values()))


@pytest.mark.unit
def test_line_warp_affine():
    """warp_affine applies an affine transform and updates bounding box/angle."""
    line = make_transform_line()
    expected = {
        "top_left": {"x": 23.0, "y": 44.0},
        "top_right": {"x": 33.0, "y": 44.0},
        "bottom_left": {"x": 23.0, "y": 48.0},
        "bottom_right": {"x": 33.0, "y": 48.0},
    }

    line.warp_affine(2.0, 0.0, 3.0, 0.0, 2.0, 4.0)

    for corner, point in expected.items():
        assert_point(getattr(line, corner), point)
    assert_box(line.bounding_box, box_from_corners(expected.values()))
    assert line.angle_radians == pytest.approx(0.0)
    assert line.angle_degrees == pytest.approx(0.0)


@pytest.mark.unit
def test_line_warp_affine_normalized_forward():
    """warp_affine_normalized_forward transforms corners and preserves angle."""
    line = make_transform_line()
    expected = {
        "top_left": {"x": 10.02, "y": 20.1},
        "top_right": {"x": 15.02, "y": 20.1},
        "bottom_left": {"x": 10.02, "y": 22.1},
        "bottom_right": {"x": 15.02, "y": 22.1},
    }

    line.warp_affine_normalized_forward(
        1.0, 0.0, 0.1, 0.0, 1.0, 0.2, 5.0, 2.0, 5.0, 2.0, False
    )

    for corner, point in expected.items():
        assert_point(getattr(line, corner), point)
    assert_box(
        line.bounding_box, {"x": 10.02, "y": 20.1, "width": 5.0, "height": 2.0}
    )
    assert line.angle_degrees == pytest.approx(0.0)
    assert line.angle_radians == pytest.approx(0.0)


@pytest.mark.unit
def test_line_rotate_90_ccw_in_place():
    """rotate_90_ccw_in_place rotates normalized corners and angle."""
    line = make_transform_line()
    expected = {
        "top_left": {"x": 20.0, "y": -9.0},
        "top_right": {"x": 20.0, "y": -14.0},
        "bottom_right": {"x": 22.0, "y": -14.0},
        "bottom_left": {"x": 22.0, "y": -9.0},
    }

    line.rotate_90_ccw_in_place(100, 200)

    for corner, point in expected.items():
        assert_point(getattr(line, corner), point)
    assert_box(
        line.bounding_box, {"x": 20.0, "y": -14.0, "width": 2.0, "height": 5.0}
    )
    assert line.angle_degrees == pytest.approx(90.0)
    assert line.angle_radians == pytest.approx(math.pi / 2)


@pytest.mark.unit
def test_line_repr(example_line):
    """Test the Line.__repr__() method."""
    assert repr(example_line) == (
        "Line("
        f"image_id='{IMAGE_ID}', "
        "line_id=1, "
        "text='Test', "
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
def test_line_iter(example_line):
    """Test the Line.__iter__() method."""
    line_dict = dict(example_line)

    assert set(line_dict) == set(LINE_KWARGS)
    assert line_dict == LINE_KWARGS
    assert Line(**line_dict) == example_line


@pytest.mark.unit
def test_line_eq():
    """Line equality includes every data field and rejects other types."""
    base = make_line()
    assert base == make_line()
    for overrides in [
        {"image_id": "3f52804b-2fad-4e00-92c8-b593da3a8ed4"},
        {"line_id": 2},
        {"text": "Test2"},
        {"bounding_box": {"x": 20.0, "y": 20.0, "width": 5.0, "height": 2.0}},
        {"top_right": {"x": 20.0, "y": 20.0}},
        {"top_left": {"x": 20.0, "y": 20.0}},
        {"bottom_right": {"x": 20.0, "y": 22.0}},
        {"bottom_left": {"x": 20.0, "y": 22.0}},
        {"angle_degrees": 2.0},
        {"angle_radians": 6.0},
        {"confidence": 0.91},
    ]:
        assert base != make_line(**overrides)
    assert base != "Test"


@pytest.mark.unit
def test_line_hash(example_line):
    """Test Line hash and set behavior."""
    duplicate_line = item_to_line(example_line.to_item())
    different_line = make_line(
        line_id=2,
        text="Test line",
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=1.0,
    )

    assert hash(example_line) == hash(duplicate_line)
    assert len({example_line, duplicate_line}) == 1
    assert len({example_line, duplicate_line, different_line}) == 2


@pytest.mark.unit
def test_item_to_line(example_line):
    """item_to_line round-trips items and rejects invalid DynamoDB items."""
    assert item_to_line(example_line.to_item()) == example_line
    with pytest.raises(ValueError, match="^Item is missing required keys"):
        item_to_line({"SK": {"S": "LINE#00001"}})

    bad_item = example_line.to_item()
    bad_item["text"] = {"N": "100"}
    with pytest.raises(ValueError, match="^Error converting item to Line"):
        item_to_line(bad_item)


@pytest.fixture
def normalized_line():
    """Create a line with normalized coordinates (0-1 range)."""
    return make_line(
        text="Test line",
        bounding_box={"x": 0.1, "y": 0.2, "width": 0.8, "height": 0.1},
        top_right={"x": 0.9, "y": 0.9},
        top_left={"x": 0.1, "y": 0.9},
        bottom_right={"x": 0.9, "y": 0.8},
        bottom_left={"x": 0.1, "y": 0.8},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=0.95,
    )


@pytest.mark.unit
@pytest.mark.parametrize(
    "kwargs, expected",
    [
        ({}, [(0.1, 0.9), (0.9, 0.9), (0.1, 0.8), (0.9, 0.8)]),
        (
            {"width": 1000, "height": 800},
            [(100.0, 720.0), (900.0, 720.0), (100.0, 640.0), (900.0, 640.0)],
        ),
        (
            {"width": 1000, "height": 800, "flip_y": True},
            [(100.0, 80.0), (900.0, 80.0), (100.0, 160.0), (900.0, 160.0)],
        ),
    ],
)
def test_line_calculate_corners(normalized_line, kwargs, expected):
    """calculate_corners returns normalized or pixel-scaled corners."""
    assert normalized_line.calculate_corners(**kwargs) == pytest.approx(
        expected
    )


@pytest.mark.unit
@pytest.mark.parametrize("kwargs", [{"width": 1000}, {"height": 800}])
def test_line_calculate_corners_requires_both_dimensions(kwargs):
    """calculate_corners raises error if only one dimension is provided."""
    with pytest.raises(
        ValueError, match="Both width and height must be provided"
    ):
        make_transform_line().calculate_corners(**kwargs)


@pytest.mark.unit
def test_line_calculate_corners_typical_receipt():
    """calculate_corners scales typical receipt-line coordinates."""
    line = make_line(
        text="RECEIPT HEADER",
        bounding_box={"x": 0.15, "y": 0.85, "width": 0.7, "height": 0.03},
        top_right={"x": 0.85, "y": 0.88},
        top_left={"x": 0.15, "y": 0.88},
        bottom_right={"x": 0.85, "y": 0.85},
        bottom_left={"x": 0.15, "y": 0.85},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=0.95,
    )

    corners = line.calculate_corners(width=3024, height=4032, flip_y=True)

    assert 450 < corners[0][0] < 460
    assert 480 < corners[0][1] < 490


@pytest.mark.unit
def test_line_calculate_corners_tilted_line():
    """calculate_corners preserves non-axis-aligned tilt."""
    line = make_line(
        text="TILTED TEXT",
        bounding_box={"x": 0.1, "y": 0.5, "width": 0.8, "height": 0.05},
        top_right={"x": 0.92, "y": 0.56},
        top_left={"x": 0.1, "y": 0.55},
        bottom_right={"x": 0.9, "y": 0.51},
        bottom_left={"x": 0.08, "y": 0.5},
        angle_degrees=2.0,
        angle_radians=0.0349,
        confidence=0.95,
    )

    corners = line.calculate_corners(width=1000, height=1000, flip_y=True)

    assert corners[1][1] < corners[0][1]
