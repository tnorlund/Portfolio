from dataclasses import dataclass
from math import sqrt
from typing import Any, Dict

from receipt_dynamo.entities.base import DynamoDBEntity
from receipt_dynamo.entities.geometry_base import GeometryMixin
from receipt_dynamo.entities.util import (
    _format_float,
    _repr_str,
    assert_valid_bounding_box,
    assert_valid_point,
    assert_valid_uuid,
)


@dataclass(eq=True, unsafe_hash=False)
class Line(GeometryMixin, DynamoDBEntity):
    """
    Represents a line and its associated metadata stored in a DynamoDB table.

    This class encapsulates line-related information such as its unique identifier,
    text content, geometric properties, rotation angles, and detection confidence.
    It is designed to support operations such as generating DynamoDB keys and applying
    geometric transformations including translation, scaling, rotation, shear, and affine warping.

    Attributes:
        image_id (str): UUID identifying the image to which the line belongs.
        line_id (int): Identifier for the line.
        text (str): The text content of the line.
        bounding_box (dict): The bounding box of the line with keys 'x', 'y', 'width', and 'height'.
        top_right (dict): The top-right corner coordinates with keys 'x' and 'y'.
        top_left (dict): The top-left corner coordinates with keys 'x' and 'y'.
        bottom_right (dict): The bottom-right corner coordinates with keys 'x' and 'y'.
        bottom_left (dict): The bottom-left corner coordinates with keys 'x' and 'y'.
        angle_degrees (float): The angle of the line in degrees.
        angle_radians (float): The angle of the line in radians.
        confidence (float): The confidence level of the line (between 0 and 1).
        histogram (dict): A histogram representing character frequencies in the text.
        num_chars (int): The number of characters in the line.
    """

    image_id: str
    line_id: int
    text: str
    bounding_box: Dict[str, Any]
    top_right: Dict[str, Any]
    top_left: Dict[str, Any]
    bottom_right: Dict[str, Any]
    bottom_left: Dict[str, Any]
    angle_degrees: float
    angle_radians: float
    confidence: float

    def __post_init__(self) -> None:
        """Validate and normalize initialization arguments."""
        assert_valid_uuid(self.image_id)

        if not isinstance(self.line_id, int):
            raise ValueError("line_id must be an integer")
        if self.line_id <= 0:
            raise ValueError("line_id must be positive")

        if not isinstance(self.text, str):
            raise ValueError("text must be a string")

        assert_valid_bounding_box(self.bounding_box)

        assert_valid_point(self.top_right)

        assert_valid_point(self.top_left)

        assert_valid_point(self.bottom_right)

        assert_valid_point(self.bottom_left)

        if not isinstance(self.angle_degrees, (float, int)):
            raise ValueError("angle_degrees must be a float or int")
        self.angle_degrees = float(self.angle_degrees)

        if not isinstance(self.angle_radians, (float, int)):
            raise ValueError("angle_radians must be a float or int")
        self.angle_radians = float(self.angle_radians)

        if isinstance(self.confidence, int):
            self.confidence = float(self.confidence)
        if not isinstance(self.confidence, float) or not (
            0 < self.confidence <= 1
        ):
            raise ValueError("confidence must be a float between 0 and 1")

    @property
    def key(self) -> Dict[str, Any]:
        """Generates the primary key for the line.

        Returns:
            dict: The primary key for the line.
        """
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {"S": f"LINE#{self.line_id:05d}"},
        }

    @property
    def gsi1_key(self) -> Dict[str, Any]:
        """Generates the GSI1 key for the line.

        Returns:
            dict: The GSI1 key for the line.
        """
        return {
            "GSI1PK": {"S": f"IMAGE#{self.image_id}"},
            "GSI1SK": {"S": f"LINE#{self.line_id:05d}"},
        }

    def to_item(self) -> Dict[str, Any]:
        """Converts the Line object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the Line object as a DynamoDB item.
        """
        return {
            **self.key,
            **self.gsi1_key,
            "TYPE": {"S": "LINE"},
            "text": {"S": self.text},
            "bounding_box": {
                "M": {
                    "x": {"N": _format_float(self.bounding_box["x"], 20, 22)},
                    "y": {"N": _format_float(self.bounding_box["y"], 20, 22)},
                    "width": {
                        "N": _format_float(self.bounding_box["width"], 20, 22)
                    },
                    "height": {
                        "N": _format_float(self.bounding_box["height"], 20, 22)
                    },
                }
            },
            "top_right": {
                "M": {
                    "x": {"N": _format_float(self.top_right["x"], 20, 22)},
                    "y": {"N": _format_float(self.top_right["y"], 20, 22)},
                }
            },
            "top_left": {
                "M": {
                    "x": {"N": _format_float(self.top_left["x"], 20, 22)},
                    "y": {"N": _format_float(self.top_left["y"], 20, 22)},
                }
            },
            "bottom_right": {
                "M": {
                    "x": {"N": _format_float(self.bottom_right["x"], 20, 22)},
                    "y": {"N": _format_float(self.bottom_right["y"], 20, 22)},
                }
            },
            "bottom_left": {
                "M": {
                    "x": {"N": _format_float(self.bottom_left["x"], 20, 22)},
                    "y": {"N": _format_float(self.bottom_left["y"], 20, 22)},
                }
            },
            "angle_degrees": {"N": _format_float(self.angle_degrees, 18, 20)},
            "angle_radians": {"N": _format_float(self.angle_radians, 18, 20)},
            "confidence": {"N": _format_float(self.confidence, 2, 2)},
        }

    def calculate_diagonal_length(self) -> float:
        """Calculates the length of the diagonal of the line.

        Returns:
            float: The length of the diagonal of the line.
        """
        return sqrt(
            (self.top_right["x"] - self.bottom_left["x"]) ** 2
            + (self.top_right["y"] - self.bottom_left["y"]) ** 2
        )

    def __repr__(self) -> str:
        """Returns a string representation of the Line object.

        Returns:
            str: A string representation of the Line object.
        """
        return (
            f"Line("
            f"image_id={_repr_str(self.image_id)}, "
            f"line_id={self.line_id}, "
            f"text={_repr_str(self.text)}, "
            f"bounding_box={self.bounding_box}, "
            f"top_right={self.top_right}, "
            f"top_left={self.top_left}, "
            f"bottom_right={self.bottom_right}, "
            f"bottom_left={self.bottom_left}, "
            f"angle_degrees={self.angle_degrees}, "
            f"angle_radians={self.angle_radians}, "
            f"confidence={self.confidence}"
            f")"
        )

    def __hash__(self) -> int:
        """Returns the hash value of the Line object."""
        return hash(
            (
                self.image_id,
                self.line_id,
                self.text,
                tuple(self.bounding_box.items()),
                tuple(self.top_right.items()),
                tuple(self.top_left.items()),
                tuple(self.bottom_right.items()),
                tuple(self.bottom_left.items()),
                self.angle_degrees,
                self.angle_radians,
                self.confidence,
            )
        )


def item_to_line(item: Dict[str, Any]) -> Line:
    """Converts a DynamoDB item to a Line object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        Line: The Line object represented by the DynamoDB item.

    Raises:
        ValueError: When the item format is invalid.
    """
    required_keys = {
        "PK",
        "SK",
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
    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - set(item.keys())
        raise ValueError(f"Item is missing required keys: {missing_keys}")
    try:
        return Line(
            image_id=item["PK"]["S"][6:],
            line_id=int(item["SK"]["S"][6:]),
            text=item["text"]["S"],
            bounding_box={
                key: float(value["N"])
                for key, value in item["bounding_box"]["M"].items()
            },
            top_right={
                key: float(value["N"])
                for key, value in item["top_right"]["M"].items()
            },
            top_left={
                key: float(value["N"])
                for key, value in item["top_left"]["M"].items()
            },
            bottom_right={
                key: float(value["N"])
                for key, value in item["bottom_right"]["M"].items()
            },
            bottom_left={
                key: float(value["N"])
                for key, value in item["bottom_left"]["M"].items()
            },
            angle_degrees=float(item["angle_degrees"]["N"]),
            angle_radians=float(item["angle_radians"]["N"]),
            confidence=float(item["confidence"]["N"]),
        )
    except KeyError as e:
        raise ValueError(f"Error converting item to Line: {e}") from e
