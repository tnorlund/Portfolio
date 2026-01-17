from dataclasses import dataclass
from math import sqrt
from typing import Any, Dict, Tuple

from receipt_dynamo.entities.text_geometry_entity import TextGeometryEntity
from receipt_dynamo.entities.entity_factory import (
    EntityFactory,
    create_geometry_extractors,
    create_image_receipt_pk_parser,
)
from receipt_dynamo.entities.util import (
    build_base_item,
)


@dataclass(kw_only=True)
class Line(TextGeometryEntity):
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

    # Entity-specific ID fields
    line_id: int

    def __post_init__(self) -> None:
        """Validate and normalize initialization arguments."""
        # Validate entity-specific ID fields
        if not isinstance(self.line_id, int):
            raise ValueError("line_id must be an integer")
        if self.line_id <= 0:
            raise ValueError("line_id must be positive")

        # Use base class geometry validation
        self._validate_geometry()

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
        return {
            **build_base_item(self, "LINE"),
            **self.gsi1_key(),
            **self._get_geometry_fields(),
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

    def _get_geometry_hash_fields(self) -> Tuple[Any, ...]:
        """Override to include entity-specific ID fields in hash computation."""
        return self._get_base_geometry_hash_fields() + (
            self.image_id,
            self.line_id,
        )

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


# Re-enable __hash__ after dataclass sets it to None
Line.__hash__ = lambda self: hash(self._get_geometry_hash_fields())  # type: ignore


def item_to_line(item: Dict[str, Any]) -> Line:
    """Convert a DynamoDB item to a Line object using type-safe EntityFactory.

    Args:
        item: The DynamoDB item dictionary to convert.

    Returns:
        A Line object with all fields properly extracted and validated.

    Raises:
        ValueError: If required fields are missing or have invalid format.
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
