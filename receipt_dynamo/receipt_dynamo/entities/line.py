from dataclasses import dataclass
from math import sqrt
from typing import Any, Dict

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
    assert_valid_uuid,
)


@dataclass(eq=True, unsafe_hash=False)
class Line(
    GeometryHashMixin,
    GeometryReprMixin,
    GeometryValidationUtilsMixin,
    SerializationMixin,
    GeometryMixin,
    GeometrySerializationMixin,
    GeometryValidationMixin,
    DynamoDBEntity,
):
    """
    Represents a line and its associated metadata stored in a DynamoDB table.

    This class encapsulates line-related information such as its unique
    identifier, text content, geometric properties, rotation angles, and
    detection confidence. It is designed to support operations such as
    generating DynamoDB keys and applying geometric transformations including
    translation, scaling, rotation, shear, and affine warping.

    Attributes:
        image_id (str): UUID identifying the image to which the line belongs.
        line_id (int): Identifier for the line.
        text (str): The text content of the line.
        bounding_box (dict): The bounding box of the line with keys 'x', 'y',
            'width', and 'height'.
        top_right (dict): The top-right corner coordinates with keys 'x' and
            'y'.
        top_left (dict): The top-left corner coordinates with keys 'x' and 'y'.
        bottom_right (dict): The bottom-right corner coordinates with keys
            'x' and 'y'.
        bottom_left (dict): The bottom-left corner coordinates with keys 'x'
            and 'y'.
        angle_degrees (float): The angle of the line in degrees.
        angle_radians (float): The angle of the line in radians.
        confidence (float): The confidence level of the line (between 0 and 1).
        histogram (dict): A histogram representing character frequencies in
            the text.
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

        # Use validation utils mixin for common validation
        self._validate_common_geometry_entity_fields()

        # Note: confidence validation in mixin allows <= 0.0, but Line
        # entities require > 0.0
        if isinstance(self.confidence, int):
            self.confidence = float(self.confidence)
        if not isinstance(self.confidence, float) or not (0 < self.confidence <= 1):
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
        # Use mixin for common geometry fields
        custom_fields = self._get_geometry_fields()

        return self.build_dynamodb_item(
            entity_type="LINE",
            gsi_methods=["gsi1_key"],
            custom_fields=custom_fields,
            exclude_fields={
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
                # Prevent auto-serialization of helper property used for GSI injection
                "gsi1_key",
            },
        )

    def calculate_diagonal_length(self) -> float:
        """Calculates the length of the diagonal of the line.

        Returns:
            float: The length of the diagonal of the line.
        """
        return sqrt(
            (self.top_right["x"] - self.bottom_left["x"]) ** 2
            + (self.top_right["y"] - self.bottom_left["y"]) ** 2
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
        )

    def __hash__(self) -> int:
        """Returns the hash value of the Line object."""
        return hash(self._get_geometry_hash_fields())

    def __repr__(self) -> str:
        """Returns a string representation of the Line object."""
        geometry_fields = self._get_geometry_repr_fields()
        return (
            f"Line("
            f"image_id='{self.image_id}', "
            f"line_id={self.line_id}, "
            f"{geometry_fields}"
            f")"
        )


def item_to_line(item: Dict[str, Any]) -> Line:
    """Convert a DynamoDB item to a Line object using type-safe EntityFactory.

    Args:
        item: The DynamoDB item dictionary to convert.

    Returns:
        A Line object with all fields properly extracted and validated.

    Raises:
        ValueError: If required fields are missing or have invalid format.
    """
    from receipt_dynamo.entities.entity_factory import (
        EntityFactory,
        create_geometry_extractors,
        create_image_receipt_pk_parser,
    )

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

    # Custom SK parser for LINE#{line_id:05d} pattern
    def parse_line_sk(sk: str) -> Dict[str, Any]:
        """Parse the SK to extract line_id."""
        parts = sk.split("#")
        if len(parts) < 2 or parts[0] != "LINE":
            raise ValueError(f"Invalid SK format for Line: {sk}")

        return {"line_id": int(parts[1])}

    # Type-safe extractors for all fields
    custom_extractors = {
        "text": EntityFactory.extract_text_field,
        **create_geometry_extractors(),  # Handles all geometry fields
    }

    # Use EntityFactory to create the entity with full type safety
    try:
        return EntityFactory.create_entity(
            entity_class=Line,
            item=item,
            required_keys=required_keys,
            key_parsers={
                "PK": create_image_receipt_pk_parser(),
                "SK": parse_line_sk,
            },
            custom_extractors=custom_extractors,
        )
    except ValueError as e:
        # Check if it's a missing keys error and re-raise as-is
        if str(e).startswith("Item is missing required keys:"):
            raise
        # Otherwise, wrap the error
        raise ValueError(f"Error converting item to Line: {e}") from e
