"""
Receipt-specific text geometry entity base class.

This module provides a base class for receipt-level text geometry entities
(ReceiptLine, ReceiptWord, ReceiptLetter) that adds receipt-specific fields
on top of the common TextGeometryEntity base class.

Benefits:
    - Eliminates duplicate receipt-specific field definitions
    - Reduces inheritance depth for receipt entities
    - Centralizes embedding_status and is_noise validation
"""

from dataclasses import dataclass
from typing import Any, ClassVar

from receipt_dynamo.constants import EmbeddingStatus
from receipt_dynamo.entities.text_geometry_entity import TextGeometryEntity


@dataclass(kw_only=True)
class ReceiptTextGeometryEntity(TextGeometryEntity):
    """
    Base class for receipt-level text geometry entities.

    This class extends TextGeometryEntity with receipt-specific fields:
        - receipt_id: Identifier for the receipt
        - embedding_status: Status of embedding processing
        - is_noise: Whether this entity is classified as noise

    Subclasses (ReceiptLine, ReceiptWord, ReceiptLetter) should:
        1. Add their specific ID fields (line_id, word_id, letter_id)
        2. Call super().__post_init__() in their __post_init__
        3. Override _get_geometry_hash_fields() to include their ID fields

    Class Variables:
        BASE_REQUIRED_KEYS: Inherited from TextGeometryEntity, used by all
            receipt geometry entities. Receipt-specific fields
            (embedding_status, is_noise) have defaults and are not required
            in DynamoDB items.
    """

    # Receipt entities use the same required keys as base geometry entities
    # (receipt_id from SK parsing, embedding_status/is_noise have defaults)
    REQUIRED_KEYS: ClassVar[set[str]] = TextGeometryEntity.BASE_REQUIRED_KEYS

    # Receipt-specific fields
    receipt_id: int
    embedding_status: EmbeddingStatus | str = EmbeddingStatus.NONE
    is_noise: bool = False

    def _validate_receipt_fields(self) -> None:
        """
        Validate receipt-specific fields.

        Call this from subclass __post_init__ after validating geometry
        and entity-specific ID fields.

        Validates:
            1. receipt_id is a positive integer
            2. embedding_status is valid (enum or string)
            3. is_noise is a boolean

        Raises:
            ValueError: If any field has invalid type or value
        """
        # Validate receipt_id
        if not isinstance(self.receipt_id, int):
            raise ValueError("receipt_id must be an integer")
        if self.receipt_id <= 0:
            raise ValueError("receipt_id must be positive")

        # Normalize and validate embedding_status
        if isinstance(self.embedding_status, EmbeddingStatus):
            self.embedding_status = self.embedding_status.value
        elif isinstance(self.embedding_status, str):
            valid_values = [s.value for s in EmbeddingStatus]
            if self.embedding_status not in valid_values:
                raise ValueError(
                    f"embedding_status must be one of: "
                    f"{', '.join(valid_values)}\nGot: {self.embedding_status}"
                )
        else:
            raise ValueError(
                "embedding_status must be a string or EmbeddingStatus enum"
            )

        # Validate is_noise
        if not isinstance(self.is_noise, bool):
            raise ValueError(
                f"is_noise must be a boolean, got "
                f"{type(self.is_noise).__name__}"
            )

    def _get_receipt_fields_for_serialization(self) -> dict[str, Any]:
        """
        Return receipt-specific fields serialized for DynamoDB.

        Returns:
            Dict with DynamoDB-formatted receipt fields
        """
        return {
            "embedding_status": {"S": self.embedding_status},
            "is_noise": {"BOOL": self.is_noise},
        }


__all__ = ["ReceiptTextGeometryEntity"]
