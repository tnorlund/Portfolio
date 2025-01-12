import pytest
from dynamo import Line, itemToLine
import math


def test_init():
    """Test the Line constructor"""
    line = Line(
        1,
        1,
        "07\/03\/2024",
        {
            "x": 0.4454263367632384,
            "height": 0.022867568134581906,
            "width": 0.08690182470506236,
            "y": 0.9167082878750482,
        },
        {"y": 0.9307722198001792, "x": 0.5323281614683008},
        {"y": 0.9395758560096301, "x": 0.44837726658954413},
        {"x": 0.529377231641995, "y": 0.9167082878750482},
        {"x": 0.4454263367632384, "y": 0.9255119240844992},
        -5.986527,
        -0.10448461,
        1,
    )
    assert int(line.image_id) == 1
    assert int(line.id) == 1
    assert line.text == "07\/03\/2024"
    assert line.boundingBox == {
        "x": 0.4454263367632384,
        "height": 0.022867568134581906,
        "width": 0.08690182470506236,
        "y": 0.9167082878750482,
    }
    assert line.top_right == {
        "y": 0.9307722198001792,
        "x": 0.5323281614683008,
    }, "top_right"
    assert line.top_left == {
        "y": 0.9395758560096301,
        "x": 0.44837726658954413,
    }, "top_left"
    assert line.bottom_right == {
        "x": 0.529377231641995,
        "y": 0.9167082878750482,
    }, "bottom_right"
    assert line.bottom_left == {
        "x": 0.4454263367632384,
        "y": 0.9255119240844992,
    }, "bottom_left"
    assert line.angle_degrees == -5.986527
    assert line.angle_radians == -0.10448461
    assert line.confidence == 1.00

    # Test bad Image ID
    with pytest.raises(ValueError):
        Line(
            -1,
            1,
            "07\/03\/2024",
            {
                "x": 0.4454263367632384,
                "height": 0.022867568134581906,
                "width": 0.08690182470506236,
                "y": 0.9167082878750482,
            },
            {"y": 0.9307722198001792, "x": 0.5323281614683008},
            {"y": 0.9395758560096301, "x": 0.44837726658954413},
            {"x": 0.529377231641995, "y": 0.9167082878750482},
            {"x": 0.4454263367632384, "y": 0.9255119240844992},
            -5.986527,
            -0.10448461,
            1,
        )

    # Test bad Line ID
    with pytest.raises(ValueError):
        Line(
            1,
            -1,
            "07\/03\/2024",
            {
                "x": 0.4454263367632384,
                "height": 0.022867568134581906,
                "width": 0.08690182470506236,
                "y": 0.9167082878750482,
            },
            {"y": 0.9307722198001792, "x": 0.5323281614683008},
            {"y": 0.9395758560096301, "x": 0.44837726658954413},
            {"x": 0.529377231641995, "y": 0.9167082878750482},
            {"x": 0.4454263367632384, "y": 0.9255119240844992},
            -5.986527,
            -0.10448461,
            1,
        )

    # Test bad Text
    with pytest.raises(ValueError):
        Line(
            1,
            1,
            1,
            {
                "x": 0.4454263367632384,
                "height": 0.022867568134581906,
                "width": 0.08690182470506236,
                "y": 0.9167082878750482,
            },
            {"y": 0.9307722198001792, "x": 0.5323281614683008},
            {"y": 0.9395758560096301, "x": 0.44837726658954413},
            {"x": 0.529377231641995, "y": 0.9167082878750482},
            {"x": 0.4454263367632384, "y": 0.9255119240844992},
            -5.986527,
            -0.10448461,
            1,
        )

    # Test bad BoundingBox
    with pytest.raises(ValueError):
        Line(
            1,
            1,
            "07\/03\/2024",
            {
                "x": 0.4454263367632384,
                "height": 0.022867568134581906,
                "width": 0.08690182470506236,
            },
            {"y": 0.9307722198001792, "x": 0.5323281614683008},
            {"y": 0.9395758560096301, "x": 0.44837726658954413},
            {"x": 0.529377231641995, "y": 0.9167082878750482},
            {"x": 0.4454263367632384, "y": 0.9255119240844992},
            -5.986527,
            -0.10448461,
            1,
        )

    # Test bad top_right
    with pytest.raises(ValueError):
        Line(
            1,
            1,
            "07\/03\/2024",
            {
                "x": 0.4454263367632384,
                "height": 0.022867568134581906,
                "width": 0.08690182470506236,
                "y": 0.9167082878750482,
            },
            {"x": 0.5323281614683008},
            {"y": 0.9395758560096301, "x": 0.44837726658954413},
            {"x": 0.529377231641995, "y": 0.9167082878750482},
            {"x": 0.4454263367632384, "y": 0.9255119240844992},
            -5.986527,
            -0.10448461,
            1,
        )

    # Test bad top_left
    with pytest.raises(ValueError):
        Line(
            1,
            1,
            "07\/03\/2024",
            {
                "x": 0.4454263367632384,
                "height": 0.022867568134581906,
                "width": 0.08690182470506236,
                "y": 0.9167082878750482,
            },
            {"y": 0.9307722198001792, "x": 0.5323281614683008},
            {"x": 0.44837726658954413},
            {"x": 0.529377231641995, "y": 0.9167082878750482},
            {"x": 0.4454263367632384, "y": 0.9255119240844992},
            -5.986527,
            -0.10448461,
            1,
        )

    # Test bad bottom_right
    with pytest.raises(ValueError):
        Line(
            1,
            1,
            "07\/03\/2024",
            {
                "x": 0.4454263367632384,
                "height": 0.022867568134581906,
                "width": 0.08690182470506236,
                "y": 0.9167082878750482,
            },
            {"y": 0.9307722198001792, "x": 0.5323281614683008},
            {"y": 0.9395758560096301, "x": 0.44837726658954413},
            {"y": 0.9167082878750482},
            {"x": 0.4454263367632384, "y": 0.9255119240844992},
            -5.986527,
            -0.10448461,
            1,
        )

    # Test bad bottom_left
    with pytest.raises(ValueError):
        Line(
            1,
            1,
            "07\/03\/2024",
            {
                "x": 0.4454263367632384,
                "height": 0.022867568134581906,
                "width": 0.08690182470506236,
                "y": 0.9167082878750482,
            },
            {"y": 0.9307722198001792, "x": 0.5323281614683008},
            {"y": 0.9395758560096301, "x": 0.44837726658954413},
            {"x": 0.529377231641995, "y": 0.9167082878750482},
            {"y": 0.9255119240844992},
            -5.986527,
            -0.10448461,
            1,
        )

    # Test bad angle_degrees
    with pytest.raises(ValueError):
        Line(
            1,
            1,
            "07\/03\/2024",
            {
                "x": 0.4454263367632384,
                "height": 0.022867568134581906,
                "width": 0.08690182470506236,
                "y": 0.9167082878750482,
            },
            {"y": 0.9307722198001792, "x": 0.5323281614683008},
            {"y": 0.9395758560096301, "x": 0.44837726658954413},
            {"x": 0.529377231641995, "y": 0.9167082878750482},
            {"x": 0.4454263367632384, "y": 0.9255119240844992},
            "-5.986527",
            -0.10448461,
            1,
        )

    # Test bad angle_radians
    with pytest.raises(ValueError):
        Line(
            1,
            1,
            "07\/03\/2024",
            {
                "x": 0.4454263367632384,
                "height": 0.022867568134581906,
                "width": 0.08690182470506236,
                "y": 0.9167082878750482,
            },
            {"y": 0.9307722198001792, "x": 0.5323281614683008},
            {"y": 0.9395758560096301, "x": 0.44837726658954413},
            {"x": 0.529377231641995, "y": 0.9167082878750482},
            {"x": 0.4454263367632384, "y": 0.9255119240844992},
            -5.986527,
            "-0.10448461",
            1,
        )


def test_key():
    """Test the Line.key() method"""
    line = Line(
        1,
        1,
        "07\/03\/2024",
        {
            "x": 0.4454263367632384,
            "height": 0.022867568134581906,
            "width": 0.08690182470506236,
            "y": 0.9167082878750482,
        },
        {"y": 0.9307722198001792, "x": 0.5323281614683008},
        {"y": 0.9395758560096301, "x": 0.44837726658954413},
        {"x": 0.529377231641995, "y": 0.9167082878750482},
        {"x": 0.4454263367632384, "y": 0.9255119240844992},
        -5.986527,
        -0.10448461,
        1,
    )
    assert line.key() == {"PK": {"S": "IMAGE#00001"}, "SK": {"S": "LINE#00001"}}


def test_to_item():
    """Test the Line.to_item() method"""
    line = Line(
        1,
        1,
        "07\/03\/2024",
        {
            "x": 0.4454263367632384,
            "height": 0.022867568134581906,
            "width": 0.08690182470506236,
            "y": 0.9167082878750482,
        },
        {"y": 0.9307722198001792, "x": 0.5323281614683008},
        {"y": 0.9395758560096301, "x": 0.44837726658954413},
        {"x": 0.529377231641995, "y": 0.9167082878750482},
        {"x": 0.4454263367632384, "y": 0.9255119240844992},
        -5.986527,
        -0.10448461,
        1,
    )
    assert line.to_item() == {
        "PK": {"S": "IMAGE#00001"},
        "SK": {"S": "LINE#00001"},
        "GSI1PK": {"S": "IMAGE#00001"},
        "GSI1SK": {"S": "IMAGE#00001#LINE#00001"},
        "Type": {"S": "LINE"},
        "Text": {"S": "07\/03\/2024"},
        "BoundingBox": {
            "M": {
                "x": {"N": "0.445426336763238400"},
                "height": {"N": "0.022867568134581906"},
                "width": {"N": "0.086901824705062360"},
                "y": {"N": "0.916708287875048200"},
            }
        },
        "TopRight": {
            "M": {
                "y": {"N": "0.930772219800179200"},
                "x": {"N": "0.532328161468300800"},
            }
        },
        "TopLeft": {
            "M": {
                "y": {"N": "0.939575856009630100"},
                "x": {"N": "0.448377266589544130"},
            }
        },
        "BottomRight": {
            "M": {
                "x": {"N": "0.529377231641995000"},
                "y": {"N": "0.916708287875048200"},
            }
        },
        "BottomLeft": {
            "M": {
                "x": {"N": "0.445426336763238400"},
                "y": {"N": "0.925511924084499200"},
            }
        },
        "AngleDegrees": {"N": "-5.9865270000"},
        "AngleRadians": {"N": "-0.1044846100"},
        "Confidence": {"N": "1.00"},
    }


def create_test_line():
    """
    Helper function to create a Line object with easily verifiable points.
    Adjust coordinates as needed for your tests.
    """
    return Line(
        image_id=1,
        id=1,
        text="Test",
        boundingBox={"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0},
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
        (5, -2),  # Translate right 5, up -2
        (0, 0),  # No translation
        (-3, 10),  # Translate left 3, down 10
    ],
)
def test_translate(dx, dy):
    """
    Test that translate(dx, dy) shifts the corner points correctly
    and does NOT update boundingBox or angles.
    """
    line = create_test_line()

    # Original corners and bounding box
    orig_top_right = line.top_right.copy()
    orig_top_left = line.top_left.copy()
    orig_bottom_right = line.bottom_right.copy()
    orig_bottom_left = line.bottom_left.copy()
    orig_bb = line.boundingBox.copy()  # boundingBox is not updated in translate

    # Translate
    line.translate(dx, dy)

    # Check corners
    assert line.top_right["x"] == pytest.approx(orig_top_right["x"] + dx)
    assert line.top_right["y"] == pytest.approx(orig_top_right["y"] + dy)

    assert line.top_left["x"] == pytest.approx(orig_top_left["x"] + dx)
    assert line.top_left["y"] == pytest.approx(orig_top_left["y"] + dy)

    assert line.bottom_right["x"] == pytest.approx(orig_bottom_right["x"] + dx)
    assert line.bottom_right["y"] == pytest.approx(orig_bottom_right["y"] + dy)

    assert line.bottom_left["x"] == pytest.approx(orig_bottom_left["x"] + dx)
    assert line.bottom_left["y"] == pytest.approx(orig_bottom_left["y"] + dy)

    # Check boundingBox (should not change)
    assert line.boundingBox == orig_bb

    # Angles should not change
    assert line.angle_degrees == 0.0
    assert line.angle_radians == 0.0


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
    Test that scale(sx, sy) scales both the corner points and the boundingBox,
    and does NOT modify angles.
    """
    line = create_test_line()

    # Original corners and bounding box
    orig_top_right = line.top_right.copy()
    orig_top_left = line.top_left.copy()
    orig_bottom_right = line.bottom_right.copy()
    orig_bottom_left = line.bottom_left.copy()
    orig_bb = line.boundingBox.copy()

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

    # Check boundingBox
    assert line.boundingBox["x"] == pytest.approx(orig_bb["x"] * sx)
    assert line.boundingBox["y"] == pytest.approx(orig_bb["y"] * sy)
    assert line.boundingBox["width"] == pytest.approx(orig_bb["width"] * sx)
    assert line.boundingBox["height"] == pytest.approx(orig_bb["height"] * sy)

    # Angles should not change
    assert line.angle_degrees == 0.0
    assert line.angle_radians == 0.0


def create_test_line():
    """A helper function that returns a test Line object."""
    return Line(
        image_id=1,
        id=1,
        text="Test",
        boundingBox={"x": 10.0, "y": 20.0, "width": 5.0, "height": 2.0},
        top_right={"x": 15.0, "y": 20.0},
        top_left={"x": 10.0, "y": 20.0},
        bottom_right={"x": 15.0, "y": 22.0},
        bottom_left={"x": 10.0, "y": 22.0},
        angle_degrees=0.0,
        angle_radians=0.0,
        confidence=1.0,
    )


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
    Test that rotate(angle, origin_x, origin_y, use_radians) only rotates if angle is in
    [-90, 90] degrees or [-π/2, π/2] radians. Otherwise, raises ValueError.
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

        # Corners and angles should remain unchanged after the exception
        assert line.top_right == orig_corners["top_right"]
        assert line.top_left == orig_corners["top_left"]
        assert line.bottom_right == orig_corners["bottom_right"]
        assert line.bottom_left == orig_corners["bottom_left"]
        assert line.angle_degrees == orig_angle_degrees
        assert line.angle_radians == orig_angle_radians

    else:
        # Rotation should succeed without error
        line.rotate(angle, 0, 0, use_radians=use_radians)

        # The bounding box remains unchanged
        assert line.boundingBox["x"] == 10.0
        assert line.boundingBox["y"] == 20.0
        assert line.boundingBox["width"] == 5.0
        assert line.boundingBox["height"] == 2.0

        # Some corners must change unless angle=0
        if angle not in (0, 0.0):
            corners_changed = (
                any(line.top_right[k] != orig_corners["top_right"][k] for k in ["x", "y"])
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
            # angle=0 => no corner change
            assert line.top_right == orig_corners["top_right"]
            assert line.top_left == orig_corners["top_left"]
            assert line.bottom_right == orig_corners["bottom_right"]
            assert line.bottom_left == orig_corners["bottom_left"]

        # Angles should have incremented
        if use_radians:
            # Should have increased angle_radians by `angle`
            assert line.angle_radians == pytest.approx(
                orig_angle_radians + angle, abs=1e-9
            )
            # angle_degrees should be old + angle*(180/π)
            deg_from_radians = angle * 180.0 / math.pi
            assert line.angle_degrees == pytest.approx(
                orig_angle_degrees + deg_from_radians, abs=1e-9
            )
        else:
            # Should have increased angle_degrees by `angle`
            assert line.angle_degrees == pytest.approx(
                orig_angle_degrees + angle, abs=1e-9
            )
            # angle_radians should be old + radians(angle)
            assert line.angle_radians == pytest.approx(
                orig_angle_radians + math.radians(angle), abs=1e-9
            )


def test_repr():
    """Test the Line.__repr__() method"""
    line = Line(
        1,
        1,
        "07\/03\/2024",
        {
            "x": 0.4454263367632384,
            "height": 0.022867568134581906,
            "width": 0.08690182470506236,
            "y": 0.9167082878750482,
        },
        {"y": 0.9307722198001792, "x": 0.5323281614683008},
        {"y": 0.9395758560096301, "x": 0.44837726658954413},
        {"x": 0.529377231641995, "y": 0.9167082878750482},
        {"x": 0.4454263367632384, "y": 0.9255119240844992},
        -5.986527,
        -0.10448461,
        1,
    )
    assert repr(line) == "Line(id=1, text='07\/03\/2024')"


def test_iter():
    """Test the Line.__iter__() method"""
    line = Line(
        1,
        1,
        "07\/03\/2024",
        {
            "x": 0.4454263367632384,
            "height": 0.022867568134581906,
            "width": 0.08690182470506236,
            "y": 0.9167082878750482,
        },
        {"y": 0.9307722198001792, "x": 0.5323281614683008},
        {"y": 0.9395758560096301, "x": 0.44837726658954413},
        {"x": 0.529377231641995, "y": 0.9167082878750482},
        {"x": 0.4454263367632384, "y": 0.9255119240844992},
        -5.986527,
        -0.10448461,
        1,
    )
    assert dict(line) == {
        "image_id": 1,
        "id": 1,
        "text": "07\/03\/2024",
        "boundingBox": {
            "x": 0.4454263367632384,
            "height": 0.022867568134581906,
            "width": 0.08690182470506236,
            "y": 0.9167082878750482,
        },
        "topRight": {"y": 0.9307722198001792, "x": 0.5323281614683008},
        "topLeft": {"y": 0.9395758560096301, "x": 0.44837726658954413},
        "bottomRight": {"x": 0.529377231641995, "y": 0.9167082878750482},
        "bottomLeft": {"x": 0.4454263367632384, "y": 0.9255119240844992},
        "angleDegrees": -5.986527,
        "angleRadians": -0.10448461,
        "confidence": 1,
    }


def test_eq():
    """Test the Line.__eq__() method"""
    line1 = Line(
        1,
        1,
        "07\/03\/2024",
        {
            "x": 0.4454263367632384,
            "height": 0.022867568134581906,
            "width": 0.08690182470506236,
            "y": 0.9167082878750482,
        },
        {"y": 0.9307722198001792, "x": 0.5323281614683008},
        {"y": 0.9395758560096301, "x": 0.44837726658954413},
        {"x": 0.529377231641995, "y": 0.9167082878750482},
        {"x": 0.4454263367632384, "y": 0.9255119240844992},
        -5.986527,
        -0.10448461,
        1,
    )
    line2 = Line(
        1,
        1,
        "07\/03\/2024",
        {
            "x": 0.4454263367632384,
            "height": 0.022867568134581906,
            "width": 0.08690182470506236,
            "y": 0.9167082878750482,
        },
        {"y": 0.9307722198001792, "x": 0.5323281614683008},
        {"y": 0.9395758560096301, "x": 0.44837726658954413},
        {"x": 0.529377231641995, "y": 0.9167082878750482},
        {"x": 0.4454263367632384, "y": 0.9255119240844992},
        -5.986527,
        -0.10448461,
        1,
    )
    assert line1 == line2


def map_to_dict(map):
    """
    Convert a DynamoDB map to a dictionary.
    """
    return {key: float(value["N"]) for key, value in map.items()}


def test_map_to_dict():
    mapped_item = {
        "BoundingBox": {
            "M": {
                "x": {"N": "0.445426336763238400"},
                "height": {"N": "0.022867568134581906"},
                "width": {"N": "0.086901824705062360"},
                "y": {"N": "0.916708287875048200"},
            }
        },
    }
    assert map_to_dict(mapped_item["BoundingBox"]["M"]) == {
        "x": 0.4454263367632384,
        "height": 0.022867568134581906,
        "width": 0.08690182470506236,
        "y": 0.9167082878750482,
    }


def test_itemToLine():
    item = {
        "PK": {"S": "IMAGE#00001"},
        "SK": {"S": "LINE#00001"},
        "Type": {"S": "LINE"},
        "Text": {"S": "07\/03\/2024"},
        "BoundingBox": {
            "M": {
                "x": {"N": "0.445426336763238400"},
                "height": {"N": "0.022867568134581906"},
                "width": {"N": "0.086901824705062360"},
                "y": {"N": "0.916708287875048200"},
            }
        },
        "TopRight": {
            "M": {
                "y": {"N": "0.930772219800179200"},
                "x": {"N": "0.532328161468300800"},
            }
        },
        "TopLeft": {
            "M": {
                "y": {"N": "0.939575856009630100"},
                "x": {"N": "0.448377266589544130"},
            }
        },
        "BottomRight": {
            "M": {
                "x": {"N": "0.529377231641995000"},
                "y": {"N": "0.916708287875048200"},
            }
        },
        "BottomLeft": {
            "M": {
                "x": {"N": "0.445426336763238400"},
                "y": {"N": "0.925511924084499200"},
            }
        },
        "AngleDegrees": {"N": "-5.9865270000"},
        "AngleRadians": {"N": "-0.1044846100"},
        "Confidence": {"N": "1.00"},
    }
    assert Line(
        1,
        1,
        "07\/03\/2024",
        {
            "x": 0.4454263367632384,
            "height": 0.022867568134581906,
            "width": 0.08690182470506236,
            "y": 0.9167082878750482,
        },
        {"y": 0.9307722198001792, "x": 0.5323281614683008},
        {"y": 0.9395758560096301, "x": 0.44837726658954413},
        {"x": 0.529377231641995, "y": 0.9167082878750482},
        {"x": 0.4454263367632384, "y": 0.9255119240844992},
        -5.986527,
        -0.10448461,
        1,
    ) == itemToLine(item)
