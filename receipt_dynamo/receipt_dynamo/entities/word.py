"""Word entity with geometry and character information for DynamoDB."""

from dataclasses import dataclass
from typing import Any, ClassVar

from receipt_dynamo.entities.text_geometry_entity import TextGeometryEntity
from receipt_dynamo.entities.entity_factory import (
    EntityFactory,
    create_geometry_extractors,
    create_image_receipt_pk_parser,
)
from receipt_dynamo.entities.util import (
    assert_type,
    build_base_item,
    validate_non_negative_int,
)


@dataclass(kw_only=True)
class Word(TextGeometryEntity):
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

    # Entity-specific ID fields
    line_id: int
    word_id: int
    extracted_data: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        """Validate and normalize initialization arguments."""
        # Validate entity-specific ID fields
        validate_non_negative_int("line_id", self.line_id)
        validate_non_negative_int("word_id", self.word_id)

        # Use base class geometry validation
        self._validate_geometry()

        if self.extracted_data is not None:
            assert_type(
                "extracted_data", self.extracted_data, dict, ValueError
            )
            # Validate extracted_data schema has required keys
            if self.extracted_data:  # Non-empty dict
                required_keys = {"type", "value"}
                missing_keys = required_keys - set(self.extracted_data.keys())
                if missing_keys:
                    raise ValueError(
                        f"extracted_data missing required keys: {missing_keys}"
                    )

    @property
    def key(self) -> dict[str, Any]:
        """Generates the primary key for the Word.

        Returns:
            dict: The primary key for the Word.
        """
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {"S": f"LINE#{self.line_id:05d}#WORD#{self.word_id:05d}"},
        }

    def gsi2_key(self) -> dict[str, Any]:
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

    def to_item(self) -> dict[str, Any]:
        """Converts the Word object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the Word object as a DynamoDB
            item.
        """
        # Start with base item and geometry fields
        item = {
            **build_base_item(self, "WORD"),
            **self.gsi2_key(),
            **self._get_geometry_fields(),
        }

        # Add extracted_data conditionally (empty dict treated as None)
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
        width: int | None = None,
        height: int | None = None,
        flip_y: bool = False,
    ) -> tuple[float, float]:
        """Calculates the centroid of the Word.

        Args:
            width (int, optional): The width of the image to scale
                coordinates. Defaults to None.
            height (int, optional): The height of the image to scale
                coordinates. Defaults to None.
            flip_y (bool, optional): Whether to flip the y coordinate.
                Defaults to False.

        Returns:
            tuple[float, float]: The (x, y) coordinates of the centroid.

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
        width: int | None = None,
        height: int | None = None,
        flip_y: bool = False,
    ) -> tuple[float, float, float, float]:
        """Calculates the bounding box of the Word.

        Args:
            width (int, optional): The width of the image to scale
                coordinates. Defaults to None.
            height (int, optional): The height of the image to scale
                coordinates. Defaults to None.
            flip_y (bool, optional): Whether to flip the y coordinate.
                Defaults to False.

        Returns:
            tuple[float, float, float, float]: The bounding box of the Word
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
        width: int | None = None,
        height: int | None = None,
        flip_y: bool = False,
    ) -> tuple[
        tuple[float, float],
        tuple[float, float],
        tuple[float, float],
        tuple[float, float],
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
            tuple[
                tuple[float, float],
                tuple[float, float],
                tuple[float, float],
                tuple[float, float],
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

    def _get_geometry_hash_fields(self) -> tuple[Any, ...]:
        """Include entity-specific ID fields in hash computation."""
        return self._get_base_geometry_hash_fields() + (
            self.image_id,
            self.line_id,
            self.word_id,
            (
                tuple(self.extracted_data.items())
                if self.extracted_data
                else None
            ),
        )

    def __hash__(self) -> int:
        """Return hash (required for dataclass with eq=True, frozen=False)."""
        return hash(self._get_geometry_hash_fields())

    def __repr__(self) -> str:
        """Returns a string representation of the Word object."""
        geometry_fields = self._get_geometry_repr_fields()
        return f"Word(" f"word_id={self.word_id}, " f"{geometry_fields}" f")"

    # Use base class required keys (no additional keys needed for Word)
    REQUIRED_KEYS: ClassVar[set[str]] = TextGeometryEntity.BASE_REQUIRED_KEYS

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "Word":
        """Convert a DynamoDB item to a Word object.

        Args:
            item: The DynamoDB item dictionary to convert.

        Returns:
            A Word object with all fields properly extracted and validated.

        Raises:
            ValueError: If required fields are missing or have invalid format.
        """
        # Custom SK parser for LINE#{line_id:05d}#WORD#{word_id:05d} pattern
        def parse_word_sk(sk: str) -> dict[str, Any]:
            """Parse the SK to extract line_id and word_id."""
            parts = sk.split("#")
            if len(parts) < 4 or parts[0] != "LINE" or parts[2] != "WORD":
                raise ValueError(f"Invalid SK format for Word: {sk}")

            return {
                "line_id": int(parts[1]),
                "word_id": int(parts[3]),
            }

        # Type-safe extractors for all fields
        custom_extractors = {
            "text": EntityFactory.extract_text_field,
            **create_geometry_extractors(),  # Handles all geometry fields
        }

        # Handle optional extracted_data field
        if "extracted_data" in item and not item.get("extracted_data", {}).get(
            "NULL"
        ):
            custom_extractors["extracted_data"] = (
                EntityFactory.extract_optional_extracted_data
            )

        # Use EntityFactory to create the entity with full type safety
        try:
            return EntityFactory.create_entity(
                entity_class=cls,
                item=item,
                required_keys=cls.REQUIRED_KEYS,
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


def item_to_word(item: dict[str, Any]) -> Word:
    """Convert a DynamoDB item to a Word object.

    This is a convenience function that delegates to Word.from_item().

    Args:
        item: The DynamoDB item dictionary to convert.

    Returns:
        A Word object with all fields properly extracted and validated.

    Raises:
        ValueError: If required fields are missing or have invalid format.
    """
    return Word.from_item(item)
