"""Unit tests for the Line entity."""

# pylint: disable=redefined-outer-name,too-many-statements,too-many-arguments
# pylint: disable=too-many-locals,unused-argument,line-too-long,too-many-lines
# pylint: disable=pointless-statement,expression-not-assigned

import math

import pytest

from receipt_dynamo import Line, item_to_line


@pytest.fixture
def example_line():
    """A pytest fixture for a sample Line object."""
    return Line(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        1,
        "Test",
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


def create_test_line():
    """
    Helper function to create a Line object with easily verifiable points.
    Adjust coordinates as needed for your tests.
    """
    return Line(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=1,
        text="Test",
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
def test_line_init_valid(example_line):
    """Test the Line constructor"""
    assert example_line.image_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert example_line.line_id == 1
    assert example_line.text == "Test"
    assert example_line.bounding_box == {
        "x": 10.0,
        "y": 20.0,
        "width": 5.0,
        "height": 2.0,
    }
    assert example_line.top_right == {"x": 15.0, "y": 20.0}
    assert example_line.top_left == {"x": 10.0, "y": 20.0}
    assert example_line.bottom_right == {"x": 15.0, "y": 22.0}
    assert example_line.bottom_left == {"x": 10.0, "y": 22.0}
    assert example_line.angle_degrees == 1.0
    assert example_line.angle_radians == 5.0
    assert example_line.confidence == 0.90


@pytest.mark.unit
def test_line_init_invalid_uuid():
    """Test the Line constructor with bad ID"""
    with pytest.raises(ValueError, match="uuid must be a string"):
        Line(
            1,
            1,
            "Test",
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
    with pytest.raises(ValueError, match="uuid must be a valid UUID"):
        Line(
            "not-a-uuid",
            1,
            "Test",
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


@pytest.mark.unit
def test_line_init_invalid_id():
    with pytest.raises(ValueError, match="line_id must be an integer"):
        Line(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            "not-an-int",
            "Test",
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
    with pytest.raises(ValueError, match="line_id must be positive"):
        Line(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            -1,
            "Test",
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


@pytest.mark.unit
def test_line_init_invalid_text():
    with pytest.raises(ValueError, match="text must be a string"):
        Line(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            1,
            1,
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


@pytest.mark.unit
def test_line_init_invalid_bounding_box():
    with pytest.raises(
        ValueError,
        match="bounding_box must be a dictionary",
    ):
        Line(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            1,
            "Test",
            1,
            {"x": 15.0, "y": 20.0},
            {"x": 10.0, "y": 20.0},
            {"x": 15.0, "y": 22.0},
            {"x": 10.0, "y": 22.0},
            1.0,
            5.0,
            0.90,
        )
    with pytest.raises(
        ValueError,
        match="bounding_box must contain the key 'height'",
    ):
        Line(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            1,
            "Test",
            {"x": 10.0, "y": 20.0, "width": 5.0},
            {"x": 15.0, "y": 20.0},
            {"x": 10.0, "y": 20.0},
            {"x": 15.0, "y": 22.0},
            {"x": 10.0, "y": 22.0},
            1.0,
            5.0,
            0.90,
        )


@pytest.mark.unit
def test_line_init_invalid_top_left():
    with pytest.raises(ValueError, match="point must be a dictionary"):
        Line(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            1,
            "Test",
            {
                "x": 10.0,
                "y": 20.0,
                "width": 5.0,
                "height": 2.0,
            },
            1,
            {"x": 10.0, "y": 20.0},
            {"x": 15.0, "y": 22.0},
            {"x": 10.0, "y": 22.0},
            1.0,
            5.0,
            0.90,
        )
    with pytest.raises(ValueError, match="point must contain the key 'y'"):
        Line(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            1,
            "Test",
            {
                "x": 10.0,
                "y": 20.0,
                "width": 5.0,
                "height": 2.0,
            },
            {"x": 15.0},
            {"x": 10.0, "y": 20.0},
            {"x": 15.0, "y": 22.0},
            {"x": 10.0, "y": 22.0},
            1.0,
            5.0,
            0.90,
        )


@pytest.mark.unit
def test_line_init_invalid_top_right():
    with pytest.raises(ValueError, match="point must be a dictionary"):
        Line(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            1,
            "Test",
            {
                "x": 10.0,
                "y": 20.0,
                "width": 5.0,
                "height": 2.0,
            },
            {"x": 10.0, "y": 20.0},
            1,
            {"x": 15.0, "y": 22.0},
            {"x": 10.0, "y": 22.0},
            1.0,
            5.0,
            0.90,
        )
    with pytest.raises(ValueError, match="point must contain the key 'y'"):
        Line(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            1,
            "Test",
            {
                "x": 10.0,
                "y": 20.0,
                "width": 5.0,
                "height": 2.0,
            },
            {"x": 10.0, "y": 20.0},
            {"x": 15.0},
            {"x": 15.0, "y": 22.0},
            {"x": 10.0, "y": 22.0},
            1.0,
            5.0,
            0.90,
        )


@pytest.mark.unit
def test_line_init_invalid_bottom_left():
    with pytest.raises(ValueError, match="point must be a dictionary"):
        Line(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            1,
            "Test",
            {
                "x": 10.0,
                "y": 20.0,
                "width": 5.0,
                "height": 2.0,
            },
            {"x": 10.0, "y": 20.0},
            {"x": 15.0, "y": 22.0},
            1,
            {"x": 10.0, "y": 22.0},
            1.0,
            5.0,
            0.90,
        )
    with pytest.raises(ValueError, match="point must contain the key 'y'"):
        Line(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            1,
            "Test",
            {
                "x": 10.0,
                "y": 20.0,
                "width": 5.0,
                "height": 2.0,
            },
            {"x": 10.0, "y": 20.0},
            {"x": 15.0, "y": 22.0},
            {"x": 15.0},
            {"x": 10.0, "y": 22.0},
            1.0,
            5.0,
            0.90,
        )


@pytest.mark.unit
def test_line_init_invalid_bottom_right():
    with pytest.raises(ValueError, match="point must be a dictionary"):
        Line(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            1,
            "Test",
            {
                "x": 10.0,
                "y": 20.0,
                "width": 5.0,
                "height": 2.0,
            },
            {"x": 10.0, "y": 20.0},
            {"x": 15.0, "y": 22.0},
            {"x": 10.0, "y": 22.0},
            1,
            1.0,
            5.0,
            0.90,
        )
    with pytest.raises(ValueError, match="point must contain the key 'y'"):
        Line(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            1,
            "Test",
            {
                "x": 10.0,
                "y": 20.0,
                "width": 5.0,
                "height": 2.0,
            },
            {"x": 10.0, "y": 20.0},
            {"x": 15.0, "y": 22.0},
            {"x": 10.0, "y": 22.0},
            {"x": 15.0},
            1.0,
            5.0,
            0.90,
        )


@pytest.mark.unit
def test_line_init_invalid_angle():
    with pytest.raises(
        ValueError,
        match="angle_degrees must be float or int, got",
    ):
        Line(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            1,
            "Test",
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
            "1.0",
            5.0,
            0.90,
        )
    with pytest.raises(
        ValueError,
        match="angle_radians must be float or int, got",
    ):
        Line(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            1,
            "Test",
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
            "5.0",
            0.90,
        )


@pytest.mark.unit
def test_line_init_invalid_confidence():
    # fmt: off
    with pytest.raises(
        ValueError,
        match="confidence must be float or int, got",
    ):
        Line(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            1,
            "Test",
            {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, },
            {"x": 15.0, "y": 20.0},
            {"x": 10.0, "y": 20.0},
            {"x": 15.0, "y": 22.0},
            {"x": 10.0, "y": 22.0},
            1.0,
            5.0,
            "0.90",
        )
    line = Line(
        "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        1,
        "Test",
        {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, },
        {"x": 15.0, "y": 20.0},
        {"x": 10.0, "y": 20.0},
        {"x": 15.0, "y": 22.0},
        {"x": 10.0, "y": 22.0},
        1.0,
        5.0,
        1,
    )
    assert line.confidence == 1.0
    with pytest.raises(
        ValueError,
        match="confidence must be between 0 and 1, got",
    ):
        Line(
            "3f52804b-2fad-4e00-92c8-b593da3a8ed3",
            1,
            "Test",
            {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, },
            {"x": 15.0, "y": 20.0},
            {"x": 10.0, "y": 20.0},
            {"x": 15.0, "y": 22.0},
            {"x": 10.0, "y": 22.0},
            1.0,
            5.0,
            -0.90,
        )
    # fmt: on


@pytest.mark.unit
def test_line_key(example_line):
    """Test the Line.key method"""
    assert example_line.key == {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "LINE#00001"},
    }


@pytest.mark.unit
def test_line_gsi1_key(example_line):
    """Test the Line.gsi1_key property"""
    assert example_line.gsi1_key() == {
        "GSI1PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "GSI1SK": {"S": "LINE#00001"},
    }


@pytest.mark.unit
def test_line_to_item(example_line):
    """Test the Line.to_item() method"""
    item = example_line.to_item()
    assert item["PK"] == {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"}
    assert item["SK"] == {"S": "LINE#00001"}
    assert item["GSI1PK"] == {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"}
    assert item["GSI1SK"] == {"S": "LINE#00001"}
    assert item["TYPE"] == {"S": "LINE"}
    assert item["text"] == {"S": "Test"}
    assert item["bounding_box"] == {
        "M": {
            "height": {"N": "2.00000000000000000000"},
            "width": {"N": "5.00000000000000000000"},
            "x": {"N": "10.00000000000000000000"},
            "y": {"N": "20.00000000000000000000"},
        }
    }
    assert item["top_right"] == {
        "M": {
            "x": {"N": "15.00000000000000000000"},
            "y": {"N": "20.00000000000000000000"},
        }
    }
    assert item["top_left"] == {
        "M": {
            "x": {"N": "10.00000000000000000000"},
            "y": {"N": "20.00000000000000000000"},
        }
    }
    assert item["bottom_right"] == {
        "M": {
            "x": {"N": "15.00000000000000000000"},
            "y": {"N": "22.00000000000000000000"},
        }
    }
    assert item["bottom_left"] == {
        "M": {
            "x": {"N": "10.00000000000000000000"},
            "y": {"N": "22.00000000000000000000"},
        }
    }
    assert item["angle_degrees"] == {"N": "1.000000000000000000"}
    assert item["angle_radians"] == {"N": "5.000000000000000000"}
    assert item["confidence"] == {"N": "0.90"}


@pytest.mark.unit
def test_line_calculate_centroid(example_line):
    """Test the Line.calculate_centroid() method"""
    centroid = example_line.calculate_centroid()
    assert centroid == (12.5, 21.0)


@pytest.mark.unit
@pytest.mark.parametrize("dx, dy", [(5, -2), (0, 0), (-3, 10)])
def test_line_translate(dx, dy):
    """
    Test that translate(dx, dy) shifts the corner points correctly
    and updates the bounding_box accordingly.
    """
    line = create_test_line()

    # Original corners and bounding box
    orig_top_right = line.top_right.copy()
    orig_top_left = line.top_left.copy()
    orig_bottom_right = line.bottom_right.copy()
    orig_bottom_left = line.bottom_left.copy()
    orig_bb = line.bounding_box.copy()

    # Translate
    line.translate(dx, dy)

    # Check corners have been updated
    assert line.top_right["x"] == pytest.approx(orig_top_right["x"] + dx)
    assert line.top_right["y"] == pytest.approx(orig_top_right["y"] + dy)

    assert line.top_left["x"] == pytest.approx(orig_top_left["x"] + dx)
    assert line.top_left["y"] == pytest.approx(orig_top_left["y"] + dy)

    assert line.bottom_right["x"] == pytest.approx(orig_bottom_right["x"] + dx)
    assert line.bottom_right["y"] == pytest.approx(orig_bottom_right["y"] + dy)

    assert line.bottom_left["x"] == pytest.approx(orig_bottom_left["x"] + dx)
    assert line.bottom_left["y"] == pytest.approx(orig_bottom_left["y"] + dy)

    # Check that the bounding_box is translated as well
    expected_bb = {
        "x": orig_bb["x"] + dx,
        "y": orig_bb["y"] + dy,
        "width": orig_bb["width"],
        "height": orig_bb["height"],
    }
    assert line.bounding_box == expected_bb


@pytest.mark.unit
@pytest.mark.parametrize("sx, sy", [(2, 3), (1, 1), (0.5, 2)])
def test_line_scale(sx, sy):
    """
    Test that scale(sx, sy) scales both the corner points and the bounding_box,
    and does NOT modify angles.
    """
    line = create_test_line()

    # Original corners and bounding box
    orig_top_right = line.top_right.copy()
    orig_top_left = line.top_left.copy()
    orig_bottom_right = line.bottom_right.copy()
    orig_bottom_left = line.bottom_left.copy()
    orig_bb = line.bounding_box.copy()

    line.scale(sx, sy)

    # Check corners
    assert line.top_right["x"] == pytest.approx(orig_top_right["x"] * sx)
    assert line.top_right["y"] == pytest.approx(orig_top_right["y"] * sy)

    assert line.top_left["x"] == pytest.approx(orig_top_left["x"] * sx)
    assert line.top_left["y"] == pytest.approx(orig_top_left["y"] * sy)

    assert line.bottom_right["x"] == pytest.approx(orig_bottom_right["x"] * sx)
    assert line.bottom_right["y"] == pytest.approx(orig_bottom_right["y"] * sy)

    assert line.bottom_left["x"] == pytest.approx(orig_bottom_left["x"] * sx)
    assert line.bottom_left["y"] == pytest.approx(orig_bottom_left["y"] * sy)

    # Check bounding_box
    assert line.bounding_box["x"] == pytest.approx(orig_bb["x"] * sx)
    assert line.bounding_box["y"] == pytest.approx(orig_bb["y"] * sy)
    assert line.bounding_box["width"] == pytest.approx(orig_bb["width"] * sx)
    assert line.bounding_box["height"] == pytest.approx(orig_bb["height"] * sy)

    # Angles should not change
    assert line.angle_degrees == 0.0
    assert line.angle_radians == 0.0


@pytest.mark.unit
@pytest.mark.parametrize(
    "angle, use_radians, should_raise",
    [  # Degrees in the valid range
        (90, False, False),
        (-90, False, False),
        (45, False, False),
        (0, False, False),
        # Degrees outside the valid range => expect ValueError
        (91, False, True),
        (-91, False, True),
        (180, False, True),
        # Radians in the valid range ([-π/2, π/2])
        (math.pi / 2, True, False),
        (-math.pi / 2, True, False),
        (0, True, False),
        (0.5, True, False),
        # Radians outside the valid range => expect ValueError
        (math.pi / 2 + 0.01, True, True),
        (-math.pi / 2 - 0.01, True, True),
        (math.pi, True, True),
    ],
)
def test_line_rotate_limited_range(angle, use_radians, should_raise):
    """
    Test that rotate(angle, origin_x, origin_y, use_radians) rotates the line
    only if the angle is within the allowed range, and that it updates the
    corner positions, angles, and bounding_box based on the new rotated corner
    positions.
    """
    line = create_test_line()
    orig_corners = {
        "top_right": line.top_right.copy(),
        "top_left": line.top_left.copy(),
        "bottom_right": line.bottom_right.copy(),
        "bottom_left": line.bottom_left.copy(),
    }
    orig_angle_degrees = line.angle_degrees
    orig_angle_radians = line.angle_radians

    if should_raise:
        with pytest.raises(ValueError):
            line.rotate(angle, 0, 0, use_radians=use_radians)

        # After a failed rotation, the line should be unchanged.
        assert line.top_right == orig_corners["top_right"]
        assert line.top_left == orig_corners["top_left"]
        assert line.bottom_right == orig_corners["bottom_right"]
        assert line.bottom_left == orig_corners["bottom_left"]
        assert line.angle_degrees == orig_angle_degrees
        assert line.angle_radians == orig_angle_radians

    else:
        # Rotation should succeed without error.
        line.rotate(angle, 0, 0, use_radians=use_radians)

        # Instead of expecting the bounding_box to be unchanged,
        # compute the expected bounding_box from the rotated corners.
        # fmt: off
        expected_bb = {
            "x": min(
                line.top_right["x"],
                line.top_left["x"],
                line.bottom_right["x"],
                line.bottom_left["x"],
            ),
            "y": min(
                line.top_right["y"],
                line.top_left["y"],
                line.bottom_right["y"],
                line.bottom_left["y"],
            ),
            "width": max(
                line.top_right["x"],
                line.top_left["x"],
                line.bottom_right["x"],
                line.bottom_left["x"],
            ) - min(
                line.top_right["x"],
                line.top_left["x"],
                line.bottom_right["x"],
                line.bottom_left["x"],
            ),
            "height": max(
                line.top_right["y"],
                line.top_left["y"],
                line.bottom_right["y"],
                line.bottom_left["y"],
            ) - min(
                line.top_right["y"],
                line.top_left["y"],
                line.bottom_right["y"],
                line.bottom_left["y"],
            ),
        }
        # fmt: on
        assert line.bounding_box == expected_bb

        # Verify that at least one of the corners has changed unless the
        # rotation angle is zero.
        if angle not in (0, 0.0):
            corners_changed = (
                any(
                    line.top_right[k] != orig_corners["top_right"][k]
                    for k in ["x", "y"]
                )
                or any(
                    line.top_left[k] != orig_corners["top_left"][k] for k in ["x", "y"]
                )
                or any(
                    line.bottom_right[k] != orig_corners["bottom_right"][k]
                    for k in ["x", "y"]
                )
                or any(
                    line.bottom_left[k] != orig_corners["bottom_left"][k]
                    for k in ["x", "y"]
                )
            )
            assert corners_changed, "Expected corners to change after valid rotation."
        else:
            assert line.top_right == orig_corners["top_right"]
            assert line.top_left == orig_corners["top_left"]
            assert line.bottom_right == orig_corners["bottom_right"]
            assert line.bottom_left == orig_corners["bottom_left"]

        # Verify the angles have been updated appropriately.
        if use_radians:
            # When using radians, the angle increments are in radians.
            assert line.angle_radians == pytest.approx(
                orig_angle_radians + angle, abs=1e-9
            )
            assert line.angle_degrees == pytest.approx(
                orig_angle_degrees + math.degrees(angle), abs=1e-9
            )
        else:
            # When using degrees, the increments are in degrees.
            assert line.angle_degrees == pytest.approx(
                orig_angle_degrees + angle, abs=1e-9
            )
            assert line.angle_radians == pytest.approx(
                orig_angle_radians + math.radians(angle), abs=1e-9
            )


@pytest.mark.unit
@pytest.mark.parametrize(
    "shx, shy, pivot_x, pivot_y, expected_corners",
    [  # Test 1: Horizontal shear only (shx nonzero, shy=0)
        (
            0.2,
            0.0,
            10.0,
            20.0,
            {
                # (15, 20)
                "top_right": {"x": 15.0 + 0.2 * (20.0 - 20.0), "y": 20.0},
                # (10,20)
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
                # (10,20)
                "top_left": {"x": 10.0, "y": 20.0 + 0.2 * (10.0 - 10.0)},
                # (15,23)
                "bottom_right": {"x": 15.0, "y": 22.0 + 0.2 * (15.0 - 10.0)},
                # (10,22)
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
def test_line_shear(shx, shy, pivot_x, pivot_y, expected_corners):
    """
    Test that the shear(shx, shy, pivot_x, pivot_y) method correctly shears
    the corners of the line and recalculates the bounding box.
    """
    line = create_test_line()

    # Apply shear transformation
    line.shear(shx, shy, pivot_x, pivot_y)

    # Check each corner against the expected values
    for corner_name in [
        "top_right",
        "top_left",
        "bottom_right",
        "bottom_left",
    ]:
        for coord in ["x", "y"]:
            expected_value = expected_corners[corner_name][coord]
            actual_value = line.__dict__[corner_name][coord]
            assert actual_value == pytest.approx(
                expected_value
            ), f"{corner_name} {coord} expected {expected_value},"
            f" got {actual_value}"

    # Compute expected bounding box from the updated corners
    xs = [
        line.top_right["x"],
        line.top_left["x"],
        line.bottom_right["x"],
        line.bottom_left["x"],
    ]
    ys = [
        line.top_right["y"],
        line.top_left["y"],
        line.bottom_right["y"],
        line.bottom_left["y"],
    ]
    expected_bb = {
        "x": min(xs),
        "y": min(ys),
        "width": max(xs) - min(xs),
        "height": max(ys) - min(ys),
    }
    assert line.bounding_box["x"] == pytest.approx(expected_bb["x"])
    assert line.bounding_box["y"] == pytest.approx(expected_bb["y"])
    assert line.bounding_box["width"] == pytest.approx(expected_bb["width"])
    assert line.bounding_box["height"] == pytest.approx(expected_bb["height"])


@pytest.mark.unit
def test_line_warp_affine():
    """
    Test that warp_affine(a, b, c, d, e, f) applies the affine transform
    x' = a*x + b*y + c, y' = d*x + e*y + f to all corners,
    recomputes the bounding box, and updates the angle accordingly.
    """
    # Create a test line with known corner positions:
    line = create_test_line()
    # Our test line has:
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
    line.warp_affine(a, b, c, d, e, f)

    # Verify the transformed corners.
    assert line.top_left["x"] == pytest.approx(expected_top_left["x"])
    assert line.top_left["y"] == pytest.approx(expected_top_left["y"])
    assert line.top_right["x"] == pytest.approx(expected_top_right["x"])
    assert line.top_right["y"] == pytest.approx(expected_top_right["y"])
    assert line.bottom_left["x"] == pytest.approx(expected_bottom_left["x"])
    assert line.bottom_left["y"] == pytest.approx(expected_bottom_left["y"])
    assert line.bottom_right["x"] == pytest.approx(expected_bottom_right["x"])
    assert line.bottom_right["y"] == pytest.approx(expected_bottom_right["y"])

    # Verify that the bounding_box has been recalculated correctly.
    assert line.bounding_box["x"] == pytest.approx(expected_bb["x"])
    assert line.bounding_box["y"] == pytest.approx(expected_bb["y"])
    assert line.bounding_box["width"] == pytest.approx(expected_bb["width"])
    assert line.bounding_box["height"] == pytest.approx(expected_bb["height"])

    # Verify that the angle has been updated correctly.
    # Here we expect 0 radians and 0 degrees since the top edge remains
    # horizontal.
    assert line.angle_radians == pytest.approx(0.0)
    assert line.angle_degrees == pytest.approx(0.0)


@pytest.mark.unit
def test_line_warp_affine_normalized_forward():
    """
    Test that Line.warp_affine_normalized_forward() applies a normalized
    affine transformation to all four corners, recalculates the bounding box
    correctly, and leaves the angle unchanged.

    The transformation parameters are:
      - a_f = 1.0, b_f = 0.0, c_f = 0.1,
      - d_f = 0.0, e_f = 1.0, f_f = 0.2,
      - orig_width = 5.0, orig_height = 2.0,
      - new_width = 5.0, new_height = 2.0,
      - flip_y = False.

    For example, for the top_left corner:
      x_old = 10.0 * 5.0 = 50.0,  y_old = 20.0 * 2.0 = 40.0;
      x_new_px = 1.0 * 50.0 + 0.0 * 40.0 + 0.1 = 50.1,
      y_new_px = 0.0 * 50.0 + 1.0 * 40.0 + 0.2 = 40.2;
      then x' = 50.1 / 5.0 = 10.02,  y' = 40.2 / 2.0 = 20.1.

    Similar calculations yield:
      - top_right becomes (15.02, 20.1),
      - bottom_left becomes (10.02, 22.1),
      - bottom_right becomes (15.02, 22.1).

    The new bounding box should be:
      {x: 10.02, y: 20.1, width: 5.0, height: 2.0},
    and the angle should remain 0.
    """
    # Create a test Line instance.
    line = create_test_line()

    # Define the normalized affine transformation parameters.
    a_f, b_f, c_f = 1.0, 0.0, 0.1
    d_f, e_f, f_f = 0.0, 1.0, 0.2
    orig_width, orig_height = 5.0, 2.0
    new_width, new_height = 5.0, 2.0
    flip_y = False

    # Expected new corner positions after the transformation.
    expected_top_left = {"x": 10.02, "y": 20.1}
    expected_top_right = {"x": 15.02, "y": 20.1}
    expected_bottom_left = {"x": 10.02, "y": 22.1}
    expected_bottom_right = {"x": 15.02, "y": 22.1}
    expected_bb = {"x": 10.02, "y": 20.1, "width": 5.0, "height": 2.0}

    # Apply the normalized forward affine transformation.
    line.warp_affine_normalized_forward(
        a_f,
        b_f,
        c_f,
        d_f,
        e_f,
        f_f,
        orig_width,
        orig_height,
        new_width,
        new_height,
        flip_y,
    )

    # Verify that each corner was transformed correctly.
    assert line.top_left["x"] == pytest.approx(expected_top_left["x"])
    assert line.top_left["y"] == pytest.approx(expected_top_left["y"])
    assert line.top_right["x"] == pytest.approx(expected_top_right["x"])
    assert line.top_right["y"] == pytest.approx(expected_top_right["y"])
    assert line.bottom_left["x"] == pytest.approx(expected_bottom_left["x"])
    assert line.bottom_left["y"] == pytest.approx(expected_bottom_left["y"])
    assert line.bottom_right["x"] == pytest.approx(expected_bottom_right["x"])
    assert line.bottom_right["y"] == pytest.approx(expected_bottom_right["y"])

    # Verify that the bounding box was recalculated correctly.
    assert line.bounding_box["x"] == pytest.approx(expected_bb["x"])
    assert line.bounding_box["y"] == pytest.approx(expected_bb["y"])
    assert line.bounding_box["width"] == pytest.approx(expected_bb["width"])
    assert line.bounding_box["height"] == pytest.approx(expected_bb["height"])

    # Verify that the angle remains unchanged (i.e. still 0).
    assert line.angle_degrees == pytest.approx(0.0)
    assert line.angle_radians == pytest.approx(0.0)


@pytest.mark.unit
def test_line_rotate_90_ccw_in_place():
    """
    Test the rotate_90_ccw_in_place method of the Line class.

    Using old image dimensions (old_w=100, old_h=200),
    the test line with corners:
      - top_left:      (10.0, 20.0)
      - top_right:     (15.0, 20.0)
      - bottom_right:  (15.0, 22.0)
      - bottom_left:   (10.0, 22.0)
    is rotated 90° counter-clockwise in-place. The expected transformation is:

      1. Multiply by old_w and old_h:
         - top_left becomes (10.0*100, 20.0*200) = (1000, 4000)
         - top_right becomes (15.0*100, 20.0*200) = (1500, 4000)
         - bottom_right becomes (15.0*100, 22.0*200) = (1500, 4400)
         - bottom_left becomes (10.0*100, 22.0*200) = (1000, 4400)
      2. Rotate in pixel space by replacing each corner:
         - new x = original y (in pixels)
         - new y = old_w - original x (in pixels)
         Thus:
         - top_left:      (4000, 100 - 1000) = (4000, -900)
         - top_right:     (4000, 100 - 1500) = (4000, -1400)
         - bottom_right:  (4400, 100 - 1500) = (4400, -1400)
         - bottom_left:   (4400, 100 - 1000) = (4400, -900)
      3. Re-normalize using the new image dimensions:
         The new width is final_w = old_h = 200 and the new height is
         final_h = old_w = 100.
         - top_left:      (4000/200, -900/100) = (20.0, -9.0)
         - top_right:     (4000/200, -1400/100) = (20.0, -14.0)
         - bottom_right:  (4400/200, -1400/100) = (22.0, -14.0)
         - bottom_left:   (4400/200, -900/100) = (22.0, -9.0)
      4. The updated bounding box should be:
         {"x": 20.0, "y": -14.0, "width": 2.0, "height": 5.0},
         and the angle is increased by 90° (angle_degrees becomes 90 and
         angle_radians becomes π/2).
    """
    # Create a test Line object.
    line = create_test_line()
    # Reset angles to zero for a clean test.
    line.angle_degrees = 0.0
    line.angle_radians = 0.0

    # Define the original image dimensions.
    old_w = 100
    old_h = 200

    # Apply the 90° counter-clockwise rotation.
    line.rotate_90_ccw_in_place(old_w, old_h)

    # Expected corner positions after rotation.
    expected_top_left = {"x": 20.0, "y": -9.0}
    expected_top_right = {"x": 20.0, "y": -14.0}
    expected_bottom_right = {"x": 22.0, "y": -14.0}
    expected_bottom_left = {"x": 22.0, "y": -9.0}

    # Expected bounding box and angle.
    expected_bb = {"x": 20.0, "y": -14.0, "width": 2.0, "height": 5.0}
    expected_angle_degrees = 90.0
    expected_angle_radians = math.pi / 2

    # Verify the updated corner positions.
    assert line.top_left["x"] == pytest.approx(expected_top_left["x"])
    assert line.top_left["y"] == pytest.approx(expected_top_left["y"])
    assert line.top_right["x"] == pytest.approx(expected_top_right["x"])
    assert line.top_right["y"] == pytest.approx(expected_top_right["y"])
    assert line.bottom_right["x"] == pytest.approx(expected_bottom_right["x"])
    assert line.bottom_right["y"] == pytest.approx(expected_bottom_right["y"])
    assert line.bottom_left["x"] == pytest.approx(expected_bottom_left["x"])
    assert line.bottom_left["y"] == pytest.approx(expected_bottom_left["y"])

    # Verify that the bounding box has been updated correctly.
    assert line.bounding_box["x"] == pytest.approx(expected_bb["x"])
    assert line.bounding_box["y"] == pytest.approx(expected_bb["y"])
    assert line.bounding_box["width"] == pytest.approx(expected_bb["width"])
    assert line.bounding_box["height"] == pytest.approx(expected_bb["height"])

    # Verify that the angle has been updated correctly.
    assert line.angle_degrees == pytest.approx(expected_angle_degrees)
    assert line.angle_radians == pytest.approx(expected_angle_radians)


@pytest.mark.unit
def test_line_repr(example_line):
    """Test the Line.__repr__() method"""
    assert (
        repr(example_line) == "Line("
        "image_id='3f52804b-2fad-4e00-92c8-b593da3a8ed3', "
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
    """Test the Line.__iter__() method"""
    line_dict = dict(example_line)
    expected_keys = {
        "image_id",
        "line_id",
        "text",
        "bounding_box",
        "top_right",
        "top_left",
        "bottom_right",
        "bottom_left",
        "angle_degrees",
        "angle_radians",
        "confidence",
    }
    assert set(line_dict.keys()) == expected_keys
    assert line_dict["image_id"] == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert line_dict["line_id"] == 1
    assert line_dict["text"] == "Test"
    assert line_dict["bounding_box"] == {
        "x": 10.0,
        "y": 20.0,
        "width": 5.0,
        "height": 2.0,
    }
    assert line_dict["top_right"] == {"x": 15.0, "y": 20.0}
    assert line_dict["top_left"] == {"x": 10.0, "y": 20.0}
    assert line_dict["bottom_right"] == {"x": 15.0, "y": 22.0}
    assert line_dict["bottom_left"] == {"x": 10.0, "y": 22.0}
    assert line_dict["angle_degrees"] == 1
    assert line_dict["angle_radians"] == 5
    assert line_dict["confidence"] == 0.90
    assert Line(**dict(example_line)) == example_line


@pytest.mark.unit
def test_line_eq():
    """Test the Line.__eq__() method"""
    # fmt: off
    l1 = Line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, "Test", {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.90)  # noqa: E501
    l2 = Line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, "Test", {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.90)  # noqa: E501
    l3 = Line("3f52804b-2fad-4e00-92c8-b593da3a8ed4", 1, "Test", {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.90)  # Different Image ID # noqa: E501
    l4 = Line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 2, "Test", {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.90)  # Different ID # noqa: E501
    l5 = Line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, "Test2", {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.90)  # Different text # noqa: E501
    l6 = Line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, "Test", {"x": 20.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.90)  # Different bounding box # noqa: E501
    l7 = Line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, "Test", {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 20.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.90)  # Different top_right # noqa: E501
    l8 = Line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, "Test", {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 15.0, "y": 20.0}, {"x": 20.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.90)  # Different top_left # noqa: E501
    l9 = Line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, "Test", {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 20.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.90)  # Different bottom_right # noqa: E501
    l10 = Line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, "Test", {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 20.0, "y": 22.0}, 1.0, 5.0, 0.90)  # Different bottom_left # noqa: E501
    l11 = Line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, "Test", {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 2.0, 5.0, 0.90)  # Different angle_degrees # noqa: E501
    l12 = Line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, "Test", {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 6.0, 0.90)  # Different angle_radians # noqa: E501
    l13 = Line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, "Test", {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.91)  # Different confidence # noqa: E501
    # fmt: on

    assert l1 == l2
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
    assert l1 != "Test"


@pytest.mark.unit
def test_line_hash(example_line):
    """Test the Line __hash__ method and the set behavior for Line objects."""
    # Create a duplicate of example_line by converting it to an item and back.
    duplicate_line = item_to_line(example_line.to_item())

    # Confirm that converting a Line to an item and back yields the same hash.
    assert hash(example_line) == hash(duplicate_line)

    # When added to a set with its duplicate, the set should contain only one
    # Line object.
    line_set = {example_line, duplicate_line}
    assert len(line_set) == 1

    # Create a different line by modifying one parameter (e.g., line_id).
    different_line = Line(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",  # same image_id
        line_id=2,  # different line_id makes this line different
        text="Test line",
        bounding_box={"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0},
        top_right={"x": 15.0, "y": 20.0},
        top_left={"x": 10.0, "y": 20.0},
        bottom_right={"x": 15.0, "y": 22.0},
        bottom_left={"x": 10.0, "y": 22.0},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=1.0,
    )

    # When added to a set with the original and its duplicate,
    # the set should contain two unique Line objects.
    line_set = {example_line, duplicate_line, different_line}
    assert len(line_set) == 2


@pytest.mark.unit
def test_item_to_line(example_line):
    item_to_line(example_line.to_item()) == example_line

    # Missing keys
    with pytest.raises(ValueError, match="^Item is missing required keys"):
        item_to_line({"SK": {"S": "LINE#00001"}})

    # Bad keys
    with pytest.raises(ValueError, match="^Error converting item to Line"):
        item_to_line(
            {
                "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
                "SK": {"S": "LINE#00001"},
                "TYPE": {"S": "LINE"},
                "text": {"N": "100"},  # This should be a string
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


# =============================================================================
# Tests for calculate_corners() method (Phase 1: Simplified PHOTO transform)
# =============================================================================


@pytest.fixture
def normalized_line():
    """Create a line with normalized coordinates (0-1 range)."""
    return Line(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=1,
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
def test_line_calculate_corners_no_scaling(normalized_line):
    """Test calculate_corners without width/height scaling."""
    corners = normalized_line.calculate_corners()

    # Should return normalized coordinates as-is
    assert corners[0] == (0.1, 0.9)  # top_left
    assert corners[1] == (0.9, 0.9)  # top_right
    assert corners[2] == (0.1, 0.8)  # bottom_left
    assert corners[3] == (0.9, 0.8)  # bottom_right


@pytest.mark.unit
def test_line_calculate_corners_with_scaling(normalized_line):
    """Test calculate_corners with width/height scaling to pixel coordinates."""
    corners = normalized_line.calculate_corners(width=1000, height=800)

    # Should scale to pixel coordinates
    assert corners[0] == pytest.approx((100.0, 720.0))  # top_left: 0.1*1000, 0.9*800
    assert corners[1] == pytest.approx((900.0, 720.0))  # top_right: 0.9*1000, 0.9*800
    assert corners[2] == pytest.approx((100.0, 640.0))  # bottom_left: 0.1*1000, 0.8*800
    assert corners[3] == pytest.approx((900.0, 640.0))  # bottom_right: 0.9*1000, 0.8*800


@pytest.mark.unit
def test_line_calculate_corners_with_flip_y(normalized_line):
    """Test calculate_corners with Y-axis flip for image coordinate systems."""
    corners = normalized_line.calculate_corners(width=1000, height=800, flip_y=True)

    # With flip_y, y_pixel = height - (y_normalized * height)
    # top_left: y = 800 - (0.9 * 800) = 800 - 720 = 80
    # bottom_left: y = 800 - (0.8 * 800) = 800 - 640 = 160
    assert corners[0] == pytest.approx((100.0, 80.0))  # top_left
    assert corners[1] == pytest.approx((900.0, 80.0))  # top_right
    assert corners[2] == pytest.approx((100.0, 160.0))  # bottom_left
    assert corners[3] == pytest.approx((900.0, 160.0))  # bottom_right


@pytest.mark.unit
def test_line_calculate_corners_requires_both_dimensions():
    """Test that calculate_corners raises error if only one dimension provided."""
    line = create_test_line()

    with pytest.raises(ValueError, match="Both width and height must be provided"):
        line.calculate_corners(width=1000)

    with pytest.raises(ValueError, match="Both width and height must be provided"):
        line.calculate_corners(height=800)


@pytest.mark.unit
def test_line_calculate_corners_typical_receipt():
    """Test calculate_corners with typical receipt line coordinates."""
    # Simulate a line near the top of a receipt (high Y in normalized coords)
    line = Line(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=1,
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

    # Image dimensions typical for phone photo
    corners = line.calculate_corners(width=3024, height=4032, flip_y=True)

    # Verify corners are in expected pixel ranges
    # Top-left X should be around 15% of width = ~453
    assert 450 < corners[0][0] < 460
    # Top-left Y with flip should be near top of image (low Y value)
    # y = 4032 - (0.88 * 4032) = 4032 - 3548 = 484
    assert 480 < corners[0][1] < 490


@pytest.mark.unit
def test_line_calculate_corners_tilted_line():
    """Test calculate_corners with a tilted line (non-axis-aligned corners)."""
    # Simulate a slightly tilted line
    line = Line(
        image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3",
        line_id=1,
        text="TILTED TEXT",
        bounding_box={"x": 0.1, "y": 0.5, "width": 0.8, "height": 0.05},
        top_right={"x": 0.92, "y": 0.56},  # Slightly higher on right
        top_left={"x": 0.1, "y": 0.55},
        bottom_right={"x": 0.9, "y": 0.51},
        bottom_left={"x": 0.08, "y": 0.5},
        angle_degrees=2.0,
        angle_radians=0.0349,
        confidence=0.95,
    )

    corners = line.calculate_corners(width=1000, height=1000, flip_y=True)

    # Verify the tilt is preserved in pixel coordinates
    # Top-right Y should be lower than top-left Y (after flip)
    assert corners[1][1] < corners[0][1]  # TR.y < TL.y (after flip, lower Y = higher in image)
