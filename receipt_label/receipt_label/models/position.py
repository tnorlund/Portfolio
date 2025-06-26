from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Dict, List, Optional, Union


@dataclass
class Point:
    """
    Represents a point in 2D space.

    This class is used for consistent representation of points
    throughout the receipt labeling system.
    """

    x: float
    y: float

    def to_dict(self) -> Dict[str, float]:
        """Convert to dictionary representation."""
        return {"x": self.x, "y": self.y}

    def to_dynamo(self) -> Dict[str, Dict[str, str]]:
        """Convert to DynamoDB representation."""
        return {"M": {"x": {"N": str(self.x)}, "y": {"N": str(self.y)}}}

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Point":
        """Create a Point from a dictionary."""
        return cls(x=float(data.get("x", 0)), y=float(data.get("y", 0)))

    @classmethod
    def from_dynamo(cls, dynamo_data: Dict[str, Any]) -> "Point":
        """Create a Point from a DynamoDB representation."""
        if not dynamo_data or "M" not in dynamo_data:
            return cls(0, 0)

        m_data = dynamo_data["M"]
        return cls(
            x=float(m_data.get("x", {}).get("N", "0")),
            y=float(m_data.get("y", {}).get("N", "0")),
        )


@dataclass
class BoundingBox:
    """
    Represents a bounding box in 2D space.

    This class provides a consistent representation of bounding boxes
    throughout the receipt labeling system, with methods for conversion
    between different formats (points, dimensions, etc.)
    """

    x: float
    y: float
    width: float
    height: float

    @property
    def top_left(self) -> Point:
        """Top-left corner of the bounding box."""
        return Point(self.x, self.y)

    @property
    def top_right(self) -> Point:
        """Top-right corner of the bounding box."""
        return Point(self.x + self.width, self.y)

    @property
    def bottom_left(self) -> Point:
        """Bottom-left corner of the bounding box."""
        return Point(self.x, self.y + self.height)

    @property
    def bottom_right(self) -> Point:
        """Bottom-right corner of the bounding box."""
        return Point(self.x + self.width, self.y + self.height)

    @property
    def center(self) -> Point:
        """Center point of the bounding box."""
        return Point(self.x + self.width / 2, self.y + self.height / 2)

    def contains_point(
        self, point: Union[Point, Dict[str, float], tuple]
    ) -> bool:
        """
        Check if the bounding box contains a given point.

        Args:
            point: A Point object, dictionary with x/y keys, or a tuple (x, y)

        Returns:
            bool: True if the point is inside the bounding box
        """
        if isinstance(point, Point):
            x, y = point.x, point.y
        elif isinstance(point, dict):
            x, y = point.get("x", 0), point.get("y", 0)
        else:
            x, y = point[0], point[1]

        return (
            self.x <= x <= self.x + self.width
            and self.y <= y <= self.y + self.height
        )

    def to_dict(self) -> Dict[str, float]:
        """Convert to dictionary representation."""
        return {
            "x": self.x,
            "y": self.y,
            "width": self.width,
            "height": self.height,
        }

    def to_dynamo(self) -> Dict[str, Dict[str, str]]:
        """Convert to DynamoDB representation."""
        return {
            "M": {
                "x": {"N": str(self.x)},
                "y": {"N": str(self.y)},
                "width": {"N": str(self.width)},
                "height": {"N": str(self.height)},
            }
        }

    def to_corners_dict(self) -> Dict[str, Dict[str, float]]:
        """Convert to a dictionary with all four corners as Points."""
        return {
            "top_left": self.top_left.to_dict(),
            "top_right": self.top_right.to_dict(),
            "bottom_left": self.bottom_left.to_dict(),
            "bottom_right": self.bottom_right.to_dict(),
        }

    def to_corners_dynamo(self) -> Dict[str, Dict[str, Dict[str, str]]]:
        """Convert to a DynamoDB representation with all four corners."""
        return {
            "top_left": self.top_left.to_dynamo(),
            "top_right": self.top_right.to_dynamo(),
            "bottom_left": self.bottom_left.to_dynamo(),
            "bottom_right": self.bottom_right.to_dynamo(),
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "BoundingBox":
        """Create a BoundingBox from a dictionary."""
        return cls(
            x=float(data.get("x", 0)),
            y=float(data.get("y", 0)),
            width=float(data.get("width", 0)),
            height=float(data.get("height", 0)),
        )

    @classmethod
    def from_dynamo(cls, dynamo_data: Dict[str, Any]) -> "BoundingBox":
        """Create a BoundingBox from a DynamoDB representation."""
        if not dynamo_data or "M" not in dynamo_data:
            return cls(0, 0, 0, 0)

        m_data = dynamo_data["M"]
        return cls(
            x=float(m_data.get("x", {}).get("N", "0")),
            y=float(m_data.get("y", {}).get("N", "0")),
            width=float(m_data.get("width", {}).get("N", "0")),
            height=float(m_data.get("height", {}).get("N", "0")),
        )

    @classmethod
    def from_points(
        cls, top_left: Point, bottom_right: Point
    ) -> "BoundingBox":
        """Create a BoundingBox from top-left and bottom-right points."""
        return cls(
            x=top_left.x,
            y=top_left.y,
            width=bottom_right.x - top_left.x,
            height=bottom_right.y - top_left.y,
        )

    @classmethod
    def from_corners(
        cls,
        top_left: Union[Point, Dict],
        top_right: Union[Point, Dict],
        bottom_left: Union[Point, Dict],
        bottom_right: Union[Point, Dict],
    ) -> "BoundingBox":
        """Create a BoundingBox from four corner points."""
        # Convert dictionary input to Points if needed
        if isinstance(top_left, dict):
            top_left = Point.from_dict(top_left)
        if isinstance(top_right, dict):
            top_right = Point.from_dict(top_right)
        if isinstance(bottom_left, dict):
            bottom_left = Point.from_dict(bottom_left)
        if isinstance(bottom_right, dict):
            bottom_right = Point.from_dict(bottom_right)

        # Calculate min/max x and y values
        min_x = min(top_left.x, top_right.x, bottom_left.x, bottom_right.x)
        min_y = min(top_left.y, top_right.y, bottom_left.y, bottom_right.y)
        max_x = max(top_left.x, top_right.x, bottom_left.x, bottom_right.x)
        max_y = max(top_left.y, top_right.y, bottom_left.y, bottom_right.y)

        return cls(x=min_x, y=min_y, width=max_x - min_x, height=max_y - min_y)

    @classmethod
    def from_corners_dynamo(
        cls,
        top_left: Dict[str, Any] = None,
        top_right: Dict[str, Any] = None,
        bottom_left: Dict[str, Any] = None,
        bottom_right: Dict[str, Any] = None,
    ) -> "BoundingBox":
        """Create a BoundingBox from four corner points in DynamoDB format."""
        # Convert DynamoDB data to Points
        corners = {}
        for name, data in [
            ("top_left", top_left),
            ("top_right", top_right),
            ("bottom_left", bottom_left),
            ("bottom_right", bottom_right),
        ]:
            if data and "M" in data:
                corners[name] = Point.from_dynamo(data)
            else:
                corners[name] = Point(0, 0)

        return cls.from_corners(
            corners["top_left"],
            corners["top_right"],
            corners["bottom_left"],
            corners["bottom_right"],
        )
