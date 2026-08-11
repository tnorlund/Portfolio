"""
This module defines standardized status enums for DynamoDB entities related to
receipt parsing, labeling, embedding, and batch processing.
"""

from enum import Enum
from typing import Any


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
    """Types of receipt sections for classification.

    The canonical vocabulary (font-intelligence epic, stylescan's ten
    sections) is the lower block. The three LEGACY values are from a
    superseded 2025-05 classifier experiment and are deprecated — kept only so
    any stray legacy rows still parse. ``FOOTER`` is retained and reused as the
    canonical footer. ``TRANSACTION_INFO`` was added later from the D2
    blind validation (2026-07); it is canonical but non-stylescan.
    """

    # --- legacy (deprecated: superseded 2025-05 experiment) ---
    HEADER = "HEADER"  # deprecated -> STOREFRONT / ADDRESS
    ITEMS_VALUE = "ITEMS_VALUE"  # deprecated -> ITEMS
    ITEMS_DESCRIPTION = "ITEMS_DESCRIPTION"  # deprecated -> ITEMS

    # --- canonical vocabulary (epic sec. 4.1) ---
    STOREFRONT = "STOREFRONT"  # merchant name / logo / hours / lane header
    ADDRESS = "ADDRESS"  # street address + store phone
    ITEMS = "ITEMS"  # purchased line items
    SECTION_HEADER = "SECTION_HEADER"  # department dividers
    SUMMARY = "SUMMARY"  # subtotal / tax / discounts / item counts
    TOTAL_LINE = "TOTAL_LINE"  # the grand-total line
    PAYMENT = "PAYMENT"  # tender: card / auth / change / cash back
    SURVEY = "SURVEY"  # post-purchase survey / sweepstakes
    FOOTER = "FOOTER"  # thank-you / policy / rewards / register metadata
    BARCODE = "BARCODE"  # numeric barcode captions
    # --- D2-measured addition (2026-07 blind validation) ---
    # Operator / register / date-time / order-id / invoice metadata lines
    # that fit none of the ten stylescan sections. Present so rows can be
    # created and validated; the semi-Markov decoder will not emit it
    # until the section-order priors are rebuilt to include it.
    TRANSACTION_INFO = "TRANSACTION_INFO"


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
    REGIONAL_REOCR = "REGIONAL_REOCR"
    # Second worker pass: re-decode line items on the Mac worker once the
    # receipt's summary exists, so the graded baseline and zone-gap
    # boundary extension run on device. The job's s3_key points at the
    # receipt's ORIGINAL OCR-result JSON (not an image): the refine pass
    # must decode over the same word universe as the persisted rows.
    LINE_ITEM_REFINE = "LINE_ITEM_REFINE"


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
    "TIP": "Gratuity or tip amount added by customer.",
    "GRAND_TOTAL": "Final amount due after all discounts, taxes and fees.",
    # ── Payment-related ────────────────────────────────────────
    # Added to prevent mislabeling as LINE_TOTAL in training data.
    "CHANGE": "Change amount returned to the customer after transaction.",
    "CASH_BACK": "Cash back amount dispensed from purchase.",
    "REFUND": "Refund amount (full or partial return).",
}

# Sorted tuple of the canonical label names.  Used to declare the allowed
# values of a `label` argument (e.g. an MCP tool's JSON-Schema ``enum``) so a
# caller is told what is legal instead of discovering it from a stack trace.
CORE_LABEL_NAMES: tuple[str, ...] = tuple(sorted(CORE_LABELS))

# Soft aliases: label names that writers commonly emit but that are not
# themselves canonical.  Rewriting one of these to its CORE_LABELS target is
# lossless and unambiguous.  A label that is neither a core label nor a known
# alias is refused, never guessed at -- guessing is how 72 distinct malformed
# label strings ended up as real DynamoDB sort keys.
NON_CORE_LABEL_ALIASES: dict[str, str] = {
    "ADDRESS": "ADDRESS_LINE",
    "BUSINESS_NAME": "MERCHANT_NAME",
    "CARD_NUMBER": "PAYMENT_METHOD",
    "PAYMENT_TYPE": "PAYMENT_METHOD",
}


def canonical_label_name(label: Any) -> str:
    """Normalize a model or stored label into the Dynamo label format."""
    if label is None:
        return ""
    return str(label).strip().upper()


def is_core_label(label: Any) -> bool:
    """Return whether a label is part of the canonical receipt label set."""
    return canonical_label_name(label) in CORE_LABELS


def normalize_label_alias(label: Any) -> str | None:
    """Map known non-core aliases to CORE_LABELS, if the mapping is safe.

    Returns ``None`` when the label is neither a core label nor a known
    alias.  Callers minting a *new* label row must treat ``None`` as a
    refusal; they must not fall back to the raw string.
    """
    canonical = canonical_label_name(label)
    if canonical in CORE_LABELS:
        return canonical
    return NON_CORE_LABEL_ALIASES.get(canonical)


def invalid_label_message(label: Any) -> str:
    """Build the caller-facing message for a label outside the vocabulary."""
    canonical = canonical_label_name(label)
    message = (
        f"Invalid label {canonical!r}: label must be one of "
        f"{list(CORE_LABEL_NAMES)}"
    )
    suggestion = NON_CORE_LABEL_ALIASES.get(canonical)
    if suggestion is not None:
        message += f". Did you mean {suggestion!r}?"
    return message


def normalize_core_label(label: Any) -> str:
    """Return the canonical CORE_LABELS name for ``label``.

    Accepts a core label in any casing and the soft aliases in
    ``NON_CORE_LABEL_ALIASES``.  Raises ``ValueError`` for anything else.

    This is the *authoring* guard: use it wherever a label name arrives as
    free text (an agent argument, an LLM response, a CLI flag) and BEFORE a
    ``ReceiptWordLabel`` is constructed, because the label name becomes part
    of the DynamoDB sort key.  Do NOT use it on a label read back out of
    DynamoDB -- 394 production rows carry historical labels outside this
    vocabulary and must stay readable.
    """
    normalized = normalize_label_alias(label)
    if normalized is None:
        raise ValueError(invalid_label_message(label))
    return normalized
