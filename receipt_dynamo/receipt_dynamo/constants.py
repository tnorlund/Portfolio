from enum import Enum

"""
This module defines standardized status enums for DynamoDB entities related to
receipt parsing, labeling, embedding, and batch processing.
"""


class ValidationStatus(str, Enum):
    """Standardized validation state for receipt word labels."""

    NONE = "NONE"  # No validation has ever been initiated
    PENDING = "PENDING"  # Validation has been queued
    VALID = "VALID"  # Validation succeeded
    INVALID = "INVALID"  # Validation rejected
    NEEDS_AGENT_REVIEW = "NEEDS_AGENT_REVIEW"  # Validation needs agent review


class BatchStatus(str, Enum):
    """States for batch job execution."""

    PENDING = "PENDING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class BatchType(str, Enum):
    """Types of batch jobs for label processing."""

    COMPLETION = "COMPLETION"
    EMBEDDING = "EMBEDDING"


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
