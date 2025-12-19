from dataclasses import dataclass
from typing import Any, Dict

from receipt_dynamo.entities.base import DynamoDBEntity
from receipt_dynamo.entities.entity_factory import (
    EntityFactory,
    create_geometry_extractors,
    create_image_receipt_pk_parser,
)
from receipt_dynamo.entities.entity_mixins import (
    GeometryHashMixin,
    GeometryMixin,
    GeometryReprMixin,
    GeometrySerializationMixin,
    GeometryValidationMixin,
    GeometryValidationUtilsMixin,
    SerializationMixin,
    WarpTransformMixin,
)
from receipt_dynamo.entities.receipt_word import EmbeddingStatus
from receipt_dynamo.entities.util import (
    _repr_str,
    assert_valid_uuid,
)


@dataclass(eq=True, unsafe_hash=False)
class ReceiptLine(
    GeometryHashMixin,
    GeometryReprMixin,
    WarpTransformMixin,
    GeometryValidationUtilsMixin,
    SerializationMixin,
    GeometryMixin,
    GeometrySerializationMixin,
    GeometryValidationMixin,
    DynamoDBEntity,
):
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

    receipt_id: int
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
    embedding_status: EmbeddingStatus | str = EmbeddingStatus.NONE
    is_noise: bool = False

    def __post_init__(self) -> None:
        """Validate and normalize initialization arguments."""
        if not isinstance(self.receipt_id, int):
            raise ValueError("receipt_id must be an integer")
        if self.receipt_id <= 0:
            raise ValueError("receipt_id must be positive")

        if not isinstance(self.line_id, int):
            raise ValueError("id must be an integer")
        if self.line_id <= 0:
            raise ValueError("id must be positive")

        # Use validation utils mixin for common validation
        self._validate_common_geometry_entity_fields()

        if isinstance(self.embedding_status, EmbeddingStatus):
            self.embedding_status = self.embedding_status.value
        elif isinstance(self.embedding_status, str):
            allowed_statuses = [s.value for s in EmbeddingStatus]
            if self.embedding_status not in allowed_statuses:
                error_message = (
                    "embedding_status must be one of: "
                    f"{', '.join(allowed_statuses)}\n"
                    f"Got: {self.embedding_status}"
                )
                raise ValueError(error_message)
        else:
            raise ValueError(
                "embedding_status must be an EmbeddingStatus or a string"
            )

        # Validate is_noise field
        if not isinstance(self.is_noise, bool):
            raise ValueError(
                (
                    "is_noise must be a boolean, got "
                    f"{type(self.is_noise).__name__}"
                )
            )

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

    def to_item(self) -> Dict[str, Any]:
        """
        Converts the ReceiptLine object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the ReceiptLine object as a
                DynamoDB item.
        """
        # Use mixin for common geometry fields and add specialized fields
        custom_fields = {
            **self._get_geometry_fields(),
            "embedding_status": {"S": self.embedding_status},
            "is_noise": {"BOOL": self.is_noise},
        }

        return self.build_dynamodb_item(
            entity_type="RECEIPT_LINE",
            gsi_methods=["gsi1_key", "gsi3_key"],
            custom_fields=custom_fields,
            exclude_fields={
                "text",
                "bounding_box",
                "top_right",
                "top_left",
                "bottom_right",
                "bottom_left",
                "angle_degrees",
                "angle_radians",
                "confidence",
                "embedding_status",
                "is_noise",
            },
        )

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

    def _get_geometry_hash_fields(self) -> tuple:
        """Override to include entity-specific ID fields in hash
        computation."""
        return self._get_base_geometry_hash_fields() + (
            self.receipt_id,
            self.image_id,
            self.line_id,
            self.embedding_status,
            self.is_noise,
        )

    def __hash__(self) -> int:
        """Returns the hash value of the ReceiptLine object."""
        return hash(self._get_geometry_hash_fields())


def item_to_receipt_line(item: Dict[str, Any]) -> ReceiptLine:
    """
    Convert a DynamoDB item to a ReceiptLine object using.

    This function uses the EntityFactory pattern to provide full type safety
    and eliminate code duplication. All field extraction is handled by
    type-safe extractors that mypy can validate.

    Args:
        item: The DynamoDB item dictionary to convert.

    Returns:
        A ReceiptLine object with all fields properly extracted and validated.

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

    # Custom SK parser for RECEIPT#{receipt_id:05d}#LINE#{line_id:05d} pattern
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
            entity_class=ReceiptLine,
            item=item,
            required_keys=required_keys,
            key_parsers={
                "PK": create_image_receipt_pk_parser(),
                "SK": parse_receipt_line_sk,
            },
            custom_extractors=custom_extractors,
        )
    except ValueError as e:
        # Re-raise ValueError with original message if it's about missing keys
        if "missing required keys" in str(e):
            raise
        # Otherwise wrap it as a conversion error
        raise ValueError(f"Error converting item to ReceiptLine: {e}") from e
    except Exception as e:
        raise ValueError(f"Error converting item to ReceiptLine: {e}") from e
