import pytest

from receipt_label.models.position import BoundingBox, Point


def test_point_creation():
    """Test Point creation and basic properties."""
    point = Point(10.5, 20.5)
    assert point.x == 10.5
    assert point.y == 20.5


def test_point_to_dict():
    """Test Point serialization to dict."""
    point = Point(10.5, 20.5)
    point_dict = point.to_dict()
    assert point_dict == {"x": 10.5, "y": 20.5}


def test_point_from_dict():
    """Test Point deserialization from dict."""
    point_dict = {"x": 10.5, "y": 20.5}
    point = Point.from_dict(point_dict)
    assert point.x == 10.5
    assert point.y == 20.5


def test_point_to_dynamo():
    """Test Point serialization to DynamoDB format."""
    point = Point(10.5, 20.5)
    dynamo_dict = point.to_dynamo()
    assert dynamo_dict == {"M": {"x": {"N": "10.5"}, "y": {"N": "20.5"}}}


def test_point_from_dynamo():
    """Test Point deserialization from DynamoDB format."""
    dynamo_dict = {"M": {"x": {"N": "10.5"}, "y": {"N": "20.5"}}}
    point = Point.from_dynamo(dynamo_dict)
    assert point.x == 10.5
    assert point.y == 20.5


def test_bounding_box_creation():
    """Test BoundingBox creation and basic properties."""
    bbox = BoundingBox(10, 20, 30, 40)
    assert bbox.x == 10
    assert bbox.y == 20
    assert bbox.width == 30
    assert bbox.height == 40


def test_bounding_box_corners():
    """Test BoundingBox corner calculations."""
    bbox = BoundingBox(10, 20, 30, 40)
    assert bbox.top_left.x == 10
    assert bbox.top_left.y == 20
    assert bbox.top_right.x == 40
    assert bbox.top_right.y == 20
    assert bbox.bottom_left.x == 10
    assert bbox.bottom_left.y == 60
    assert bbox.bottom_right.x == 40
    assert bbox.bottom_right.y == 60


def test_bounding_box_to_dict():
    """Test BoundingBox serialization to dict."""
    bbox = BoundingBox(10, 20, 30, 40)
    bbox_dict = bbox.to_dict()
    assert bbox_dict == {"x": 10, "y": 20, "width": 30, "height": 40}


def test_bounding_box_from_dict():
    """Test BoundingBox deserialization from dict."""
    bbox_dict = {"x": 10, "y": 20, "width": 30, "height": 40}
    bbox = BoundingBox.from_dict(bbox_dict)
    assert bbox.x == 10
    assert bbox.y == 20
    assert bbox.width == 30
    assert bbox.height == 40


def test_bounding_box_to_dynamo():
    """Test BoundingBox serialization to DynamoDB format."""
    bbox = BoundingBox(10, 20, 30, 40)
    dynamo_dict = bbox.to_dynamo()
    assert dynamo_dict == {
        "M": {
            "x": {"N": "10"},
            "y": {"N": "20"},
            "width": {"N": "30"},
            "height": {"N": "40"},
        }
    }


def test_bounding_box_from_dynamo():
    """Test BoundingBox deserialization from DynamoDB format."""
    dynamo_dict = {
        "M": {
            "x": {"N": "10"},
            "y": {"N": "20"},
            "width": {"N": "30"},
            "height": {"N": "40"},
        }
    }
    bbox = BoundingBox.from_dynamo(dynamo_dict)
    assert bbox.x == 10
    assert bbox.y == 20
    assert bbox.width == 30
    assert bbox.height == 40


def test_bounding_box_contains_point():
    """Test the BoundingBox contains_point method."""
    bbox = BoundingBox(10, 20, 30, 40)

    # Point inside
    assert bbox.contains_point(Point(15, 25))

    # Points on edges
    assert bbox.contains_point(Point(10, 20))  # Top-left
    assert bbox.contains_point(Point(40, 20))  # Top-right
    assert bbox.contains_point(Point(10, 60))  # Bottom-left
    assert bbox.contains_point(Point(40, 60))  # Bottom-right

    # Points outside
    assert not bbox.contains_point(Point(5, 25))  # Left
    assert not bbox.contains_point(Point(45, 25))  # Right
    assert not bbox.contains_point(Point(15, 15))  # Above
    assert not bbox.contains_point(Point(15, 65))  # Below


def test_bounding_box_from_points():
    """Test creating BoundingBox from two points."""
    top_left = Point(10, 20)
    bottom_right = Point(40, 60)

    bbox = BoundingBox.from_points(top_left, bottom_right)

    assert bbox.x == 10
    assert bbox.y == 20
    assert bbox.width == 30
    assert bbox.height == 40


def test_bounding_box_from_corners():
    """Test creating BoundingBox from four corners."""
    top_left = Point(10, 20)
    top_right = Point(40, 20)
    bottom_left = Point(10, 60)
    bottom_right = Point(40, 60)

    bbox = BoundingBox.from_corners(
        top_left, top_right, bottom_left, bottom_right
    )

    assert bbox.x == 10
    assert bbox.y == 20
    assert bbox.width == 30
    assert bbox.height == 40


def test_bounding_box_from_corners_dicts():
    """Test creating BoundingBox from four corner dictionaries."""
    top_left = {"x": 10, "y": 20}
    top_right = {"x": 40, "y": 20}
    bottom_left = {"x": 10, "y": 60}
    bottom_right = {"x": 40, "y": 60}

    bbox = BoundingBox.from_corners(
        top_left, top_right, bottom_left, bottom_right
    )

    assert bbox.x == 10
    assert bbox.y == 20
    assert bbox.width == 30
    assert bbox.height == 40


def test_bounding_box_from_corners_dynamo():
    """Test creating BoundingBox from four corner DynamoDB dicts."""
    top_left = {"M": {"x": {"N": "10"}, "y": {"N": "20"}}}
    top_right = {"M": {"x": {"N": "40"}, "y": {"N": "20"}}}
    bottom_left = {"M": {"x": {"N": "10"}, "y": {"N": "60"}}}
    bottom_right = {"M": {"x": {"N": "40"}, "y": {"N": "60"}}}

    bbox = BoundingBox.from_corners_dynamo(
        top_left, top_right, bottom_left, bottom_right
    )

    assert bbox.x == 10
    assert bbox.y == 20
    assert bbox.width == 30
    assert bbox.height == 40
