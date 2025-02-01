import pytest
import math
from dynamo import Line, itemToLine


@pytest.fixture
def example_line():
    # fmt: off
    return Line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, "Test", { "x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, }, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.90)
    # fmt: on


def create_test_line():
    """
    Helper function to create a Line object with easily verifiable points.
    Adjust coordinates as needed for your tests.
    """
    # fmt: off
    return Line( image_id="3f52804b-2fad-4e00-92c8-b593da3a8ed3", id=1, text="Test", bounding_box={"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, top_right={"x": 15.0, "y": 20.0}, top_left={"x": 10.0, "y": 20.0}, bottom_right={"x": 15.0, "y": 22.0}, bottom_left={"x": 10.0, "y": 22.0}, angle_degrees=0.0, angle_radians=0.0, confidence=1.0, )
    # fmt: on


@pytest.mark.unit
def test_init(example_line):
    """Test the Line constructor"""
    assert example_line.image_id == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert example_line.id == 1
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
def test_init_bad_uuid():
    """Test the Line constructor with bad ID"""
    # fmt: off
    with pytest.raises(ValueError, match="uuid must be a string"):
        Line(1, 1, "Test", { "x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, }, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.90)
    with pytest.raises(ValueError, match="uuid must be a valid UUID"):
        Line("not-a-uuid", 1, "Test", { "x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, }, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.90)
    # fmt: on


@pytest.mark.unit
def test_init_bad_id():
    # fmt: off
    with pytest.raises(ValueError, match="id must be an integer"):
        Line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", "not-an-int", "Test", { "x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, }, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.90)
    with pytest.raises(ValueError, match="id must be positive"):
        Line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", -1, "Test", { "x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, }, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.90)
    # fmt: on


@pytest.mark.unit
def test_init_bad_text():
    # fmt: off
    with pytest.raises(ValueError, match="text must be a string"):
        Line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, 1, { "x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, }, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.90)
    # fmt: on


@pytest.mark.unit
def test_init_bad_bounding_box():
    # fmt: off
    with pytest.raises(ValueError, match="bounding_box must be a dictionary"):
        Line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, "Test", 1, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.90)
    with pytest.raises(ValueError, match="bounding_box must contain the key 'height'"):
        Line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, "Test", {"x": 10.0, "y": 20.0, "width": 5.0}, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.90)
    # fmt: on


@pytest.mark.unit
def test_init_bad_top_left():
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
def test_init_bad_top_right():
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
def test_init_bad_bottom_left():
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
def test_init_bad_bottom_right():
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
def test_init_bad_angle():
    # fmt: off
    with pytest.raises(ValueError, match="angle_degrees must be a float or int"):
        Line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, "Test", { "x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, }, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, "1.0", 5.0, 0.90)
    with pytest.raises(ValueError, match="angle_radians must be a float or int"):
        Line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, "Test", { "x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, }, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, "5.0", 0.90)
    # fmt: on


@pytest.mark.unit
def test_init_bad_confidence():
    # fmt: off
    with pytest.raises(ValueError, match="confidence must be a float"):
        Line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, "Test", { "x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, }, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, "0.90")
    line = Line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, "Test", { "x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, }, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 1)
    assert line.confidence == 1.0
    with pytest.raises(ValueError, match="confidence must be between 0 and 1"):
        Line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, "Test", { "x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0, }, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, -0.90)
    # fmt: on

@pytest.mark.unit
def test_key(example_line):
    """Test the Line.key() method"""
    assert example_line.key() == {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "LINE#00001"},
    }

@pytest.mark.unit
def test_key(example_line):
    """Test the Line.key() method"""
    assert example_line.key() == {
        "PK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"},
        "SK": {"S": "LINE#00001"},
    }

@pytest.mark.unit
def test_gsi1_key(example_line):
    """Test the Line.gsi1_key() method"""
    assert example_line.gsi1_key() == {
        "GSI1PK": {"S": "IMAGE"},
        "GSI1SK": {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#LINE#00001"},
    }

@pytest.mark.unit
def test_to_item(example_line):
    """Test the Line.to_item() method"""
    item = example_line.to_item()
    assert item["PK"] == {"S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3"}
    assert item["SK"] == {"S": "LINE#00001"}
    assert item["GSI1PK"] == {"S": "IMAGE"}
    assert item["GSI1SK"] == {
        "S": "IMAGE#3f52804b-2fad-4e00-92c8-b593da3a8ed3#LINE#00001"
    }
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
    assert "histogram" in item
    assert "num_chars" in item

@pytest.mark.unit
def test_calculate_centroid(example_line):
    """Test the Line.calculate_centroid() method"""
    centroid = example_line.calculate_centroid()
    assert centroid == (12.5, 21.0)

@pytest.mark.unit
@pytest.mark.parametrize(
    "dx, dy",
    [
        (5, -2),  # Translate right 5, up -2
        (0, 0),  # No translation
        (-3, 10),  # Translate left 3, down 10
    ],
)
def test_translate(dx, dy):
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
@pytest.mark.parametrize(
    "sx, sy",
    [
        (2, 3),  # Scale 2x horizontally, 3x vertically
        (1, 1),  # No scaling
        (0.5, 2),  # Scale down horizontally, up vertically
    ],
)
def test_scale(sx, sy):
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
    [
        # Degrees in the valid range
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
def test_rotate_limited_range(angle, use_radians, should_raise):
    """
    Test that rotate(angle, origin_x, origin_y, use_radians) rotates the line only
    if the angle is within the allowed range, and that it updates the corner positions,
    angles, and bounding_box based on the new rotated corner positions.
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
        expected_bb = { "x": min( line.top_right["x"], line.top_left["x"], line.bottom_right["x"], line.bottom_left["x"], ), "y": min( line.top_right["y"], line.top_left["y"], line.bottom_right["y"], line.bottom_left["y"], ), "width": max( line.top_right["x"], line.top_left["x"], line.bottom_right["x"], line.bottom_left["x"], ) - min( line.top_right["x"], line.top_left["x"], line.bottom_right["x"], line.bottom_left["x"], ), "height": max( line.top_right["y"], line.top_left["y"], line.bottom_right["y"], line.bottom_left["y"], ) - min( line.top_right["y"], line.top_left["y"], line.bottom_right["y"], line.bottom_left["y"], ), }
        # fmt: on
        assert line.bounding_box == expected_bb

        # Verify that at least one of the corners has changed unless the rotation angle is zero.
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
def test_scale():
    """
    Test that scale(sx, sy) scales both the corner points and the bounding_box,
    and does NOT modify angles.
    """
    line = create_test_line()

    orig_top_right = line.top_right.copy()
    orig_top_left = line.top_left.copy()
    orig_bottom_right = line.bottom_right.copy()
    orig_bottom_left = line.bottom_left.copy()
    orig_bb = line.bounding_box.copy()

    sx, sy = 2, 3
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

    # Angles should remain unchanged.
    assert line.angle_degrees == 0.0
    assert line.angle_radians == 0.0


@pytest.mark.unit
def test_repr(example_line):
    """Test the Line.__repr__() method"""
    assert repr(example_line) == "Line(id=1, text='Test')"


@pytest.mark.unit
def test_iter(example_line):
    """Test the Line.__iter__() method"""
    line_dict = dict(example_line)
    expected_keys = {
        "image_id",
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
    }
    assert set(line_dict.keys()) == expected_keys
    assert line_dict["image_id"] == "3f52804b-2fad-4e00-92c8-b593da3a8ed3"
    assert line_dict["id"] == 1
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


@pytest.mark.unit
def test_eq():
    """Test the Line.__eq__() method"""
    # fmt: off
    l1 = Line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, "Test", {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.90)
    l2 = Line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, "Test", {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.90)
    l3 = Line("3f52804b-2fad-4e00-92c8-b593da3a8ed4", 1, "Test", {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.90) # Different Image ID
    l4 = Line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 2, "Test", {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.90) # Different ID
    l5 = Line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, "Test2", {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.90) # Different text
    l6 = Line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, "Test", {"x": 20.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.90) # Different bounding box
    l7 = Line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, "Test", {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 20.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.90) # Different top_right
    l8 = Line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, "Test", {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 15.0, "y": 20.0}, {"x": 20.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.90) # Different top_left
    l9 = Line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, "Test", {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 20.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.90) # Different bottom_right
    l10 = Line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, "Test", {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 20.0, "y": 22.0}, 1.0, 5.0, 0.90) # Different bottom_left
    l11 = Line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, "Test", {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 2.0, 5.0, 0.90) # Different angle_degrees
    l12 = Line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, "Test", {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 6.0, 0.90) # Different angle_radians
    l13 = Line("3f52804b-2fad-4e00-92c8-b593da3a8ed3", 1, "Test", {"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0}, {"x": 15.0, "y": 20.0}, {"x": 10.0, "y": 20.0}, {"x": 15.0, "y": 22.0}, {"x": 10.0, "y": 22.0}, 1.0, 5.0, 0.91) # Different confidence
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
def test_itemToLine(example_line):
    itemToLine(example_line.to_item()) == example_line

    # Missing keys
    with pytest.raises(ValueError, match="^Item is missing required keys"):
        itemToLine({"SK": {"S": "LINE#00001"}})

    # Bad keys
    with pytest.raises(ValueError, match="^Error converting item to Line"):
        itemToLine(
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
