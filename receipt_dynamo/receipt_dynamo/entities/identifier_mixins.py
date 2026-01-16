"""
Identifier mixins for hierarchical entity relationships.

These mixins standardize the commonly duplicated ID fields across entities,
providing consistent validation and DynamoDB key generation patterns.

Hierarchy for receipt-based entities:
    Image -> Receipt -> Line -> Word

Hierarchy for image OCR entities (no receipt):
    Image -> Line -> Word

Job-related entities use JobIdentifierMixin.

Classes:
    ImageIdentifierMixin: Base for entities belonging to an image
    ReceiptIdentifierMixin: For entities within a receipt
    LineIdentifierMixin: For entities within a line (receipt context)
    WordIdentifierMixin: For entities within a word (receipt context)
    ImageLineIdentifierMixin: For line entities directly under an image
    ImageWordIdentifierMixin: For word entities directly under a line
    JobIdentifierMixin: For job-related entities
"""

from typing import Any, Dict

from receipt_dynamo.entities.util import (
    assert_valid_uuid,
    validate_non_negative_int,
    validate_positive_int,
)


class ImageIdentifierMixin:
    """
    Mixin for entities that belong to an image.

    Provides validation and key generation for image_id field.

    Attributes:
        image_id: UUID string identifying the source image
    """

    image_id: str

    def _validate_image_id(self) -> None:
        """
        Validate that image_id is a valid UUIDv4 string.

        Raises:
            ValueError: If image_id is not a valid UUID
        """
        assert_valid_uuid(self.image_id)

    def _pk_image(self) -> Dict[str, Any]:
        """
        Generate IMAGE# partition key for DynamoDB.

        Returns:
            Dict with PK in DynamoDB format: {"PK": {"S": "IMAGE#..."}}
        """
        return {"PK": {"S": f"IMAGE#{self.image_id}"}}


class ReceiptIdentifierMixin(ImageIdentifierMixin):
    """
    Mixin for entities that belong to a receipt within an image.

    Extends ImageIdentifierMixin with receipt_id validation and
    composite key generation.

    Attributes:
        image_id: UUID string identifying the source image (inherited)
        receipt_id: Positive integer identifying the receipt
    """

    receipt_id: int

    def _validate_receipt_id(self) -> None:
        """
        Validate that receipt_id is a positive integer.

        Raises:
            ValueError: If receipt_id is not a positive integer
        """
        validate_positive_int("receipt_id", self.receipt_id)

    def _validate_receipt_identifiers(self) -> None:
        """
        Validate both image_id and receipt_id.

        Convenience method for __post_init__ implementations.

        Raises:
            ValueError: If either identifier is invalid
        """
        self._validate_image_id()
        self._validate_receipt_id()

    def _sk_receipt(self) -> str:
        """
        Generate RECEIPT# sort key component.

        Returns:
            Formatted string: "RECEIPT#00001"
        """
        return f"RECEIPT#{self.receipt_id:05d}"

    def _composite_image_receipt(self) -> str:
        """
        Generate IMAGE#...#RECEIPT#... composite key.

        Used for GSI keys that need to query by image+receipt.

        Returns:
            Formatted string: "IMAGE#uuid#RECEIPT#00001"
        """
        return f"IMAGE#{self.image_id}#RECEIPT#{self.receipt_id:05d}"


class LineIdentifierMixin(ReceiptIdentifierMixin):
    """
    Mixin for entities that belong to a line within a receipt.

    Extends ReceiptIdentifierMixin with line_id validation and
    key generation.

    Attributes:
        image_id: UUID string identifying the source image (inherited)
        receipt_id: Positive integer identifying the receipt (inherited)
        line_id: Non-negative integer identifying the line
    """

    line_id: int

    def _validate_line_id(self) -> None:
        """
        Validate that line_id is a non-negative integer.

        Note: line_id can be 0 in some contexts (e.g., first line).

        Raises:
            ValueError: If line_id is not a non-negative integer
        """
        validate_non_negative_int("line_id", self.line_id)

    def _validate_line_identifiers(self) -> None:
        """
        Validate image_id, receipt_id, and line_id.

        Convenience method for __post_init__ implementations.

        Raises:
            ValueError: If any identifier is invalid
        """
        self._validate_receipt_identifiers()
        self._validate_line_id()

    def _sk_receipt_line(self) -> str:
        """
        Generate RECEIPT#...#LINE#... sort key.

        Returns:
            Formatted string: "RECEIPT#00001#LINE#00001"
        """
        return f"RECEIPT#{self.receipt_id:05d}#LINE#{self.line_id:05d}"


class WordIdentifierMixin(LineIdentifierMixin):
    """
    Mixin for entities that belong to a word within a line.

    Extends LineIdentifierMixin with word_id validation and
    complete hierarchical key generation.

    Attributes:
        image_id: UUID string identifying the source image (inherited)
        receipt_id: Positive integer identifying the receipt (inherited)
        line_id: Non-negative integer identifying the line (inherited)
        word_id: Non-negative integer identifying the word
    """

    word_id: int

    def _validate_word_id(self) -> None:
        """
        Validate that word_id is a non-negative integer.

        Raises:
            ValueError: If word_id is not a non-negative integer
        """
        validate_non_negative_int("word_id", self.word_id)

    def _validate_word_identifiers(self) -> None:
        """
        Validate all hierarchical identifiers.

        Convenience method for __post_init__ implementations.

        Raises:
            ValueError: If any identifier is invalid
        """
        self._validate_line_identifiers()
        self._validate_word_id()

    def _sk_receipt_line_word(self) -> str:
        """
        Generate RECEIPT#...#LINE#...#WORD#... sort key.

        Returns:
            Formatted string: "RECEIPT#00001#LINE#00001#WORD#00001"
        """
        return (
            f"RECEIPT#{self.receipt_id:05d}#"
            f"LINE#{self.line_id:05d}#"
            f"WORD#{self.word_id:05d}"
        )


# =============================================================================
# Non-receipt entity identifiers (Image OCR results without receipt context)
# =============================================================================


class ImageLineIdentifierMixin(ImageIdentifierMixin):
    """
    Mixin for line entities directly under an image (no receipt).

    Used for raw OCR results before receipt detection/extraction.

    Attributes:
        image_id: UUID string identifying the source image (inherited)
        line_id: Positive integer identifying the line
    """

    line_id: int

    def _validate_line_id(self) -> None:
        """
        Validate that line_id is a positive integer.

        Note: Unlike receipt-context lines, image-level lines must be > 0.

        Raises:
            ValueError: If line_id is not a positive integer
        """
        validate_positive_int("line_id", self.line_id)

    def _validate_image_line_identifiers(self) -> None:
        """
        Validate image_id and line_id.

        Raises:
            ValueError: If any identifier is invalid
        """
        self._validate_image_id()
        self._validate_line_id()

    def _sk_line(self) -> str:
        """
        Generate LINE# sort key.

        Returns:
            Formatted string: "LINE#00001"
        """
        return f"LINE#{self.line_id:05d}"


class ImageWordIdentifierMixin(ImageLineIdentifierMixin):
    """
    Mixin for word entities directly under a line (no receipt).

    Used for raw OCR results before receipt detection/extraction.

    Attributes:
        image_id: UUID string identifying the source image (inherited)
        line_id: Positive integer identifying the line (inherited)
        word_id: Non-negative integer identifying the word
    """

    word_id: int

    def _validate_word_id(self) -> None:
        """
        Validate that word_id is a non-negative integer.

        Raises:
            ValueError: If word_id is not a non-negative integer
        """
        validate_non_negative_int("word_id", self.word_id)

    def _validate_image_word_identifiers(self) -> None:
        """
        Validate all identifiers.

        Raises:
            ValueError: If any identifier is invalid
        """
        self._validate_image_line_identifiers()
        self._validate_word_id()

    def _sk_line_word(self) -> str:
        """
        Generate LINE#...#WORD#... sort key.

        Returns:
            Formatted string: "LINE#00001#WORD#00001"
        """
        return f"LINE#{self.line_id:05d}#WORD#{self.word_id:05d}"


# =============================================================================
# Job-related identifiers
# =============================================================================


class JobIdentifierMixin:
    """
    Mixin for entities that belong to a training/processing job.

    Provides validation and key generation for job-related entities
    like JobStatus, JobCheckpoint, JobLog, JobMetric, etc.

    Attributes:
        job_id: UUID string identifying the job
    """

    job_id: str

    def _validate_job_id(self) -> None:
        """
        Validate that job_id is a valid UUIDv4 string.

        Raises:
            ValueError: If job_id is not a valid UUID
        """
        assert_valid_uuid(self.job_id)

    def _pk_job(self) -> Dict[str, Any]:
        """
        Generate JOB# partition key for DynamoDB.

        Returns:
            Dict with PK in DynamoDB format: {"PK": {"S": "JOB#..."}}
        """
        return {"PK": {"S": f"JOB#{self.job_id}"}}


__all__ = [  # noqa: RUF022 - keep logical grouping
    # Receipt hierarchy
    "ImageIdentifierMixin",
    "ReceiptIdentifierMixin",
    "LineIdentifierMixin",
    "WordIdentifierMixin",
    # Image OCR hierarchy (no receipt)
    "ImageLineIdentifierMixin",
    "ImageWordIdentifierMixin",
    # Job hierarchy
    "JobIdentifierMixin",
]
