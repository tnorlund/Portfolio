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
    assert line.topRight == {
        "y": 0.9307722198001792,
        "x": 0.5323281614683008,
    }, "topRight"
    assert line.topLeft == {
        "y": 0.9395758560096301,
        "x": 0.44837726658954413,
    }, "topLeft"
    assert line.bottomRight == {
        "x": 0.529377231641995,
        "y": 0.9167082878750482,
    }, "bottomRight"
    assert line.bottomLeft == {
        "x": 0.4454263367632384,
        "y": 0.9255119240844992,
    }, "bottomLeft"
    assert line.angleDegrees == -5.986527
    assert line.angleRadians == -0.10448461
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

    # Test bad TopRight
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

    # Test bad TopLeft
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

    # Test bad BottomRight
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

    # Test bad BottomLeft
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

    # Test bad AngleDegrees
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

    # Test bad AngleRadians
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
        topRight={"x": 15.0, "y": 20.0},
        topLeft={"x": 10.0, "y": 20.0},
        bottomRight={"x": 15.0, "y": 22.0},
        bottomLeft={"x": 10.0, "y": 22.0},
        angleDegrees=0.0,
        angleRadians=0.0,
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
    orig_top_right = line.topRight.copy()
    orig_top_left = line.topLeft.copy()
    orig_bottom_right = line.bottomRight.copy()
    orig_bottom_left = line.bottomLeft.copy()
    orig_bb = line.boundingBox.copy()  # boundingBox is not updated in translate

    # Translate
    line.translate(dx, dy)

    # Check corners
    assert line.topRight["x"] == pytest.approx(orig_top_right["x"] + dx)
    assert line.topRight["y"] == pytest.approx(orig_top_right["y"] + dy)

    assert line.topLeft["x"] == pytest.approx(orig_top_left["x"] + dx)
    assert line.topLeft["y"] == pytest.approx(orig_top_left["y"] + dy)

    assert line.bottomRight["x"] == pytest.approx(orig_bottom_right["x"] + dx)
    assert line.bottomRight["y"] == pytest.approx(orig_bottom_right["y"] + dy)

    assert line.bottomLeft["x"] == pytest.approx(orig_bottom_left["x"] + dx)
    assert line.bottomLeft["y"] == pytest.approx(orig_bottom_left["y"] + dy)

    # Check boundingBox (should not change)
    assert line.boundingBox == orig_bb

    # Angles should not change
    assert line.angleDegrees == 0.0
    assert line.angleRadians == 0.0


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
    orig_top_right = line.topRight.copy()
    orig_top_left = line.topLeft.copy()
    orig_bottom_right = line.bottomRight.copy()
    orig_bottom_left = line.bottomLeft.copy()
    orig_bb = line.boundingBox.copy()

    line.scale(sx, sy)

    # Check corners
    assert line.topRight["x"] == pytest.approx(orig_top_right["x"] * sx)
    assert line.topRight["y"] == pytest.approx(orig_top_right["y"] * sy)

    assert line.topLeft["x"] == pytest.approx(orig_top_left["x"] * sx)
    assert line.topLeft["y"] == pytest.approx(orig_top_left["y"] * sy)

    assert line.bottomRight["x"] == pytest.approx(orig_bottom_right["x"] * sx)
    assert line.bottomRight["y"] == pytest.approx(orig_bottom_right["y"] * sy)

    assert line.bottomLeft["x"] == pytest.approx(orig_bottom_left["x"] * sx)
    assert line.bottomLeft["y"] == pytest.approx(orig_bottom_left["y"] * sy)

    # Check boundingBox
    assert line.boundingBox["x"] == pytest.approx(orig_bb["x"] * sx)
    assert line.boundingBox["y"] == pytest.approx(orig_bb["y"] * sy)
    assert line.boundingBox["width"] == pytest.approx(orig_bb["width"] * sx)
    assert line.boundingBox["height"] == pytest.approx(orig_bb["height"] * sy)

    # Angles should not change
    assert line.angleDegrees == 0.0
    assert line.angleRadians == 0.0


@pytest.mark.parametrize(
    "angle, origin, use_radians",
    [
        (90, (0, 0), False),  # Rotate 90° about origin in degrees
        (math.pi / 2, (0, 0), True),  # Same rotation in radians
        (180, (10, 20), False),  # Rotate 180° about (10, 20)
    ],
)
def test_rotate(angle, origin, use_radians):
    """
    Test that rotate(angle, origin_x, origin_y) rotates the corners as expected
    and updates angleDegrees / angleRadians. boundingBox should remain unchanged.
    """
    line = create_test_line()

    # Keep a copy of original corners
    orig_corners = {
        "topRight": line.topRight.copy(),
        "topLeft": line.topLeft.copy(),
        "bottomRight": line.bottomRight.copy(),
        "bottomLeft": line.bottomLeft.copy(),
    }
    orig_bb = line.boundingBox.copy()

    # Perform rotation
    line.rotate(angle, origin[0], origin[1], use_radians=use_radians)

    # boundingBox should remain unchanged
    assert line.boundingBox == orig_bb

    # Check angles. The final angle should be original + `angle`.
    # Since original angle is 0, the final angle should be exactly `angle`.
    # But recall that in rotate, if `use_radians=False`, we convert `angle` to radians internally.
    # So we'll compare line.angleRadians vs the “converted” total, and line.angleDegrees vs the same “converted” total.
    if use_radians:
        # If we used radians, angle is already in radians
        expected_angle_radians = angle % (2 * math.pi)
        expected_angle_degrees = (expected_angle_radians * 180.0 / math.pi) % 360
    else:
        # If we used degrees
        expected_angle_degrees = angle % 360
        expected_angle_radians = math.radians(expected_angle_degrees)

    assert line.angleRadians == pytest.approx(expected_angle_radians, abs=1e-9)
    assert line.angleDegrees == pytest.approx(expected_angle_degrees, abs=1e-9)

    # For the actual corner positions, you could do more detailed checks:
    #
    # Example: If rotating 90 degrees around (0,0), you can compare each point
    # to its expected new position. If rotating 180 degrees around (10,20),
    # you can check that each point is essentially mirrored about that origin.
    #
    # This can be more involved, so here we simply ensure the corner coordinates
    # have changed (unless angle=0). If you want exact checks, compute the
    # rotation manually for each point and compare to the line’s corners.

    if angle not in (0, 360, 2 * math.pi, -360):
        # At least one corner coordinate should differ if a real rotation took place
        # (or all corners if 180°, etc.). This is a minimal sanity check.
        corners_changed = (
            any(line.topRight[k] != orig_corners["topRight"][k] for k in ["x", "y"])
            or any(line.topLeft[k] != orig_corners["topLeft"][k] for k in ["x", "y"])
            or any(
                line.bottomRight[k] != orig_corners["bottomRight"][k]
                for k in ["x", "y"]
            )
            or any(
                line.bottomLeft[k] != orig_corners["bottomLeft"][k] for k in ["x", "y"]
            )
        )
        assert corners_changed, "Expected corners to change after rotation."
    else:
        # If angle is effectively 0, corners should not have changed
        assert line.topRight == orig_corners["topRight"]
        assert line.topLeft == orig_corners["topLeft"]
        assert line.bottomRight == orig_corners["bottomRight"]
        assert line.bottomLeft == orig_corners["bottomLeft"]


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
