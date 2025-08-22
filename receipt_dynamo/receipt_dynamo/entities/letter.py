from dataclasses import dataclass
from typing import Any, Dict, Generator, Tuple

from receipt_dynamo.entities.entity_mixins import (
    GeometryMixin,
    GeometrySerializationMixin,
    GeometryValidationMixin,
)
from receipt_dynamo.entities.util import (
    assert_valid_uuid,
    build_base_item,
    validate_positive_int,
)


@dataclass(eq=True, unsafe_hash=False)
class Letter(
    GeometryMixin, GeometrySerializationMixin, GeometryValidationMixin
):
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

        validate_positive_int("line_id", self.line_id)
        validate_positive_int("word_id", self.word_id)
        validate_positive_int("letter_id", self.letter_id)

        if not isinstance(self.text, str):
            raise ValueError("text must be a string")
        if len(self.text) != 1:
            raise ValueError("text must be exactly one character")

        # Use mixin for common geometry validation
        self._validate_geometry_fields()

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
            **build_base_item(self, "LETTER"),
            **self._get_geometry_fields(),
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
    """Convert a DynamoDB item to a Letter object using type-safe EntityFactory.

    Args:
        item: The DynamoDB item dictionary to convert.

    Returns:
        A Letter object with all fields properly extracted and validated.

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

    # Custom SK parser for LINE#...#WORD#...#LETTER#{letter_id:05d} pattern
    def parse_letter_sk(sk: str) -> Dict[str, Any]:
        """Parse the SK to extract line_id, word_id, and letter_id."""
        parts = sk.split("#")

        # Expected format: LINE#{line_id}#WORD#{word_id}#LETTER#{letter_id}
        if (
            len(parts) < 6
            or parts[0] != "LINE"
            or parts[2] != "WORD"
            or parts[4] != "LETTER"
        ):
            raise ValueError(f"Invalid SK format for Letter: {sk}")

        return {
            "line_id": int(parts[1]),
            "word_id": int(parts[3]),
            "letter_id": int(parts[5]),
        }

    # Type-safe extractors for all fields
    custom_extractors = {
        "text": EntityFactory.extract_text_field,
        **create_geometry_extractors(),  # Handles all geometry fields
    }

    # Use EntityFactory to create the entity with full type safety
    try:
        return EntityFactory.create_entity(
            entity_class=Letter,
            item=item,
            required_keys=required_keys,
            key_parsers={
                "PK": create_image_receipt_pk_parser(),
                "SK": parse_letter_sk,
            },
            custom_extractors=custom_extractors,
        )
    except ValueError as e:
        # Check if it's a missing keys error and re-raise as-is
        if str(e).startswith("Item is missing required keys:"):
            raise
        # Otherwise, wrap the error
        raise ValueError(f"Error converting item to Letter: {e}") from e
