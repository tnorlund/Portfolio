"""
Data models for receipt analysis.
Extracted from costco_label_discovery.py to improve modularity.
"""

from enum import Enum
from typing import List, Dict, Optional, Union, Any
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


class CurrencyLabelType(str, Enum):
    """Currency labels."""

    GRAND_TOTAL = "GRAND_TOTAL"
    TAX = "TAX"
    LINE_TOTAL = "LINE_TOTAL"
    SUBTOTAL = "SUBTOTAL"


class LineItemLabelType(str, Enum):
    """Line-item component labels."""

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
    """A discovered currency label with LLM reasoning.

    Each currency amount found on the receipt must include all these fields.
    """

    line_text: str = Field(
        ...,
        description="The exact text containing the currency amount as it appears on the receipt line (e.g., '15.02' or '$15.02')"
    )
    amount: float = Field(
        ...,
        description="The numeric currency value as a float (e.g., 15.02 for $15.02, 24.01 for $24.01). Must be a number, not a string.",
        json_schema_extra={"examples": [15.02, 24.01, 0.00]}
    )
    label_type: CurrencyLabelType = Field(
        ...,
        description="One of: GRAND_TOTAL (final amount due), TAX (sales tax), SUBTOTAL (sum before tax), LINE_TOTAL (individual item total)"
    )
    line_ids: List[int] = Field(
        default_factory=list,
        description="Array of line IDs where this amount appears on the receipt (e.g., [16, 17])",
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score from 0.0 (uncertain) to 1.0 (certain) for this classification"
    )
    reasoning: str = Field(
        ...,
        description="Brief explanation of why this classification was chosen (e.g., 'Appears at bottom as AMOUNT', 'Explicitly labeled as TAX')"
    )
    cove_verified: bool = Field(
        default=False,
        description="Whether this label was verified by Chain of Verification (CoVe). If True, validation_status should be set to VALID."
    )


class LineItemLabel(BaseModel):
    """A discovered line-item label with LLM reasoning.

    Each word in a line item that should be classified.
    """

    word_text: str = Field(
        ...,
        description="The exact text of the specific word being labeled (e.g., 'STUFFED', 'PEP', '2', '15.02')"
    )
    label_type: LineItemLabelType = Field(
        ...,
        description="One of: PRODUCT_NAME (item name), QUANTITY (amount/weight), UNIT_PRICE (price per unit)"
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score from 0.0 (uncertain) to 1.0 (certain) for this classification"
    )
    reasoning: str = Field(
        ...,
        description="Brief explanation of why this word was classified this way (e.g., 'Product name comes before price', 'Appears with quantity indicators')"
    )
    cove_verified: bool = Field(
        default=False,
        description="Whether this label was verified by Chain of Verification (CoVe). If True, validation_status should be set to VALID."
    )


class CurrencyClassificationItem(BaseModel):
    """Individual currency classification item."""

    amount: float = Field(description="The currency amount value")
    label_type: CurrencyLabelType = Field(
        description="The predicted label type"
    )
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
    discovered_labels: List[Union[CurrencyLabel, LineItemLabel, Any]]  # Any includes TransactionLabel and others
    validation_results: (
        Dict  # Allow any values including None for missing validations
    )
    total_lines: int
    confidence_score: float
    formatted_text: str
    processing_time: Optional[float] = Field(
        default=None, description="Processing time in seconds"
    )

    # Additional fields for deferred writes (labels and metadata updates)
    receipt_word_labels_to_add: Optional[List[Any]] = Field(default_factory=list)
    receipt_word_labels_to_update: Optional[List[Any]] = Field(default_factory=list)
    metadata_validation: Optional[Dict[str, Any]] = Field(default=None)


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


class Phase1Response(BaseModel):
    """Response model for phase 1.

    This model enforces that all currency amounts are returned as floats.
    Pydantic will automatically parse string amounts to floats.
    """

    currency_labels: List[CurrencyLabel] = Field(
        description="Currency classifications with all amounts as floats (not strings)"
    )
    reasoning: str = Field(description="Overall analysis reasoning")
    confidence: float = Field(
        ge=0.0, le=1.0, description="Overall confidence score"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [{
                "currency_labels": [{
                    "line_text": "24.01",
                    "amount": 24.01,  # float, not "24.01"
                    "label_type": "GRAND_TOTAL",
                    "line_ids": [29],
                    "confidence": 0.95,
                    "reasoning": "Final amount due"
                }],
                "reasoning": "Extracted all currency amounts",
                "confidence": 0.93
            }]
        }
    }


class Phase2Response(BaseModel):
    line_item_labels: List[LineItemLabel] = Field(
        description="Line item classifications (line_ids optional, ignored)"
    )
    reasoning: str = Field(description="Overall analysis reasoning")
    confidence: float = Field(
        ge=0.0, le=1.0, description="Overall confidence score"
    )


# Transaction Context Labels (NEW)
class TransactionLabelType(str, Enum):
    """Transaction context labels from CORE_LABELS."""
    # Transaction-specific
    DATE = "DATE"
    TIME = "TIME"
    PAYMENT_METHOD = "PAYMENT_METHOD"
    COUPON = "COUPON"
    DISCOUNT = "DISCOUNT"
    LOYALTY_ID = "LOYALTY_ID"
    # Merchant info (needed for word labeling, even though in ReceiptMetadata)
    MERCHANT_NAME = "MERCHANT_NAME"
    PHONE_NUMBER = "PHONE_NUMBER"
    ADDRESS_LINE = "ADDRESS_LINE"
    # Store details
    STORE_HOURS = "STORE_HOURS"
    WEBSITE = "WEBSITE"


class TransactionLabel(BaseModel):
    """A discovered transaction context label with LLM reasoning."""

    word_text: str = Field(
        ...,
        description="The exact text of the word being labeled (e.g., '12/25/2024', '14:30', 'VISA', 'SAVE10')"
    )
    label_type: TransactionLabelType = Field(
        ...,
        description="Transaction context label type - descriptions come from CORE_LABELS"
    )
    confidence: float = Field(
        ...,
        ge=0.0,
        le=1.0,
        description="Confidence score from 0.0 (uncertain) to 1.0 (certain) for this classification"
    )
    reasoning: str = Field(
        ...,
        description="Brief explanation of why this word was classified this way (e.g., 'Date format matches MM/DD/YYYY', 'Appears near payment section')"
    )
    cove_verified: bool = Field(
        default=False,
        description="Whether this label was verified by Chain of Verification (CoVe). If True, validation_status should be set to VALID."
    )

    model_config = {
        "json_schema_extra": {
            "examples": [
                {
                    "word_text": "12/25/2024",
                    "label_type": "DATE",
                    "confidence": 0.95,
                    "reasoning": "Date format"
                },
                {
                    "word_text": "CASH",
                    "label_type": "PAYMENT_METHOD",
                    "confidence": 0.90,
                    "reasoning": "Payment type indicator"
                }
            ]
        }
    }


class PhaseContextResponse(BaseModel):
    """Response model for transaction context analysis."""

    transaction_labels: List[TransactionLabel] = Field(
        description="Transaction context classifications (DATE, TIME, PAYMENT_METHOD, etc.)"
    )
    reasoning: str = Field(description="Overall analysis reasoning")
    confidence: float = Field(
        ge=0.0, le=1.0, description="Overall confidence score"
    )

    model_config = {
        "json_schema_extra": {
            "examples": [{
                "transaction_labels": [
                    {
                        "word_text": "12/25/2024",
                        "label_type": "DATE",
                        "confidence": 0.95,
                        "reasoning": "Date format"
                    },
                    {
                        "word_text": "VISA",
                        "label_type": "PAYMENT_METHOD",
                        "confidence": 0.90,
                        "reasoning": "Payment method"
                    }
                ],
                "reasoning": "Extracted transaction context",
                "confidence": 0.93
            }]
        }
    }
