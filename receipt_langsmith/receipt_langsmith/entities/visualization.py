"""Visualization data structures for receipt scanner UI.

This module defines schemas for visualization cache data used by
the LabelEvaluatorVisualization React component.
"""

from typing import Any

from pydantic import BaseModel, Field


class DecisionCounts(BaseModel):
    """Counts of VALID/INVALID/NEEDS_REVIEW decisions.

    Used to summarize LLM review outcomes for currency, metadata,
    and financial evaluations.
    """

    model_config = {"extra": "ignore"}

    VALID: int = 0
    """Number of VALID decisions."""

    INVALID: int = 0
    """Number of INVALID decisions."""

    NEEDS_REVIEW: int = 0
    """Number of NEEDS_REVIEW decisions."""


class EvaluatorResult(BaseModel):
    """Result from a single evaluator (currency/metadata/financial).

    Contains decision counts, detailed decisions, and timing information.
    """

    model_config = {"extra": "ignore"}

    decisions: DecisionCounts = Field(default_factory=DecisionCounts)
    """Aggregated decision counts."""

    all_decisions: list[dict[str, Any]] = Field(default_factory=list)
    """Full list of individual decisions with reasoning."""

    duration_seconds: float = 0.0
    """Execution duration in seconds."""


class GeometricResult(BaseModel):
    """Result from geometric anomaly detection.

    Contains position and constellation anomalies found.
    """

    model_config = {"extra": "ignore"}

    issues_found: int = 0
    """Count of geometric issues detected."""

    issues: list[dict[str, Any]] = Field(default_factory=list)
    """List of geometric issues with details."""

    duration_seconds: float = 0.0
    """Detection duration in seconds."""


class ReceiptIdentifier(BaseModel):
    """Base receipt identification fields.

    Common fields used across all receipt-related structures.
    """

    model_config = {"extra": "ignore"}

    image_id: str | None = None
    """Receipt image UUID."""

    receipt_id: int | None = None
    """Receipt number within image (1-indexed)."""

    merchant_name: str = "Unknown"
    """Merchant name from receipt."""

    execution_id: str = ""
    """Step Function execution ID for batch processing."""


class EvaluatorTiming(BaseModel):
    """Timing information for a single evaluator.

    Contains start offset and duration relative to parent trace.
    """

    model_config = {"extra": "ignore"}

    start_ms: int = 0
    """Milliseconds from parent start to evaluator start."""

    duration_ms: int = 0
    """Execution duration in milliseconds."""


class ReceiptWithDecisions(ReceiptIdentifier):
    """Receipt with LLM decision data for visualization.

    Used by find_receipts_with_decisions_from_parquet.
    """

    run_id: str = ""
    """Parent trace run ID."""

    timing: dict[str, EvaluatorTiming] = Field(default_factory=dict)
    """Evaluator timings by name (EvaluateCurrencyLabels, etc.)."""

    currency_decisions: list[dict[str, Any]] = Field(default_factory=list)
    """LLM decisions for currency labels."""

    metadata_decisions: list[dict[str, Any]] = Field(default_factory=list)
    """LLM decisions for metadata labels."""

    financial_decisions: list[dict[str, Any]] = Field(default_factory=list)
    """LLM decisions for financial validation."""


class ReceiptWithAnomalies(ReceiptIdentifier):
    """Receipt with geometric anomaly data.

    Used by find_receipts_with_anomalies_from_parquet.
    """

    run_id: str = ""
    """Parent trace run ID."""

    issues: list[dict[str, Any]] = Field(default_factory=list)
    """Geometric issues filtered by anomaly type."""

    all_issues: list[dict[str, Any]] = Field(default_factory=list)
    """All issues from EvaluateLabels."""

    flagged_words: list[dict[str, Any]] = Field(default_factory=list)
    """Words flagged for review."""


class VisualizationReceipt(ReceiptIdentifier):
    """Complete receipt data for LabelEvaluatorVisualization.

    Combines geometric, currency, metadata, financial, and review results
    with timing information for the scanner UI.
    """

    geometric: GeometricResult = Field(default_factory=GeometricResult)
    """Geometric anomaly detection results."""

    currency: EvaluatorResult = Field(default_factory=EvaluatorResult)
    """Currency label evaluation results."""

    metadata: EvaluatorResult = Field(default_factory=EvaluatorResult)
    """Metadata label evaluation results."""

    financial: EvaluatorResult = Field(default_factory=EvaluatorResult)
    """Financial math validation results."""

    review: EvaluatorResult | None = None
    """LLM review results (None if not reviewed)."""

    line_item_duration_seconds: float | None = None
    """Duration of DiscoverPatterns span."""


class BoundingBox(BaseModel):
    """Bounding box for word position on receipt image."""

    model_config = {"extra": "ignore"}

    x: float = 0.0
    """X coordinate (normalized 0-1)."""

    y: float = 0.0
    """Y coordinate (normalized 0-1)."""

    width: float = 0.0
    """Width (normalized 0-1)."""

    height: float = 0.0
    """Height (normalized 0-1)."""


class WordWithLabel(BaseModel):
    """Word with bounding box and label for visualization.

    Used in viz_cache_job to include word positions for overlay rendering.
    """

    model_config = {"extra": "ignore"}

    text: str = ""
    """Word text content."""

    label: str | None = None
    """Assigned label (MERCHANT_NAME, PRICE, etc.)."""

    line_id: int | None = None
    """Line ID within receipt."""

    word_id: int | None = None
    """Word ID within line."""

    bbox: BoundingBox = Field(default_factory=BoundingBox)
    """Bounding box with x, y, width, height."""


class VizCacheReceipt(ReceiptIdentifier):
    """Receipt entry for visualization cache JSON.

    Complete data structure written to viz-cache-{timestamp}.json,
    including words with positions for overlay rendering.
    """

    issues_found: int = 0
    """Total issues across all evaluators."""

    words: list[WordWithLabel] = Field(default_factory=list)
    """Words with bounding boxes and labels."""

    geometric: GeometricResult = Field(default_factory=GeometricResult)
    """Geometric evaluation results."""

    currency: EvaluatorResult = Field(default_factory=EvaluatorResult)
    """Currency evaluation results."""

    metadata: EvaluatorResult = Field(default_factory=EvaluatorResult)
    """Metadata evaluation results."""

    financial: EvaluatorResult = Field(default_factory=EvaluatorResult)
    """Financial evaluation results."""

    cdn_key: str = ""
    """CloudFront CDN key for receipt image."""
