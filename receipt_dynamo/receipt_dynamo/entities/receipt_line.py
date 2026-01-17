from dataclasses import dataclass
from typing import Any, ClassVar, Dict, Set, Tuple

from receipt_dynamo.constants import EmbeddingStatus
from receipt_dynamo.entities.receipt_text_geometry_entity import (
    ReceiptTextGeometryEntity,
)
from receipt_dynamo.entities.entity_factory import (
    EntityFactory,
    create_geometry_extractors,
    create_image_receipt_pk_parser,
)
from receipt_dynamo.entities.util import (
    _repr_str,
    build_base_item,
)


@dataclass(kw_only=True)
class ReceiptLine(ReceiptTextGeometryEntity):
    """Receipt line metadata stored in DynamoDB.

    This class encapsulates receipt line information such as the receipt
    identifier, image UUID, text, geometric properties, and rotation angles.
    It includes detection confidence and supports generating DynamoDB keys
    before converting the line to a DynamoDB item.

    Attributes:
        receipt_id (int): Identifier for the receipt.
        image_id (str): UUID identifying the image to which the receipt line
            belongs.
        line_id (int): Identifier for the receipt line.
        text (str): The text content of the receipt line.
        bounding_box (dict): Bounding box with keys ``x``, ``y``, ``width`` and
            ``height``.
        top_right (dict): The top-right corner with keys ``x`` and ``y``.
        top_left (dict): The top-left corner with keys ``x`` and ``y``.
        bottom_right (dict): The bottom-right corner with keys ``x`` and ``y``.
        bottom_left (dict): The bottom-left corner with keys ``x`` and ``y``.
        angle_degrees (float): The angle of the receipt line in degrees.
        angle_radians (float): The angle of the receipt line in radians.
        confidence (float): Confidence level of the line between 0 and 1.
        embedding_status (EmbeddingStatus): Embedding status for the line.
    """

    # Entity-specific ID fields
    line_id: int

    def __post_init__(self) -> None:
        """Validate and normalize initialization arguments."""
        # Validate line_id
        if not isinstance(self.line_id, int):
            raise ValueError("id must be an integer")
        if self.line_id <= 0:
            raise ValueError("id must be positive")

        # Use base class geometry validation
        self._validate_geometry()

        # Use base class receipt field validation
        self._validate_receipt_fields()

    @property
    def key(self) -> Dict[str, Any]:
        """
        Generates the primary key for the receipt line.

        Returns:
            dict: The primary key for the receipt line.
        """
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {
                "S": f"RECEIPT#{self.receipt_id:05d}#LINE#{self.line_id:05d}"
            },
        }

    def gsi1_key(self) -> Dict[str, Any]:
        """
        Generates the secondary index key for the receipt line.
        """
        return {
            "GSI1PK": {"S": f"EMBEDDING_STATUS#{self.embedding_status}"},
            "GSI1SK": {
                "S": (
                    f"LINE#"
                    f"IMAGE#{self.image_id}#"
                    f"RECEIPT#{self.receipt_id:05d}#"
                    f"LINE#{self.line_id:05d}"
                )
            },
        }

    def gsi3_key(self) -> Dict[str, Any]:
        """
        Generates the GSI3 secondary index key for the receipt line.
        Enables efficient querying by image_id + receipt_id.
        """
        return {
            "GSI3PK": {
                "S": f"IMAGE#{self.image_id}#RECEIPT#{self.receipt_id:05d}"
            },
            "GSI3SK": {"S": "LINE"},
        }

    def gsi4_key(self) -> Dict[str, Any]:
        """Generates the GSI4 key for receipt details access pattern.

        GSI4 enables efficient single-query retrieval of all receipt-related
        entities (Receipt, Lines, Words, Labels, Place) while excluding Letters.
        """
        return {
            "GSI4PK": {
                "S": f"IMAGE#{self.image_id}#RECEIPT#{self.receipt_id:05d}"
            },
            "GSI4SK": {"S": f"2_LINE#{self.line_id:05d}"},
        }

    def to_item(self) -> Dict[str, Any]:
        """
        Converts the ReceiptLine object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the ReceiptLine object as a
                DynamoDB item.
        """
        return {
            **build_base_item(self, "RECEIPT_LINE"),
            **self.gsi1_key(),
            **self.gsi3_key(),
            **self.gsi4_key(),
            **self._get_geometry_fields(),
            **self._get_receipt_fields_for_serialization(),
        }

    def __repr__(self) -> str:
        geometry_fields = self._get_geometry_repr_fields()
        return (
            f"ReceiptLine("
            f"receipt_id={self.receipt_id}, "
            f"image_id={_repr_str(self.image_id)}, "
            f"line_id={self.line_id}, "
            f"{geometry_fields}, "
            f"embedding_status={self.embedding_status}, "
            f"is_noise={self.is_noise}"
            f")"
        )

    def _get_geometry_hash_fields(self) -> Tuple[Any, ...]:
        """Include entity-specific ID fields in hash computation."""
        return self._get_base_geometry_hash_fields() + (
            self.receipt_id,
            self.image_id,
            self.line_id,
            self.embedding_status,
            self.is_noise,
        )

    def __hash__(self) -> int:
        """Return hash (required for dataclass with eq=True, frozen=False)."""
        return hash(self._get_geometry_hash_fields())

    # Inherit REQUIRED_KEYS from ReceiptTextGeometryEntity
    REQUIRED_KEYS: ClassVar[Set[str]] = (
        ReceiptTextGeometryEntity.REQUIRED_KEYS
    )

    @classmethod
    def from_item(cls, item: Dict[str, Any]) -> "ReceiptLine":
        """Convert a DynamoDB item to a ReceiptLine object.

        Args:
            item: The DynamoDB item dictionary to convert.

        Returns:
            A ReceiptLine object with all fields properly extracted and
            validated.

        Raises:
            ValueError: If required fields are missing or have invalid format.
        """
        # Custom SK parser for RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}
        def parse_receipt_line_sk(sk: str) -> Dict[str, Any]:
            """Parse the SK to extract receipt_id and line_id."""
            parts = sk.split("#")
            if len(parts) < 4 or parts[0] != "RECEIPT" or parts[2] != "LINE":
                raise ValueError(f"Invalid SK format for ReceiptLine: {sk}")

            return {
                "receipt_id": int(parts[1]),
                "line_id": int(parts[3]),
            }

        # Type-safe extractors for all fields
        custom_extractors = {
            "text": EntityFactory.extract_text_field,
            "embedding_status": EntityFactory.extract_embedding_status,
            "is_noise": EntityFactory.extract_is_noise,
            **create_geometry_extractors(),  # Handles all geometry fields
        }

        # Use EntityFactory to create the entity with full type safety
        try:
            return EntityFactory.create_entity(
                entity_class=cls,
                item=item,
                required_keys=cls.REQUIRED_KEYS,
                key_parsers={
                    "PK": create_image_receipt_pk_parser(),
                    "SK": parse_receipt_line_sk,
                },
                custom_extractors=custom_extractors,
            )
        except ValueError as e:
            # Re-raise ValueError with original message if about missing keys
            if "missing required keys" in str(e):
                raise
            # Otherwise wrap it as a conversion error
            raise ValueError(
                f"Error converting item to ReceiptLine: {e}"
            ) from e
        except Exception as e:
            raise ValueError(
                f"Error converting item to ReceiptLine: {e}"
            ) from e


def item_to_receipt_line(item: Dict[str, Any]) -> ReceiptLine:
    """Convert a DynamoDB item to a ReceiptLine object.

    This is a convenience function that delegates to ReceiptLine.from_item().

    Args:
        item: The DynamoDB item dictionary to convert.

    Returns:
        A ReceiptLine object with all fields properly extracted and validated.

    Raises:
        ValueError: If required fields are missing or have invalid format.
    """
    return ReceiptLine.from_item(item)


# Re-export EmbeddingStatus for backwards compatibility
__all__ = ["ReceiptLine", "item_to_receipt_line", "EmbeddingStatus"]
