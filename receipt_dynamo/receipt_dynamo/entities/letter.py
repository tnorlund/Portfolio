from dataclasses import dataclass
from typing import Any, Dict, Generator, Tuple

from receipt_dynamo.entities.geometry_base import GeometryMixin
from receipt_dynamo.entities.util import (
    _format_float,
    assert_valid_bounding_box,
    assert_valid_point,
    assert_valid_uuid,
)


@dataclass(eq=True, unsafe_hash=False)
class Letter(GeometryMixin):
    """Represents a single letter extracted from an image for DynamoDB.

    This class encapsulates letter-related information such as its unique
    identifiers, text content, geometric properties (bounding box and corner
    coordinates), rotation angles, and detection confidence. It supports
    operations such as generating DynamoDB keys and applying geometric
    transformations including translation, scaling, rotation, shear, and
    affine warping.

    Attributes:
        image_id (str): UUID identifying the image.
        line_id (int): Identifier for the line containing the letter.
        word_id (int): Identifier for the word containing the letter.
        letter_id (int): Identifier for the letter.
        text (str): The text of the letter (must be exactly one character).
        bounding_box (dict): The bounding box of the letter with keys 'x', 'y',
            'width', and 'height'.
        top_right (dict): The top-right corner coordinates with keys 'x' and
            'y'.
        top_left (dict): The top-left corner coordinates with keys 'x' and 'y'.
        bottom_right (dict): The bottom-right corner coordinates with keys 'x'
            and 'y'.
        bottom_left (dict): The bottom-left corner coordinates with keys 'x'
            and 'y'.
        angle_degrees (float): The angle of the letter in degrees.
        angle_radians (float): The angle of the letter in radians.
        confidence (float): The confidence level of the letter (between 0 and
            1).
    """

    image_id: str
    line_id: int
    word_id: int
    letter_id: int
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

        if not isinstance(self.word_id, int):
            raise ValueError("word_id must be an integer")
        if self.word_id <= 0:
            raise ValueError("word_id must be positive")

        if not isinstance(self.letter_id, int):
            raise ValueError("id must be an integer")
        if self.letter_id <= 0:
            raise ValueError("id must be positive")

        if not isinstance(self.text, str):
            raise ValueError("text must be a string")
        if len(self.text) != 1:
            raise ValueError("text must be exactly one character")

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
        if not isinstance(self.confidence, float):
            raise ValueError("confidence must be a float")
        if self.confidence <= 0.0 or self.confidence > 1.0:
            raise ValueError("confidence must be between 0 and 1")

    @property
    def key(self) -> Dict[str, Any]:
        """Generates the primary key for the Letter.

        Returns:
            dict: A dictionary containing the primary key for the Letter.
        """
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {
                "S": f"LINE#{self.line_id:05d}"
                f"#WORD#{self.word_id:05d}"
                f"#LETTER#{self.letter_id:05d}"
            },
        }

    def to_item(self) -> Dict[str, Any]:
        """Converts the Letter object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the Letter object as a DynamoDB
                item.
        """
        return {
            **self.key,
            "TYPE": {"S": "LETTER"},
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

    def __repr__(self) -> str:
        """Returns a string representation of the Letter object.

        Returns:
            str: The string representation of the Letter object.
        """
        return (
            f"Letter("
            f"letter_id={self.letter_id}, "
            f"text='{self.text}', "
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

    def __iter__(self) -> Generator[Tuple[str, Any], None, None]:
        """Returns an iterator over the Letter object's attributes.

        Yields:
            Tuple[str, Any]: A tuple containing the attribute name and its
                value.
        """
        yield "image_id", self.image_id
        yield "word_id", self.word_id
        yield "line_id", self.line_id
        yield "letter_id", self.letter_id
        yield "text", self.text
        yield "bounding_box", self.bounding_box
        yield "top_right", self.top_right
        yield "top_left", self.top_left
        yield "bottom_right", self.bottom_right
        yield "bottom_left", self.bottom_left
        yield "angle_degrees", self.angle_degrees
        yield "angle_radians", self.angle_radians
        yield "confidence", self.confidence

    def to_dict(self) -> Dict[str, Any]:
        """Returns a dictionary representation of the Letter object."""
        return {
            "image_id": self.image_id,
            "line_id": self.line_id,
            "word_id": self.word_id,
            "letter_id": self.letter_id,
            "text": self.text,
            "bounding_box": self.bounding_box,
            "top_right": self.top_right,
            "top_left": self.top_left,
            "bottom_right": self.bottom_right,
            "bottom_left": self.bottom_left,
            "angle_degrees": self.angle_degrees,
            "angle_radians": self.angle_radians,
            "confidence": self.confidence,
        }

    def __eq__(self, other: object) -> bool:
        """Determines whether two Letter objects are equal.

        Args:
            other (object): The object to compare.

        Returns:
            bool: True if the Letter objects have the same attributes, False
                otherwise.
        """
        if not isinstance(other, Letter):
            return False
        return (
            self.image_id == other.image_id
            and self.line_id == other.line_id
            and self.word_id == other.word_id
            and self.letter_id == other.letter_id
            and self.text == other.text
            and self.bounding_box == other.bounding_box
            and self.top_right == other.top_right
            and self.top_left == other.top_left
            and self.bottom_right == other.bottom_right
            and self.bottom_left == other.bottom_left
            and self.angle_degrees == other.angle_degrees
            and self.angle_radians == other.angle_radians
            and self.confidence == other.confidence
        )

    def __hash__(self) -> int:
        """Returns the hash value of the Letter object.

        Returns:
            int: The hash value of the Letter object.
        """
        return hash(
            (
                self.image_id,
                self.line_id,
                self.word_id,
                self.letter_id,
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


def item_to_letter(item: Dict[str, Any]) -> Letter:
    """Converts a DynamoDB item to a Letter object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        Letter: The Letter object represented by the DynamoDB item.

    Raises:
        ValueError: If the item is missing required keys or has malformed
            fields.
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
        return Letter(
            image_id=item["PK"]["S"][6:],  # strip off "IMAGE#"
            letter_id=int(item["SK"]["S"].split("#")[5]),
            line_id=int(item["SK"]["S"].split("#")[1]),
            word_id=int(item["SK"]["S"].split("#")[3]),
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
    except (KeyError, ValueError) as e:
        raise ValueError(f"Error converting item to Letter: {e}")
