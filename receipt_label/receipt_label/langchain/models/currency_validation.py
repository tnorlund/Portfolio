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


class CurrencyItem(BaseModel):
    """Single currency amount with classification."""

    amount: float = Field(description="The currency amount (e.g., 25.99)")
    label_type: str = Field(
        description="GRAND_TOTAL, TAX, LINE_TOTAL, or SUBTOTAL"
    )
    confidence: float = Field(
        ge=0.0, le=1.0, description="Confidence in classification"
    )
    reasoning: str = Field(description="Why this classification was chosen")
    line_ids: List[int] = Field(
        description="Line IDs where this amount was found"
    )
    word_text: str = Field(description="The exact text containing this amount")


class LineItem(BaseModel):
    """Single line item with all components."""

    product_name: Optional[str] = Field(
        default=None, description="Product description"
    )
    quantity: Optional[str] = Field(
        default=None, description="Quantity (e.g., '2 lb', '3')"
    )
    unit_price: Optional[float] = Field(
        default=None, description="Price per unit"
    )
    line_total: Optional[float] = Field(
        default=None, description="Extended total for this line"
    )
    confidence: float = Field(ge=0.0, le=1.0, description="Overall confidence")
    reasoning: str = Field(description="Explanation of the analysis")


class SimpleReceiptResponse(BaseModel):
    """Unified response model for both phases."""

    currency_amounts: List[CurrencyItem] = Field(
        description="Currency classifications"
    )
    line_items: List[LineItem] = Field(description="Product line items")
    reasoning: str = Field(description="Overall analysis reasoning")
    confidence: float = Field(
        ge=0.0, le=1.0, description="Overall confidence score"
    )
