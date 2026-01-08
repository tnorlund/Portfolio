"""LabelEvaluator trace schemas.

This module defines schemas for parsing LabelEvaluator trace inputs/outputs
from LangSmith exports, including child traces like EvaluateCurrencyLabels,
EvaluateMetadataLabels, ValidateFinancialMath, and LLMReview.
"""

from typing import Any, Optional

from pydantic import BaseModel, Field


class DrillDownWordTrace(BaseModel):
    """Per-word deviation analysis within a constellation anomaly."""

    word_id: int
    """Word ID within the receipt."""

    line_id: int
    """Line ID containing the word."""

    text: str
    """Word text content."""

    position: tuple[float, float] = (0.0, 0.0)
    """(x, y) normalized coordinates."""

    expected_offset: tuple[float, float] = (0.0, 0.0)
    """(dx, dy) expected offset from centroid."""

    actual_offset: tuple[float, float] = (0.0, 0.0)
    """(dx, dy) actual offset from centroid."""

    deviation: float = 0.0
    """Euclidean distance from expected position."""

    is_culprit: bool = False
    """True if deviation exceeds 2σ threshold."""


class EvaluationIssueTrace(BaseModel):
    """An issue detected during label evaluation.

    This represents a flagged word/label that may need review.
    """

    issue_type: str
    """Issue type: 'position_anomaly', 'geometric_anomaly',
    'constellation_anomaly', 'same_line_conflict', etc."""

    type: Optional[str] = None
    """Alias for issue_type (some traces use 'type' instead)."""

    word_text: Optional[str] = None
    """Text of the flagged word."""

    line_id: Optional[int] = None
    """Line ID containing the word."""

    word_id: Optional[int] = None
    """Word ID within the receipt."""

    current_label: Optional[str] = None
    """The label being evaluated."""

    suggested_status: str = "NEEDS_REVIEW"
    """Suggested status: 'VALID', 'INVALID', or 'NEEDS_REVIEW'."""

    reasoning: str = ""
    """Human-readable explanation of the issue."""

    suggested_label: Optional[str] = None
    """Suggested correct label if INVALID."""

    drill_down: Optional[list[DrillDownWordTrace]] = None
    """Per-word deviation data for constellation anomalies."""

    # Additional fields from trace outputs
    label_pair: Optional[tuple[str, str]] = None
    """Label pair involved in geometric anomaly."""

    deviation_sigma: Optional[float] = None
    """Deviation in standard deviations."""


class LLMReviewTrace(BaseModel):
    """LLM review decision for a flagged word."""

    decision: str
    """Decision: 'VALID', 'INVALID', or 'NEEDS_REVIEW'."""

    reasoning: str = ""
    """LLM's explanation for the decision."""

    suggested_label: Optional[str] = None
    """Suggested label if decision is INVALID."""


class ReviewDecisionTrace(BaseModel):
    """Combined review decision with word context.

    This is the format returned by EvaluateCurrencyLabels,
    EvaluateMetadataLabels, and ValidateFinancialMath.
    """

    word_text: str
    """Text of the reviewed word."""

    line_id: int
    """Line ID containing the word."""

    word_id: int
    """Word ID within the receipt."""

    original_label: Optional[str] = None
    """Original label before evaluation."""

    llm_review: Optional[LLMReviewTrace] = None
    """LLM review result."""

    # Flattened fields (some traces have these at top level)
    decision: Optional[str] = None
    """Decision if not nested in llm_review."""

    reasoning: Optional[str] = None
    """Reasoning if not nested in llm_review."""

    suggested_label: Optional[str] = None
    """Suggested label if not nested in llm_review."""


class FlaggedWordTrace(BaseModel):
    """A word flagged for evaluation.

    Contains position and label information for visualization.
    """

    word_id: int
    """Word ID within the receipt."""

    line_id: int
    """Line ID containing the word."""

    text: str
    """Word text content."""

    x: float
    """Normalized x position (0-1)."""

    y: float
    """Normalized y position (0-1)."""

    width: float
    """Normalized width."""

    height: float
    """Normalized height."""

    label: Optional[str] = None
    """Current label."""

    issue_type: Optional[str] = None
    """Type of issue flagged."""


# Input schemas for each trace type


class LabelEvaluatorInputs(BaseModel):
    """Inputs to main LabelEvaluator trace."""

    image_id: str
    """Receipt image ID (UUID)."""

    receipt_id: int
    """Receipt ID within image (1-indexed)."""

    skip_llm_review: bool = False
    """If True, skip LLM review and use evaluator results directly."""

    skip_geometry: bool = False
    """If True, skip expensive geometric anomaly detection."""


class EvaluateLabelsInputs(BaseModel):
    """Inputs to EvaluateLabels child trace (geometric evaluation)."""

    image_id: str
    receipt_id: int
    data_s3_key: Optional[str] = None
    """S3 key for input data (batch processing mode)."""


class CurrencyEvaluatorInputs(BaseModel):
    """Inputs to EvaluateCurrencyLabels child trace."""

    image_id: str
    receipt_id: int
    data_s3_key: Optional[str] = None


class MetadataEvaluatorInputs(BaseModel):
    """Inputs to EvaluateMetadataLabels child trace."""

    image_id: str
    receipt_id: int
    data_s3_key: Optional[str] = None


class FinancialEvaluatorInputs(BaseModel):
    """Inputs to ValidateFinancialMath child trace."""

    image_id: str
    receipt_id: int
    data_s3_key: Optional[str] = None


# Output schemas for each trace type


class EvaluateLabelsOutputs(BaseModel):
    """Outputs from EvaluateLabels child trace (geometric evaluation)."""

    issues: list[EvaluationIssueTrace] = Field(default_factory=list)
    """Geometric and position anomalies found."""

    flagged_words: list[FlaggedWordTrace] = Field(default_factory=list)
    """Words flagged for evaluation."""

    status: str = ""
    """Completion status."""

    results_s3_key: Optional[str] = None
    """S3 key for output results (batch mode)."""


class CurrencyEvaluatorOutputs(BaseModel):
    """Outputs from EvaluateCurrencyLabels child trace."""

    all_decisions: list[ReviewDecisionTrace] = Field(default_factory=list)
    """LLM review decisions for currency labels."""

    currency_words_evaluated: int = 0
    """Number of currency words evaluated."""

    decisions: dict[str, int] = Field(default_factory=dict)
    """Decision counts: {'VALID': n, 'INVALID': m, 'NEEDS_REVIEW': k}."""

    status: str = ""
    """Completion status."""

    results_s3_key: Optional[str] = None


class MetadataEvaluatorOutputs(BaseModel):
    """Outputs from EvaluateMetadataLabels child trace."""

    all_decisions: list[ReviewDecisionTrace] = Field(default_factory=list)
    """LLM review decisions for metadata labels."""

    metadata_words_evaluated: int = 0
    """Number of metadata words evaluated."""

    decisions: dict[str, int] = Field(default_factory=dict)
    """Decision counts."""

    status: str = ""
    results_s3_key: Optional[str] = None


class FinancialEvaluatorOutputs(BaseModel):
    """Outputs from ValidateFinancialMath child trace."""

    all_decisions: list[ReviewDecisionTrace] = Field(default_factory=list)
    """LLM review decisions for financial labels."""

    financial_issues: list[dict[str, Any]] = Field(default_factory=list)
    """Financial math issues found."""

    status: str = ""
    results_s3_key: Optional[str] = None


class LLMReviewOutputs(BaseModel):
    """Outputs from LLMReview child trace."""

    reviewed_issues: list[dict[str, Any]] = Field(default_factory=list)
    """Issues that were reviewed by LLM."""

    review_count: int = 0
    """Number of issues reviewed."""


class LabelEvaluatorOutputs(BaseModel):
    """Outputs from main LabelEvaluator trace (ReceiptEvaluation).

    This aggregates results from all child evaluators.
    """

    image_id: str = ""
    """Receipt image ID."""

    receipt_id: int = 0
    """Receipt ID within image."""

    status: str = ""
    """Completion status: 'completed', 'error', etc."""

    error: Optional[str] = None
    """Error message if status is 'error'."""

    # Child trace outputs (may be populated or None)
    evaluate_labels: Optional[EvaluateLabelsOutputs] = None
    currency_evaluation: Optional[CurrencyEvaluatorOutputs] = None
    metadata_evaluation: Optional[MetadataEvaluatorOutputs] = None
    financial_evaluation: Optional[FinancialEvaluatorOutputs] = None
    llm_review: Optional[LLMReviewOutputs] = None
