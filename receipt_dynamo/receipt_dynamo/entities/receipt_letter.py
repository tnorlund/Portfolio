from dataclasses import dataclass
from typing import Any, Dict, Generator, Tuple

from receipt_dynamo.entities.base import DynamoDBEntity
from receipt_dynamo.entities.entity_mixins import (
    GeometryHashMixin,
    GeometryMixin,
    GeometryReprMixin,
    GeometrySerializationMixin,
    GeometryValidationMixin,
    GeometryValidationUtilsMixin,
    WarpTransformMixin,
)
from receipt_dynamo.entities.util import (
    _repr_str,
    assert_valid_uuid,
)


@dataclass(eq=True, unsafe_hash=False)
class ReceiptLetter(
    GeometryHashMixin,
    GeometryReprMixin,
    WarpTransformMixin,
    GeometryValidationUtilsMixin,
    GeometryMixin,
    GeometrySerializationMixin,
    GeometryValidationMixin,
    DynamoDBEntity,
):
    """
    Represents a receipt letter and its associated metadata stored in a
    DynamoDB table.

    This class encapsulates receipt letter-related information such as the
    receipt identifier, image UUID, line identifier, word identifier,
    letter identifier, text content (exactly one character), geometric
    properties, rotation angles, and detection confidence. It is designed to
    support operations such as generating DynamoDB keys and converting the
    receipt letter to a DynamoDB item.

    Attributes:
        receipt_id (int): Identifier for the receipt.
        image_id (str): UUID identifying the image to which the receipt letter
            belongs.
        line_id (int): Identifier for the receipt line.
        word_id (int): Identifier for the receipt word that this letter belongs
            to.
        letter_id (int): Identifier for the receipt letter.
        text (str): The text content of the receipt letter (must be exactly one
            character).
        bounding_box (dict): The bounding box of the receipt letter with keys
            'x', 'y', 'width', and 'height'.
        top_right (dict): The top-right corner coordinates with keys 'x' and
            'y'.
        top_left (dict): The top-left corner coordinates with keys 'x' and 'y'.
        bottom_right (dict): The bottom-right corner coordinates with keys 'x'
            and 'y'.
        bottom_left (dict): The bottom-left corner coordinates with keys 'x'
            and 'y'.
        angle_degrees (float): The angle of the receipt letter in degrees.
        angle_radians (float): The angle of the receipt letter in radians.
        confidence (float): The confidence level of the receipt letter (between
            0 and 1).
    """

    receipt_id: int
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
        if not isinstance(self.receipt_id, int):
            raise ValueError("receipt_id must be an integer")
        if self.receipt_id <= 0:
            raise ValueError("receipt_id must be positive")

        if not isinstance(self.line_id, int):
            raise ValueError("line_id must be an integer")
        if self.line_id < 0:
            raise ValueError("line_id must be positive")

        if not isinstance(self.word_id, int):
            raise ValueError("word_id must be an integer")
        if self.word_id < 0:
            raise ValueError("word_id must be positive")

        if not isinstance(self.letter_id, int):
            raise ValueError("letter_id must be an integer")
        if self.letter_id < 0:
            raise ValueError("letter_id must be positive")

        # Use validation utils mixin for common validation
        # (handles image_id and text)
        self._validate_common_geometry_entity_fields()

        # Additional validation specific to letter
        if len(self.text) != 1:
            raise ValueError("text must be exactly one character")

        # Note: confidence validation in mixin allows <= 0.0, but receipt
        # entities require > 0.0
        if self.confidence <= 0.0:
            raise ValueError("confidence must be between 0 and 1")

    @property
    def key(self) -> Dict[str, Any]:
        """
        Generates the primary key for the receipt letter.

        Returns:
            dict: The primary key for the receipt letter.
        """
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {
                "S": (
                    f"RECEIPT#{self.receipt_id:05d}#"
                    f"LINE#{self.line_id:05d}#"
                    f"WORD#{self.word_id:05d}#"
                    f"LETTER#{self.letter_id:05d}"
                )
            },
        }

    def to_item(self) -> Dict[str, Any]:
        """
        Converts the ReceiptLetter object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the ReceiptLetter object as a
            DynamoDB item.
        """
        return {
            **self.key,
            "TYPE": {"S": "RECEIPT_LETTER"},
            **self._get_geometry_fields(),
        }

    def __eq__(self, other: object) -> bool:
        """
        Determines whether two ReceiptLetter objects are equal.

        Args:
            other (object): The object to compare.

        Returns:
            bool: True if the ReceiptLetter objects are equal, False otherwise.
        """
        if not isinstance(other, ReceiptLetter):
            return False
        return (
            self.receipt_id == other.receipt_id
            and self.image_id == other.image_id
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

    def __iter__(self) -> Generator[Tuple[str, Any], None, None]:
        """
        Returns an iterator over the ReceiptLetter object's attributes.

        Yields:
            Tuple[str, any]: A tuple containing the attribute name and its
            value.
        """
        yield "image_id", self.image_id
        yield "receipt_id", self.receipt_id
        yield "line_id", self.line_id
        yield "word_id", self.word_id
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

    def __repr__(self) -> str:
        """
        Returns a string representation of the ReceiptLetter object.

        Returns:
            str: A string representation of the ReceiptLetter object.
        """
        geometry_fields = self._get_geometry_repr_fields()
        return (
            f"ReceiptLetter("
            f"receipt_id={self.receipt_id}, "
            f"image_id={_repr_str(self.image_id)}, "
            f"line_id={self.line_id}, "
            f"word_id={self.word_id}, "
            f"letter_id={self.letter_id}, "
            f"{geometry_fields}"
            f")"
        )

    def _get_geometry_hash_fields(self) -> tuple:
        """Override to include entity-specific ID fields in hash
        computation."""
        return self._get_base_geometry_hash_fields() + (
            self.receipt_id,
            self.image_id,
            self.line_id,
            self.word_id,
            self.letter_id,
        )

    def __hash__(self) -> int:
        """
        Generates a hash value for the ReceiptLetter object.

        Returns:
            int: The hash value for the ReceiptLetter object.
        """
        return hash(self._get_geometry_hash_fields())


def item_to_receipt_letter(item: Dict[str, Any]) -> ReceiptLetter:
    """
    Converts a DynamoDB item to a ReceiptLetter object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        ReceiptLetter: The ReceiptLetter object represented by the DynamoDB
        item.

    Raises:
        ValueError: When the item format is invalid or required keys are
        missing.
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

    # Custom SK parser for RECEIPT#/LINE#/WORD#/LETTER# pattern
    def parse_receipt_letter_sk(sk: str) -> Dict[str, Any]:
        """Parse the SK to extract receipt_id, line_id, word_id, and
        letter_id."""
        parts = sk.split("#")
        if (
            len(parts) < 8
            or parts[0] != "RECEIPT"
            or parts[2] != "LINE"
            or parts[4] != "WORD"
            or parts[6] != "LETTER"
        ):
            raise ValueError(f"Invalid SK format for ReceiptLetter: {sk}")

        return {
            "receipt_id": int(parts[1]),
            "line_id": int(parts[3]),
            "word_id": int(parts[5]),
            "letter_id": int(parts[7]),
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

    # Use EntityFactory to create the entity with full type safety
    return EntityFactory.create_entity(
        entity_class=ReceiptLetter,
        item=item,
        required_keys=required_keys,
        key_parsers={
            "PK": create_image_receipt_pk_parser(),
            "SK": parse_receipt_letter_sk,
        },
        custom_extractors=custom_extractors,
    )
