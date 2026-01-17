from dataclasses import dataclass
from math import atan2
from typing import Any, ClassVar

from receipt_dynamo.entities.entity_factory import (
    EntityFactory,
    create_geometry_extractors,
    create_image_receipt_pk_parser,
    create_receipt_line_word_sk_parser,
)
from receipt_dynamo.entities.receipt_text_geometry_entity import (
    ReceiptTextGeometryEntity,
)


@dataclass(kw_only=True)
class ReceiptWord(ReceiptTextGeometryEntity):
    """
    Represents a receipt word and its associated metadata stored in a
    DynamoDB table.

    This class encapsulates receipt word-related information such as the
    receipt identifier, image UUID, line identifier, word identifier, text
    content, geometric properties, rotation angles, detection confidence, and
    character statistics. It is designed to support operations such as
    generating DynamoDB keys (including secondary indexes) and converting the
    receipt word to a DynamoDB item.

    Attributes:
        receipt_id (int): Identifier for the receipt.
        image_id (str): UUID identifying the image to which the receipt word
            belongs.
        line_id (int): Identifier for the receipt line.
        word_id (int): Identifier for the receipt word.
        text (str): The text content of the receipt word.
        bounding_box (dict): The bounding box of the receipt word with keys
            'x', 'y', 'width', and 'height'.
        top_right (dict): The top-right corner coordinates with keys 'x' and
            'y'.
        top_left (dict): The top-left corner coordinates with keys 'x' and 'y'.
        bottom_right (dict): The bottom-right corner coordinates with keys
            'x' and 'y'.
        bottom_left (dict): The bottom-left corner coordinates with keys 'x'
            and 'y'.
        angle_degrees (float): The angle of the receipt word in degrees.
        angle_radians (float): The angle of the receipt word in radians.
        confidence (float): The confidence level of the receipt word (between
            0 and 1).
        extracted_data (dict): The extracted data of the receipt word provided
            by Apple's NL API.
        embedding_status (str): The status of the embedding for the receipt
            word.
    """

    # Entity-specific ID fields
    line_id: int
    word_id: int
    extracted_data: dict[str, Any] | None = None

    def __post_init__(self) -> None:
        """Validate and normalize initialization arguments."""
        # Validate line_id
        if not isinstance(self.line_id, int):
            raise ValueError("line_id must be an integer")
        if self.line_id < 0:
            raise ValueError("line_id must be non-negative")

        # Validate word_id
        if not isinstance(self.word_id, int):
            raise ValueError("word_id must be an integer")
        if self.word_id < 0:
            raise ValueError("word_id must be non-negative")

        # Use base class geometry validation
        self._validate_geometry()

        # Use base class receipt field validation
        self._validate_receipt_fields()

        # Additional confidence check for receipt entities
        if self.confidence <= 0.0:
            raise ValueError("confidence must be between 0 and 1")

        # Validate extracted_data
        if self.extracted_data is not None and not isinstance(
            self.extracted_data, dict
        ):
            raise ValueError("extracted_data must be a dict")

    @property
    def key(self) -> dict[str, Any]:
        """
        Generates the primary key for the receipt word.

        Returns:
            dict: The primary key for the receipt word.
        """
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {
                "S": (
                    f"RECEIPT#{self.receipt_id:05d}#"
                    f"LINE#{self.line_id:05d}#"
                    f"WORD#{self.word_id:05d}"
                )
            },
        }

    def gsi1_key(self) -> dict[str, Any]:
        """
        Generates the secondary index key for the receipt word.
        """
        return {
            "GSI1PK": {"S": f"EMBEDDING_STATUS#{self.embedding_status}"},
            "GSI1SK": {
                "S": (
                    f"WORD#"
                    f"IMAGE#{self.image_id}#"
                    f"RECEIPT#{self.receipt_id:05d}#"
                    f"LINE#{self.line_id:05d}#"
                    f"WORD#{self.word_id:05d}"
                )
            },
        }

    def gsi2_key(self) -> dict[str, Any]:
        """
        Generates the secondary index key for the receipt word.

        Returns:
            dict: The secondary index key for the receipt word.
        """
        return {
            "GSI2PK": {"S": "RECEIPT"},
            "GSI2SK": {
                "S": (
                    f"IMAGE#{self.image_id}#"
                    f"RECEIPT#{self.receipt_id:05d}#"
                    f"LINE#{self.line_id:05d}#"
                    f"WORD#{self.word_id:05d}"
                )
            },
        }

    def gsi3_key(self) -> dict[str, Any]:
        """
        Generates the secondary index key for the receipt word.

        Returns:
            dict: The secondary index key for the receipt word.
        """
        return {
            "GSI3PK": {
                "S": f"IMAGE#{self.image_id}#RECEIPT#{self.receipt_id:05d}"
            },
            "GSI3SK": {"S": "WORD"},
        }

    def gsi4_key(self) -> dict[str, Any]:
        """Generates the GSI4 key for receipt details access pattern.

        GSI4 enables efficient single-query retrieval of all receipt-related
        entities (Receipt, Lines, Words, Labels, Place) excluding Letters.
        """
        return {
            "GSI4PK": {
                "S": f"IMAGE#{self.image_id}#RECEIPT#{self.receipt_id:05d}"
            },
            "GSI4SK": {"S": f"3_WORD#{self.line_id:05d}#{self.word_id:05d}"},
        }

    def _serialize_extracted_data(self) -> dict[str, Any]:
        """Serialize extracted_data for DynamoDB."""
        if self.extracted_data is None:
            return {"NULL": True}
        return {
            "M": {k: {"S": str(v)} for k, v in self.extracted_data.items()}
        }

    def to_item(self) -> dict[str, Any]:
        """
        Converts the ReceiptWord object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the ReceiptWord object as a
            DynamoDB item.
        """
        return {
            **self.key,
            "TYPE": {"S": "RECEIPT_WORD"},
            **self.gsi1_key(),
            **self.gsi2_key(),
            **self.gsi3_key(),
            **self.gsi4_key(),
            **self._get_geometry_fields(),
            **self._get_receipt_fields_for_serialization(),
            "extracted_data": self._serialize_extracted_data(),
        }

    def __repr__(self) -> str:
        """Returns a string representation of the ReceiptWord object."""
        geometry_fields = self._get_geometry_repr_fields()
        return (
            f"ReceiptWord("
            f"receipt_id={self.receipt_id}, "
            f"image_id='{self.image_id}', "
            f"line_id={self.line_id}, "
            f"word_id={self.word_id}, "
            f"{geometry_fields}, "
            f"embedding_status='{self.embedding_status}', "
            f"is_noise={self.is_noise}"
            f")"
        )

    def distance_and_angle_from__receipt_word(
        self, other: "ReceiptWord"
    ) -> tuple[float, float]:
        """
        Calculates the distance and the angle between this receipt word and
        another receipt word.

        Args:
            other (ReceiptWord): The other receipt word.

        Returns:
            tuple[float, float]: The distance and angle between the two receipt
            words.
        """
        x1, y1 = self.calculate_centroid()
        x2, y2 = other.calculate_centroid()
        distance = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        angle = atan2(y2 - y1, x2 - x1)
        return distance, angle

    def diff(self, other: "ReceiptWord") -> dict[str, Any]:
        """
        Compare this ReceiptWord with another and return their differences.

        Args:
            other (ReceiptWord): The other ReceiptWord to compare with.

        Returns:
            dict: A dictionary containing the differences between the two
            ReceiptWord objects.
        """
        differences: dict[str, Any] = {}
        for attr, value in sorted(self.__dict__.items()):
            other_value = getattr(other, attr)
            if other_value != value:
                if isinstance(value, dict) and isinstance(other_value, dict):
                    diff: dict[str, Any] = {}
                    all_keys = set(value.keys()) | set(other_value.keys())
                    for k in all_keys:
                        if value.get(k) != other_value.get(k):
                            diff[k] = {
                                "self": value.get(k),
                                "other": other_value.get(k),
                            }
                    if diff:
                        differences[attr] = dict(sorted(diff.items()))
                else:
                    differences[attr] = {"self": value, "other": other_value}
        return differences

    def _get_geometry_hash_fields(self) -> tuple[Any, ...]:
        """
        Override to include entity-specific ID fields in hash computation.

        Returns:
            tuple: A tuple of fields used for hash computation.
        """
        return self._get_base_geometry_hash_fields() + (
            self.receipt_id,
            self.image_id,
            self.line_id,
            self.word_id,
            (
                tuple(self.extracted_data.items())
                if self.extracted_data
                else None
            ),
            self.embedding_status,
            self.is_noise,
        )

    def __hash__(self) -> int:
        """Return hash (required for dataclass with eq=True, frozen=False)."""
        return hash(self._get_geometry_hash_fields())

    # Inherit REQUIRED_KEYS from ReceiptTextGeometryEntity
    REQUIRED_KEYS: ClassVar[set[str]] = ReceiptTextGeometryEntity.REQUIRED_KEYS

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "ReceiptWord":
        """Convert a DynamoDB item to a ReceiptWord object.

        Args:
            item: The DynamoDB item dictionary to convert.

        Returns:
            A ReceiptWord object with all fields properly extracted and
            validated.

        Raises:
            ValueError: If required fields are missing or have invalid format.
        """
        # Type-safe extractors for all fields
        custom_extractors = {
            "text": EntityFactory.extract_text_field,
            "embedding_status": EntityFactory.extract_embedding_status,
            "is_noise": EntityFactory.extract_is_noise,
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
        return EntityFactory.create_entity(
            entity_class=cls,
            item=item,
            required_keys=cls.REQUIRED_KEYS,
            key_parsers={
                "PK": create_image_receipt_pk_parser(),
                "SK": create_receipt_line_word_sk_parser(),
            },
            custom_extractors=custom_extractors,
        )


def item_to_receipt_word(item: dict[str, Any]) -> ReceiptWord:
    """Convert a DynamoDB item to a ReceiptWord object.

    This is a convenience function that delegates to ReceiptWord.from_item().

    Args:
        item: The DynamoDB item dictionary to convert.

    Returns:
        A ReceiptWord object with all fields properly extracted and validated.

    Raises:
        ValueError: If required fields are missing or have invalid format.
    """
    return ReceiptWord.from_item(item)
