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


class JobStatus(str, Enum):
    """Status for Job and JobStatus entities."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


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


class CompactionState(str, Enum):
    """States for ChromaDB compaction runs/delta merges."""

    PENDING = "PENDING"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class CoreMLExportStatus(str, Enum):
    """Status for CoreML export jobs."""

    PENDING = "PENDING"
    RUNNING = "RUNNING"
    SUCCEEDED = "SUCCEEDED"
    FAILED = "FAILED"


# Core receipt label types with descriptions.
# Used for metadata filtering in ChromaDB and RAG queries.
CORE_LABELS: dict[str, str] = {
    # ── Merchant & store info ───────────────────────────────────
    "MERCHANT_NAME": "Trading name or brand of the store issuing the receipt.",
    "STORE_HOURS": "Printed business hours or opening times for the merchant.",
    "PHONE_NUMBER": "Telephone number printed on the receipt (store's main line).",
    "WEBSITE": "Web or email address printed on the receipt (e.g., sprouts.com).",
    "LOYALTY_ID": "Customer loyalty / rewards / membership identifier.",
    # ── Location / address ──────────────────────────────────────
    "ADDRESS_LINE": "Full address line (street + city etc.) printed on the receipt.",
    # ── Transaction info ───────────────────────────────────────
    "DATE": "Calendar date of the transaction.",
    "TIME": "Time of the transaction.",
    "PAYMENT_METHOD": "Payment instrument summary (e.g., VISA ••••1234, CASH).",
    "COUPON": "Coupon code or description that reduces price.",
    "DISCOUNT": "Any non-coupon discount line item (e.g., 10% member discount).",
    # ── Line-item fields ───────────────────────────────────────
    "PRODUCT_NAME": "Descriptive text of a purchased product (item name).",
    "QUANTITY": "Numeric count or weight of the item (e.g., 2, 1.31 lb).",
    "UNIT_PRICE": "Price per single unit / weight before tax.",
    "LINE_TOTAL": "Extended price for that line (quantity x unit price).",
    # ── Totals & taxes ─────────────────────────────────────────
    "SUBTOTAL": "Sum of all line totals before tax and discounts.",
    "TAX": "Any tax line (sales tax, VAT, bottle deposit).",
    "GRAND_TOTAL": "Final amount due after all discounts, taxes and fees.",
    # ── Payment-related ────────────────────────────────────────
    # Added to prevent mislabeling as LINE_TOTAL in training data.
    "CHANGE": "Change amount returned to the customer after transaction.",
    "CASH_BACK": "Cash back amount dispensed from purchase.",
    "REFUND": "Refund amount (full or partial return).",
}
