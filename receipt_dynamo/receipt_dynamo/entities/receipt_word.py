from dataclasses import dataclass
from math import atan2
from typing import Any, Dict, Optional, Tuple

from receipt_dynamo.constants import EmbeddingStatus
from receipt_dynamo.entities.base import DynamoDBEntity
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

from .entity_factory import (
    EntityFactory,
    create_geometry_extractors,
    create_image_receipt_pk_parser,
    create_receipt_line_word_sk_parser,
)


@dataclass(eq=True, unsafe_hash=False)
class ReceiptWord(
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

    receipt_id: int
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
    embedding_status: EmbeddingStatus | str = EmbeddingStatus.NONE
    is_noise: bool = False

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
            raise ValueError("id must be an integer")
        if self.word_id < 0:
            raise ValueError("id must be positive")

        # Use validation utils mixin for common validation
        self._validate_common_geometry_entity_fields()

        # Note: confidence validation in mixin allows <= 0.0, but receipt
        # entities require > 0.0
        if self.confidence <= 0.0:
            raise ValueError("confidence must be between 0 and 1")

        if self.extracted_data is not None and not isinstance(
            self.extracted_data, dict
        ):
            raise ValueError("extracted_data must be a dict")

        # Normalize and validate embedding_status (allow enum or string)
        if isinstance(self.embedding_status, EmbeddingStatus):
            self.embedding_status = self.embedding_status.value
        elif isinstance(self.embedding_status, str):
            valid_values = [s.value for s in EmbeddingStatus]
            if self.embedding_status not in valid_values:
                raise ValueError(
                    (
                        "embedding_status must be one of: "
                        f"{', '.join(valid_values)}\n"
                        f"Got: {self.embedding_status}"
                    )
                )
        else:
            raise ValueError(
                "embedding_status must be a string or EmbeddingStatus enum"
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

    def gsi1_key(self) -> Dict[str, Any]:
        """
        Generates the secondary index key for the receipt word.
        """
        return {
            "GSI1PK": {"S": f"EMBEDDING_STATUS#{self.embedding_status}"},
            "GSI1SK": {
                "S": (
                    f"IMAGE#{self.image_id}#"
                    f"RECEIPT#{self.receipt_id:05d}#"
                    f"LINE#{self.line_id:05d}#"
                    f"WORD#{self.word_id:05d}"
                )
            },
        }

    def gsi2_key(self) -> Dict[str, Any]:
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

    def gsi3_key(self) -> Dict[str, Any]:
        """
        Generates the secondary index key for the receipt word.

        Returns:
            dict: The secondary index key for the receipt word.
        """
        return {
            "GSI3PK": {"S": f"IMAGE#{self.image_id}"},
            "GSI3SK": {
                "S": (
                    f"RECEIPT#{self.receipt_id:05d}#"
                    f"LINE#{self.line_id:05d}#"
                    f"WORD#{self.word_id:05d}"
                )
            },
        }

    def to_item(self) -> Dict[str, Any]:
        """
        Converts the ReceiptWord object to a DynamoDB item using
        SerializationMixin.

        Returns:
            dict: A dictionary representing the ReceiptWord object as a
            DynamoDB item.
        """
        # Use mixin for common geometry fields and add specialized fields
        custom_fields = {
            **self._get_geometry_fields(),
            "extracted_data": self._serialize_value(self.extracted_data),
            "embedding_status": {"S": self.embedding_status},
            "is_noise": {"BOOL": self.is_noise},
        }

        return self.build_dynamodb_item(
            entity_type="RECEIPT_WORD",
            gsi_methods=["gsi1_key", "gsi2_key", "gsi3_key"],
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
                "extracted_data",
                "embedding_status",
                "is_noise",
            },
        )

    def __hash__(self) -> int:
        """Returns the hash value of the ReceiptWord object."""
        return hash(self._get_geometry_hash_fields())

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
    ) -> Tuple[float, float]:
        """
        Calculates the distance and the angle between this receipt word and
        another receipt word.

        Args:
            other (ReceiptWord): The other receipt word.

        Returns:
            Tuple[float, float]: The distance and angle between the two receipt
            words.
        """
        x1, y1 = self.calculate_centroid()
        x2, y2 = other.calculate_centroid()
        distance = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5
        angle = atan2(y2 - y1, x2 - x1)
        return distance, angle

    def diff(self, other: "ReceiptWord") -> Dict[str, Any]:
        """
        Compare this ReceiptWord with another and return their differences.

        Args:
            other (ReceiptWord): The other ReceiptWord to compare with.

        Returns:
            dict: A dictionary containing the differences between the two
            ReceiptWord objects.
        """
        differences: Dict[str, Any] = {}
        for attr, value in sorted(self.__dict__.items()):
            other_value = getattr(other, attr)
            if other_value != value:
                if isinstance(value, dict) and isinstance(other_value, dict):
                    diff: Dict[str, Any] = {}
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

    def _get_geometry_hash_fields(self) -> tuple:
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


def item_to_receipt_word(item: Dict[str, Any]) -> ReceiptWord:
    """
    Converts a DynamoDB item to a ReceiptWord object using EntityFactory.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        ReceiptWord: The ReceiptWord object represented by the DynamoDB item.

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
        entity_class=ReceiptWord,
        item=item,
        required_keys=required_keys,
        key_parsers={
            "PK": create_image_receipt_pk_parser(),
            "SK": create_receipt_line_word_sk_parser(),
        },
        custom_extractors=custom_extractors,
    )
