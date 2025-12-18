"""Word entity with geometry and character information for DynamoDB."""

# infra/lambda_layer/python/dynamo/entities/word.py
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from receipt_dynamo.entities.base import DynamoDBEntity
from receipt_dynamo.entities.entity_mixins import (
    GeometryHashMixin,
    GeometryMixin,
    GeometryReprMixin,
    GeometrySerializationMixin,
    GeometryValidationMixin,
    GeometryValidationUtilsMixin,
    SerializationMixin,
)
from receipt_dynamo.entities.util import (
    assert_type,
    assert_valid_uuid,
    validate_non_negative_int,
)


@dataclass(eq=True, unsafe_hash=False)
class Word(
    GeometryHashMixin,
    GeometryReprMixin,
    GeometryValidationUtilsMixin,
    SerializationMixin,
    GeometryMixin,
    GeometrySerializationMixin,
    GeometryValidationMixin,
    DynamoDBEntity,
):
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

        # Use validation utils mixin for common validation
        self._validate_common_geometry_entity_fields()

        if self.extracted_data is not None:
            assert_type("extracted_data", self.extracted_data, dict, ValueError)

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
            "GSI2SK": {"S": f"LINE#{self.line_id:05d}#WORD#{self.word_id:05d}"},
        }

    def to_item(self) -> Dict[str, Any]:
        """Converts the Word object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the Word object as a DynamoDB
            item.
        """
        # Use mixin for common geometry fields
        custom_fields = self._get_geometry_fields()

        # Add extracted_data conditionally to avoid type conflicts
        if self.extracted_data:
            custom_fields["extracted_data"] = {
                "M": {
                    "type": {"S": self.extracted_data["type"]},
                    "value": {"S": self.extracted_data["value"]},
                }
            }
        else:
            custom_fields["extracted_data"] = {"NULL": True}

        return self.build_dynamodb_item(
            entity_type="WORD",
            gsi_methods=["gsi2_key"],
            custom_fields=custom_fields,
            exclude_fields={
                "image_id",
                "line_id",
                "word_id",
                "text",
                "bounding_box",
                "top_right",
                "top_left",
                "bottom_right",
                "bottom_left",
                "angle_degrees",
                "angle_radians",
                "confidence",
                "extracted_data",
            },
        )

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

    def _get_geometry_hash_fields(self) -> tuple:
        """Override to include entity-specific ID fields in hash
        computation."""
        geometry_fields = (
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
        return geometry_fields + (
            self.image_id,
            self.line_id,
            self.word_id,
            (tuple(self.extracted_data.items()) if self.extracted_data else None),
        )

    def __hash__(self) -> int:
        """Returns the hash value of the Word object."""
        return hash(self._get_geometry_hash_fields())

    def __repr__(self) -> str:
        """Returns a string representation of the Word object."""
        geometry_fields = self._get_geometry_repr_fields()
        return f"Word(" f"word_id={self.word_id}, " f"{geometry_fields}" f")"


def item_to_word(item: Dict[str, Any]) -> Word:
    """Converts a DynamoDB item to a Word object using EntityFactory.

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

    # Custom SK parser for LINE#{line_id:05d}#WORD#{word_id:05d} pattern
    def parse_word_sk(sk: str) -> Dict[str, Any]:
        """Parse the SK to extract line_id and word_id."""
        parts = sk.split("#")
        if len(parts) < 4 or parts[0] != "LINE" or parts[2] != "WORD":
            raise ValueError(f"Invalid SK format for Word: {sk}")

        return {
            "line_id": int(parts[1]),
            "word_id": int(parts[3]),
        }

    # Import EntityFactory and related functions
    from .entity_factory import (
        EntityFactory,
        create_geometry_extractors,
        create_image_receipt_pk_parser,
    )

    # Type-safe extractors for all fields
    custom_extractors = {
        "text": EntityFactory.extract_text_field,
        **create_geometry_extractors(),  # Handles all geometry fields
    }

    # Handle optional extracted_data field
    if "extracted_data" in item and not item.get("extracted_data", {}).get("NULL"):
        custom_extractors["extracted_data"] = (
            EntityFactory.extract_optional_extracted_data
        )

    # Use EntityFactory to create the entity with full type safety
    try:
        return EntityFactory.create_entity(
            entity_class=Word,
            item=item,
            required_keys=required_keys,
            key_parsers={
                "PK": create_image_receipt_pk_parser(),
                "SK": parse_word_sk,
            },
            custom_extractors=custom_extractors,
        )
    except ValueError as e:
        # Check if it's a missing keys error and re-raise as-is
        if str(e).startswith("Item is missing required keys:"):
            raise
        # Otherwise, wrap the error
        raise ValueError(f"Error converting item to Word: {e}") from e
