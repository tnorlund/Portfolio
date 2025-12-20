"""Type definitions for Label Evaluator Step Functions.

This module provides TypedDict definitions for structured data passed between
Lambda handlers and stored in S3. Using TypedDicts instead of dict[str, Any]
improves type safety, IDE support, and documentation.

Usage:
    from types import ReceiptRef, EvaluationResult, IssueDetail
"""

from typing import NotRequired, TypedDict


# =============================================================================
# Core Receipt Types
# =============================================================================


class ReceiptRef(TypedDict):
    """Reference to a receipt for processing."""

    image_id: str
    receipt_id: int
    merchant_name: str


class Coordinate(TypedDict):
    """2D coordinate point."""

    x: float
    y: float


class BoundingBox(TypedDict):
    """Bounding box for OCR geometry."""

    x: float
    y: float
    width: float
    height: float


# =============================================================================
# Serialized Entity Types (S3 storage format)
# =============================================================================


class SerializedWord(TypedDict):
    """ReceiptWord serialized for S3/JSON storage."""

    image_id: str
    receipt_id: int
    line_id: int
    word_id: int
    text: str
    bounding_box: BoundingBox
    top_right: Coordinate
    top_left: Coordinate
    bottom_right: Coordinate
    bottom_left: Coordinate
    angle_degrees: float
    angle_radians: float
    confidence: float
    extracted_data: NotRequired[dict | None]
    embedding_status: NotRequired[str]
    is_noise: NotRequired[bool]


class SerializedLabel(TypedDict):
    """ReceiptWordLabel serialized for S3/JSON storage."""

    image_id: str
    receipt_id: int
    line_id: int
    word_id: int
    label: str
    reasoning: str | None
    timestamp_added: str  # ISO format datetime string
    validation_status: NotRequired[str | None]
    label_proposed_by: NotRequired[str | None]
    label_consolidated_from: NotRequired[str | None]


class SerializedPlace(TypedDict, total=False):
    """ReceiptPlace serialized for S3/JSON storage.

    Uses total=False since most fields are optional.
    """

    # Required
    image_id: str
    receipt_id: int
    place_id: str
    merchant_name: str

    # Optional location
    formatted_address: str
    short_address: str
    latitude: float | None
    longitude: float | None

    # Optional contact
    phone_number: str
    website: str

    # Optional validation
    validation_status: str
    confidence: float
    timestamp: str  # ISO format


# =============================================================================
# Evaluation Issue Types
# =============================================================================


class IssueDetail(TypedDict):
    """Details of a detected labeling issue."""

    type: str  # "position_anomaly", "same_line_conflict", etc.
    word_text: str
    word_id: NotRequired[int]
    line_id: NotRequired[int]
    current_label: str | None
    suggested_status: str  # "VALID", "INVALID", "NEEDS_REVIEW"
    suggested_label: str | None
    reasoning: str


class EvaluationResult(TypedDict):
    """Result from evaluate_labels Lambda."""

    image_id: str
    receipt_id: int
    issues_found: int
    issues: list[IssueDetail]
    error: NotRequired[str | None]
    merchant_receipts_analyzed: NotRequired[int]
    label_types_found: NotRequired[int]


class ReceiptResultSummary(TypedDict):
    """Summary of a single receipt evaluation (for aggregation)."""

    status: str  # "completed", "error"
    image_id: str
    receipt_id: int
    issues_found: int
    results_s3_key: NotRequired[str]
    error: NotRequired[str]


# =============================================================================
# Handler Input Types
# =============================================================================


class ListMerchantsInput(TypedDict, total=False):
    """Input for list_merchants Lambda."""

    execution_id: str
    batch_bucket: str
    min_receipts: int
    max_training_receipts: int
    skip_llm_review: bool


class ListReceiptsInput(TypedDict, total=False):
    """Input for list_receipts Lambda."""

    execution_id: str
    batch_bucket: str
    batch_size: int
    merchant_name: str  # Required
    limit: int
    max_training_receipts: int


class FetchReceiptDataInput(TypedDict):
    """Input for fetch_receipt_data Lambda."""

    receipt: ReceiptRef
    execution_id: str
    batch_bucket: str


class EvaluateLabelsInput(TypedDict):
    """Input for evaluate_labels Lambda."""

    data_s3_key: str
    patterns_s3_key: NotRequired[str]
    execution_id: str
    batch_bucket: str


class ComputePatternsInput(TypedDict, total=False):
    """Input for compute_patterns Lambda."""

    execution_id: str
    batch_bucket: str
    merchant_name: str  # Required
    max_training_receipts: int


class AggregateResultsInput(TypedDict, total=False):
    """Input for aggregate_results Lambda."""

    execution_id: str
    batch_bucket: str
    process_results: list  # Nested batch results
    merchant_name: str
    dry_run: bool


class LLMReviewInput(TypedDict):
    """Input for llm_review Lambda."""

    results_s3_key: str
    execution_id: str
    batch_bucket: str


# =============================================================================
# Handler Output Types
# =============================================================================


class MerchantInfo(TypedDict):
    """Merchant info from list_merchants."""

    merchant_name: str
    receipt_count: int


class ListMerchantsOutput(TypedDict):
    """Output from list_merchants Lambda."""

    merchants: list[MerchantInfo]
    total_merchants: int
    total_receipts: int
    skipped_merchants: int
    min_receipts: int
    max_training_receipts: int
    skip_llm_review: bool
    manifest_s3_key: str


class ListReceiptsOutput(TypedDict):
    """Output from list_receipts Lambda."""

    manifest_s3_key: str | None
    total_receipts: int
    total_batches: int
    merchant_name: str
    max_training_receipts: int
    receipt_batches: list[list[ReceiptRef]]


class FetchReceiptDataOutput(TypedDict):
    """Output from fetch_receipt_data Lambda."""

    data_s3_key: str
    image_id: str
    receipt_id: int
    word_count: int
    label_count: int


class EvaluateLabelsOutput(TypedDict):
    """Output from evaluate_labels Lambda."""

    status: str  # "completed", "error"
    results_s3_key: NotRequired[str]
    image_id: str
    receipt_id: int
    issues_found: int
    compute_time_seconds: NotRequired[float]
    error: NotRequired[str]


class ComputePatternsOutput(TypedDict):
    """Output from compute_patterns Lambda."""

    patterns_s3_key: str
    merchant_name: str
    receipt_count: int
    pattern_stats: NotRequired[dict | None]
    compute_time_seconds: NotRequired[float]


class AggregateResultsOutput(TypedDict):
    """Output from aggregate_results Lambda."""

    execution_id: str
    summary: dict  # Nested summary stats
    report_s3_key: str
    issues_s3_key: str


class LLMReviewOutput(TypedDict):
    """Output from llm_review Lambda."""

    status: str
    results_s3_key: str
    image_id: str
    receipt_id: int
    issues_reviewed: int
    reviews_completed: int
    error: NotRequired[str]


# =============================================================================
# S3 Data File Types
# =============================================================================


class ReceiptDataFile(TypedDict):
    """Structure of receipt data file in S3."""

    image_id: str
    receipt_id: int
    words: list[SerializedWord]
    labels: list[SerializedLabel]
    place: SerializedPlace | None


class PatternsFile(TypedDict, total=False):
    """Structure of patterns file in S3."""

    merchant_name: str
    patterns: dict | None  # MerchantPatterns serialized
