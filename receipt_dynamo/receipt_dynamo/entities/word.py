"""Word entity with geometry and character information for DynamoDB."""

# infra/lambda_layer/python/dynamo/entities/word.py
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from receipt_dynamo.entities.base import DynamoDBEntity
from receipt_dynamo.entities.geometry_base import GeometryMixin
from receipt_dynamo.entities.util import (
    _format_float,
    _repr_str,
    assert_type,
    assert_valid_bounding_box,
    assert_valid_point,
    assert_valid_uuid,
    deserialize_bounding_box,
    deserialize_confidence,
    deserialize_coordinate_point,
    serialize_bounding_box,
    serialize_confidence,
    serialize_coordinate_point,
    validate_confidence_range,
    validate_non_negative_int,
)


@dataclass(eq=True, unsafe_hash=False)
class Word(GeometryMixin, DynamoDBEntity):
    """Represents a word extracted from an image for DynamoDB.

    This class encapsulates word-related information such as its unique
    identifiers, text content, geometric properties (bounding box and
    corner coordinates), rotation angles, detection confidence,
    character histogram, and character count. It supports operations such
    as generating DynamoDB keys and applying geometric transformations
    including translation, scaling, rotation, shear, and affine warping.

    Attributes:
        image_id (str): UUID identifying the image.
        line_id (int): Identifier for the line containing the word.
        word_id (int): Identifier for the word.
        text (str): The text of the word.
        bounding_box (dict): The bounding box of the word with keys 'x',
            'y', 'width', and 'height'.
        top_right (dict): The top-right corner coordinates with keys 'x'
            and 'y'.
        top_left (dict): The top-left corner coordinates with keys 'x' and
            'y'.
        bottom_right (dict): The bottom-right corner coordinates with
            keys 'x' and 'y'.
        bottom_left (dict): The bottom-left corner coordinates with keys
            'x' and 'y'.
        angle_degrees (float): The angle of the word in degrees.
        angle_radians (float): The angle of the word in radians.
        confidence (float): The confidence level of the word
            (between 0 and 1).
        histogram (dict): A histogram representing character frequencies in
            the word.
        num_chars (int): The number of characters in the word.
    """

    image_id: str
    line_id: int
    word_id: int
    text: str
    bounding_box: Dict[str, Any]
    top_right: Dict[str, Any]
    top_left: Dict[str, Any]
    bottom_right: Dict[str, Any]
    bottom_left: Dict[str, Any]
    angle_degrees: float
    angle_radians: float
    confidence: float
    extracted_data: Optional[Dict[str, Any]] = None

    def __post_init__(self) -> None:
        """Validate and normalize initialization arguments."""
        assert_valid_uuid(self.image_id)

        validate_non_negative_int("line_id", self.line_id)
        validate_non_negative_int("word_id", self.word_id)

        assert_type("text", self.text, str, ValueError)

        assert_valid_bounding_box(self.bounding_box)
        assert_valid_point(self.top_right)
        assert_valid_point(self.top_left)
        assert_valid_point(self.bottom_right)
        assert_valid_point(self.bottom_left)

        assert_type(
            "angle_degrees", self.angle_degrees, (float, int), ValueError
        )
        assert_type(
            "angle_radians", self.angle_radians, (float, int), ValueError
        )

        self.confidence = validate_confidence_range(
            "confidence", self.confidence
        )

        if self.extracted_data is not None:
            assert_type(
                "extracted_data", self.extracted_data, dict, ValueError
            )

    @property
    def key(self) -> Dict[str, Any]:
        """Generates the primary key for the Word.

        Returns:
            dict: The primary key for the Word.
        """
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {"S": f"LINE#{self.line_id:05d}#WORD#{self.word_id:05d}"},
        }

    def gsi2_key(self) -> Dict[str, Any]:
        """Generates the GSI2 key for the Word.

        Returns:
            dict: The GSI2 key for the Word.
        """
        return {
            "GSI2PK": {"S": f"IMAGE#{self.image_id}"},
            "GSI2SK": {
                "S": f"LINE#{self.line_id:05d}#WORD#{self.word_id:05d}"
            },
        }

    def to_item(self) -> Dict[str, Any]:
        """Converts the Word object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the Word object as a DynamoDB
            item.
        """
        item: Dict[str, Any] = {
            **self.key,
            **self.gsi2_key(),
            "TYPE": {"S": "WORD"},
            "text": {"S": self.text},
            "bounding_box": serialize_bounding_box(self.bounding_box),
            "top_right": serialize_coordinate_point(self.top_right),
            "top_left": serialize_coordinate_point(self.top_left),
            "bottom_right": serialize_coordinate_point(self.bottom_right),
            "bottom_left": serialize_coordinate_point(self.bottom_left),
            "angle_degrees": {
                "N": _format_float(
                    self.angle_degrees,
                    18,
                    20,
                )
            },
            "angle_radians": {
                "N": _format_float(
                    self.angle_radians,
                    18,
                    20,
                )
            },
            "confidence": serialize_confidence(self.confidence),
        }

        # Add extracted_data conditionally to avoid type conflicts
        if self.extracted_data:
            item["extracted_data"] = {
                "M": {
                    "type": {"S": self.extracted_data["type"]},
                    "value": {"S": self.extracted_data["value"]},
                }
            }
        else:
            item["extracted_data"] = {"NULL": True}

        return item

    def calculate_centroid(
        self,
        width: Optional[int] = None,
        height: Optional[int] = None,
        flip_y: bool = False,
    ) -> Tuple[float, float]:
        """Calculates the centroid of the Word.

        Args:
            width (int, optional): The width of the image to scale
                coordinates. Defaults to None.
            height (int, optional): The height of the image to scale
                coordinates. Defaults to None.
            flip_y (bool, optional): Whether to flip the y coordinate.
                Defaults to False.

        Returns:
            Tuple[float, float]: The (x, y) coordinates of the centroid.

        Raises:
            ValueError: If only one of width or height is provided.
        """
        if (width is None) != (height is None):
            raise ValueError(
                "Both width and height must be provided together",
            )

        x, y = super().calculate_centroid()

        if width is not None and height is not None:
            x *= width
            y *= height
            if flip_y:
                y = height - y

        return x, y

    def calculate_bounding_box(
        self,
        width: Optional[int] = None,
        height: Optional[int] = None,
        flip_y: bool = False,
    ) -> Tuple[float, float, float, float]:
        """Calculates the bounding box of the Word.

        Args:
            width (int, optional): The width of the image to scale
                coordinates. Defaults to None.
            height (int, optional): The height of the image to scale
                coordinates. Defaults to None.
            flip_y (bool, optional): Whether to flip the y coordinate.
                Defaults to False.

        Returns:
            Tuple[float, float, float, float]: The bounding box of the Word
                with keys 'x', 'y', 'width', and 'height'.

        Raises:
            ValueError: If only one of width or height is provided.
        """
        if (width is None) != (height is None):
            raise ValueError(
                "Both width and height must be provided together",
            )

        x = self.bounding_box["x"]
        y = self.bounding_box["y"]
        w = self.bounding_box["width"]
        h = self.bounding_box["height"]

        if width is not None and height is not None:
            x *= width
            y *= height
            if flip_y:
                y = height - y

        return x, y, w, h

    def calculate_corners(
        self,
        width: Optional[int] = None,
        height: Optional[int] = None,
        flip_y: bool = False,
    ) -> Tuple[
        Tuple[float, float],
        Tuple[float, float],
        Tuple[float, float],
        Tuple[float, float],
    ]:
        """Calculates the top-left and top-right, and the bottom-left and
        bottom-right corners of the Word in image coordinates.

        Args:
            width (int, optional): The width of the image to scale
                coordinates. Defaults to None.
            height (int, optional): The height of the image to scale
                coordinates. Defaults to None.
            flip_y (bool, optional): Whether to flip the y coordinate.
                Defaults to False.

        Returns:
            Tuple[
                Tuple[float, float],
                Tuple[float, float],
                Tuple[float, float],
                Tuple[float, float],
            ]: The corners of the Word.

        Raises:
            ValueError: If only one of width or height is provided.
        """
        if (width is None) != (height is None):
            raise ValueError(
                "Both width and height must be provided together",
            )

        if width is not None and height is not None:
            x_scale: float = float(width)
            y_scale: float = float(height)
        else:
            x_scale = y_scale = 1.0

        top_left_x = self.top_left["x"] * x_scale
        top_right_x = self.top_right["x"] * x_scale
        bottom_left_x = self.bottom_left["x"] * x_scale
        bottom_right_x = self.bottom_right["x"] * x_scale

        if flip_y:
            top_left_y = height - (self.top_left["y"] * y_scale)
            top_right_y = height - (self.top_right["y"] * y_scale)
            bottom_left_y = height - (self.bottom_left["y"] * y_scale)
            bottom_right_y = height - (self.bottom_right["y"] * y_scale)
        else:
            top_left_y = self.top_left["y"] * y_scale
            top_right_y = self.top_right["y"] * y_scale
            bottom_left_y = self.bottom_left["y"] * y_scale
            bottom_right_y = self.bottom_right["y"] * y_scale

        return (
            (top_left_x, top_left_y),
            (top_right_x, top_right_y),
            (bottom_left_x, bottom_left_y),
            (bottom_right_x, bottom_right_y),
        )

    def __repr__(self):
        """Returns a string representation of the Word object.

        Returns:
            str: The string representation of the Word object.
        """
        return (
            f"Word("
            f"word_id={self.word_id}, "
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
        """Returns the hash value of the Word object."""
        return hash(
            (
                self.image_id,
                self.line_id,
                self.word_id,
                self.text,
                tuple(self.bounding_box.items()),
                tuple(self.top_right.items()),
                tuple(self.top_left.items()),
                tuple(self.bottom_right.items()),
                tuple(self.bottom_left.items()),
                self.angle_degrees,
                self.angle_radians,
                self.confidence,
                (
                    tuple(self.extracted_data.items())
                    if self.extracted_data
                    else None
                ),
            )
        )


def item_to_word(item: Dict[str, Any]) -> Word:
    """Converts a DynamoDB item to a Word object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        Word: The Word object represented by the DynamoDB item.

    Raises:
        ValueError: When the item is missing required keys or has malformed
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
        return Word(
            image_id=item["PK"]["S"][6:],
            line_id=int(item["SK"]["S"].split("#")[1]),
            word_id=int(item["SK"]["S"].split("#")[3]),
            text=item["text"]["S"],
            bounding_box=deserialize_bounding_box(item["bounding_box"]),
            top_right=deserialize_coordinate_point(item["top_right"]),
            top_left=deserialize_coordinate_point(item["top_left"]),
            bottom_right=deserialize_coordinate_point(item["bottom_right"]),
            bottom_left=deserialize_coordinate_point(item["bottom_left"]),
            angle_degrees=float(item["angle_degrees"]["N"]),
            angle_radians=float(item["angle_radians"]["N"]),
            confidence=deserialize_confidence(item["confidence"]),
            extracted_data=(
                None
                if "NULL" in item.get("extracted_data", {})
                else {
                    "type": item.get("extracted_data", {})
                    .get("M", {})
                    .get("type", {})
                    .get("S"),
                    "value": item.get("extracted_data", {})
                    .get("M", {})
                    .get("value", {})
                    .get("S"),
                }
            ),
        )
    except (KeyError, ValueError) as e:
        raise ValueError(f"Error converting item to Word: {e}") from e
