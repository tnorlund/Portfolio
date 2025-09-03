"""
Data models for receipt analysis.
Extracted from costco_label_discovery.py to improve modularity.
"""

from enum import Enum
from typing import List, Dict, Optional
from dataclasses import dataclass
from pydantic import BaseModel, Field


class LabelType(str, Enum):
    """Label types for currency amounts and line-item components."""

    # Currency labels
    GRAND_TOTAL = "GRAND_TOTAL"
    TAX = "TAX"
    LINE_TOTAL = "LINE_TOTAL"
    SUBTOTAL = "SUBTOTAL"

    # Line-item component labels
    PRODUCT_NAME = "PRODUCT_NAME"
    QUANTITY = "QUANTITY"
    UNIT_PRICE = "UNIT_PRICE"


@dataclass
class ReceiptTextGroup:
    """A group of visually contiguous receipt lines."""

    line_ids: List[int]
    text: str
    centroid_y: float  # Y coordinate of the visual group center


@dataclass
class SpatialMarker:
    """Spatial position marker for receipt analysis."""

    position_percent: float  # 0.0 = top, 1.0 = bottom
    description: str  # "TOP", "MIDDLE", "BOTTOM"


class CurrencyLabel(BaseModel):
    """A discovered currency label with LLM reasoning."""

    word_text: str = Field(description="The exact text of the currency amount")
    label_type: LabelType = Field(description="The classified label type")
    line_number: int = Field(description="Line number in the receipt")
    line_ids: List[int] = Field(
        default_factory=list,
        description="Underlying OCR line_id values contributing to this group",
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confidence in this classification"
    )
    reasoning: str = Field(
        description="Explanation for why this classification was chosen"
    )
    value: float = Field(description="Numeric value extracted from the text")
    position_y: float = Field(
        description="Relative position on receipt (0.0=top, 1.0=bottom)"
    )
    context: str = Field(description="Surrounding text context")


class CurrencyClassificationItem(BaseModel):
    """Individual currency classification item."""

    amount: float = Field(description="The currency amount value")
    label_type: LabelType = Field(description="The predicted label type")
    line_number: int = Field(description="Line number in the receipt")
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confidence in this classification"
    )
    reasoning: str = Field(
        description="Explanation for why this classification was chosen"
    )
    position_context: str = Field(
        description="Spatial context (TOP, MIDDLE, BOTTOM of receipt)"
    )
    text_context: str = Field(description="Surrounding text context")


class CurrencyClassificationResponse(BaseModel):
    """Structured response for currency classification."""

    classifications: List[CurrencyClassificationItem] = Field(
        description="List of classified currency amounts"
    )


class ReceiptAnalysis(BaseModel):
    """Complete analysis results for a receipt."""

    receipt_id: str
    known_total: float
    discovered_labels: List[CurrencyLabel]
    validation_results: (
        Dict  # Allow any values including None for missing validations
    )
    total_lines: int
    confidence_score: float
    formatted_text: str
    processing_time: Optional[float] = Field(
        default=None, description="Processing time in seconds"
    )
