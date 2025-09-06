"""
This module defines standardized status enums for DynamoDB entities related to
receipt parsing, labeling, embedding, and batch processing.
"""

from enum import Enum


class ValidationStatus(str, Enum):
    """Standardized validation state for receipt word labels."""

    NONE = "NONE"  # No validation has ever been initiated
    PENDING = "PENDING"  # Validation has been queued
    VALID = "VALID"  # Validation succeeded
    INVALID = "INVALID"  # Validation rejected
    NEEDS_REVIEW = "NEEDS_REVIEW"  # Validation needs review test


class BatchStatus(str, Enum):
    """States for batch job execution.

    Maps to OpenAI Batch API statuses:
    - VALIDATING: Initial file validation in progress
    - IN_PROGRESS: Batch processing underway
    - FINALIZING: Results being prepared
    - COMPLETED: Success, results ready
    - FAILED: Validation or processing error
    - EXPIRED: Exceeded 24h SLA, partial results may be available
    - CANCELING: Cancellation requested
    - CANCELLED: Successfully cancelled
    - PENDING: Internal status for batches submitted but not yet checked
    """

    PENDING = "PENDING"  # Internal status before first poll
    VALIDATING = "VALIDATING"
    IN_PROGRESS = "IN_PROGRESS"
    FINALIZING = "FINALIZING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    EXPIRED = "EXPIRED"
    CANCELING = "CANCELING"
    CANCELLED = "CANCELLED"


class BatchType(str, Enum):
    """Types of batch jobs for label processing."""

    COMPLETION = "COMPLETION"
    EMBEDDING = "EMBEDDING"  # Deprecated - use WORD_EMBEDDING
    WORD_EMBEDDING = "WORD_EMBEDDING"
    LINE_EMBEDDING = "LINE_EMBEDDING"


class LabelStatus(str, Enum):
    """Status assigned to a canonical label."""

    ACTIVE = "ACTIVE"
    DEPRECATED = "DEPRECATED"


class EmbeddingStatus(str, Enum):
    """Tracking the outcome of OpenAI embedding jobs."""

    NONE = "NONE"
    PENDING = "PENDING"
    SUCCESS = "SUCCESS"
    FAILED = "FAILED"
    NOISE = "NOISE"


class SectionType(str, Enum):
    """Types of receipt sections for classification."""

    HEADER = "HEADER"  # Contains merchant info, date, receipt number
    FOOTER = "FOOTER"  # Contains totals, payment info, thank you notes
    ITEMS_VALUE = "ITEMS_VALUE"  # The number that the item is worth
    ITEMS_DESCRIPTION = "ITEMS_DESCRIPTION"  # The description of the item


class MerchantValidationStatus(str, Enum):
    """Tracking the outcome of merchant validation jobs."""

    MATCHED = "MATCHED"
    NO_MATCH = "NO_MATCH"
    UNSURE = "UNSURE"


class ValidationMethod(Enum):
    PHONE_LOOKUP = "PHONE_LOOKUP"
    ADDRESS_LOOKUP = "ADDRESS_LOOKUP"
    NEARBY_LOOKUP = "NEARBY_LOOKUP"
    TEXT_SEARCH = "TEXT_SEARCH"
    INFERENCE = "INFERENCE"


class PassNumber(Enum):
    """The pass number for a completion batch result."""

    FIRST = "FIRST_PASS"
    SECOND = "SECOND_PASS"


class OCRStatus(Enum):
    """The status of an OCR job."""

    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class OCRJobType(Enum):
    """The type of OCR job."""

    REFINEMENT = "REFINEMENT"
    FIRST_PASS = "FIRST_PASS"


class ImageType(Enum):
    """The type of image."""

    SCAN = "SCAN"
    PHOTO = "PHOTO"
    NATIVE = "NATIVE"


class ChromaDBCollection(str, Enum):
    """ChromaDB collection types for receipt embeddings."""

    LINES = "lines"
    WORDS = "words"
