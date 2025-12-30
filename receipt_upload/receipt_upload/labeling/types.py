"""
Shared types for the unified labeling pipeline.

This module defines the data structures passed between pipeline stages.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional


class LabelConfidence(Enum):
    """Confidence level for label predictions."""

    HIGH = "high"  # > 0.9
    MEDIUM = "medium"  # 0.7 - 0.9
    LOW = "low"  # < 0.7


class ValidationStatus(Enum):
    """Status of label validation."""

    PENDING = "pending"
    VALIDATED = "validated"
    CORRECTED = "corrected"
    REJECTED = "rejected"


@dataclass
class WordLabel:
    """Label prediction for a single word."""

    line_id: int
    word_id: int
    text: str
    label: str
    confidence: float
    all_probabilities: Optional[Dict[str, float]] = None
    validation_status: ValidationStatus = ValidationStatus.PENDING
    source: str = "layoutlm"  # layoutlm, chroma, manual


@dataclass
class LabelCorrection:
    """Proposed correction from validation stages."""

    line_id: int
    word_id: int
    original_label: str
    corrected_label: str
    confidence: float
    reason: str
    source: str  # chroma, places, financial


@dataclass
class LayoutLMResult:
    """Result from LayoutLM inference stage."""

    image_id: str
    receipt_id: int
    word_labels: List[WordLabel]
    model_metadata: Dict[str, Any] = field(default_factory=dict)
    inference_time_ms: float = 0.0


@dataclass
class ChromaResult:
    """Result from ChromaDB validation stage."""

    similar_receipts: List[Dict[str, Any]]
    label_corrections: List[LabelCorrection]
    embeddings_created: bool = False
    validation_time_ms: float = 0.0


@dataclass
class PlacesResult:
    """Result from Google Places validation stage."""

    place_id: Optional[str] = None
    merchant_name: Optional[str] = None
    formatted_address: Optional[str] = None
    phone_number: Optional[str] = None
    business_types: List[str] = field(default_factory=list)
    label_corrections: List[LabelCorrection] = field(default_factory=list)
    lookup_method: Optional[str] = None  # phone, address, text_search
    validation_time_ms: float = 0.0


@dataclass
class FinancialResult:
    """Result from financial validation stage."""

    currency: Optional[str] = None
    is_valid: bool = True
    grand_total: Optional[float] = None
    subtotal: Optional[float] = None
    tax: Optional[float] = None
    label_corrections: List[LabelCorrection] = field(default_factory=list)
    issues: List[Dict[str, Any]] = field(default_factory=list)
    validation_time_ms: float = 0.0


@dataclass
class PipelineResult:
    """Final result from the unified labeling pipeline."""

    image_id: str
    receipt_id: int
    final_labels: List[WordLabel]
    layoutlm_result: LayoutLMResult
    chroma_result: ChromaResult
    places_result: PlacesResult
    financial_result: FinancialResult
    total_time_ms: float = 0.0
    stages_completed: List[str] = field(default_factory=list)
    errors: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class PipelineContext:
    """Context passed through the pipeline stages."""

    image_id: str
    receipt_id: int
    receipt_text: str
    words: List[Dict[str, Any]]
    lines: List[Dict[str, Any]]
    # Clients injected from caller
    dynamo_client: Any = None
    chroma_client: Any = None
    places_client: Any = None
    embed_fn: Any = None
    layoutlm_inference: Any = None
