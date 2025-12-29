"""
Structured output models for LLM calls in the label evaluator.

These models are used with LangChain's `with_structured_output()` to constrain
LLM responses at the token generation level, preventing invalid labels like "OTHER".

Usage:
    llm_with_structure = llm.with_structured_output(BatchedReviewResponse)
    response = llm_with_structure.invoke(prompt)
    # response is already a BatchedReviewResponse object
"""

from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field

# =============================================================================
# Shared Enums
# =============================================================================


class DecisionEnum(str, Enum):
    """LLM decision for label validation."""

    VALID = "VALID"
    INVALID = "INVALID"
    NEEDS_REVIEW = "NEEDS_REVIEW"


class ConfidenceEnum(str, Enum):
    """Confidence level for LLM decisions."""

    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class LabelEnum(str, Enum):
    """Valid receipt labels - matches CORE_LABELS_SET from constants.py."""

    MERCHANT_NAME = "MERCHANT_NAME"
    STORE_HOURS = "STORE_HOURS"
    PHONE_NUMBER = "PHONE_NUMBER"
    WEBSITE = "WEBSITE"
    LOYALTY_ID = "LOYALTY_ID"
    ADDRESS_LINE = "ADDRESS_LINE"
    DATE = "DATE"
    TIME = "TIME"
    PAYMENT_METHOD = "PAYMENT_METHOD"
    COUPON = "COUPON"
    DISCOUNT = "DISCOUNT"
    PRODUCT_NAME = "PRODUCT_NAME"
    QUANTITY = "QUANTITY"
    UNIT_PRICE = "UNIT_PRICE"
    LINE_TOTAL = "LINE_TOTAL"
    SUBTOTAL = "SUBTOTAL"
    TAX = "TAX"
    GRAND_TOTAL = "GRAND_TOTAL"
    CHANGE = "CHANGE"
    CASH_BACK = "CASH_BACK"
    REFUND = "REFUND"


class ReceiptTypeEnum(str, Enum):
    """Type of receipt for pattern discovery."""

    ITEMIZED = "itemized"
    SERVICE = "service"


class ItemStructureEnum(str, Enum):
    """Line item structure type."""

    SINGLE_LINE = "single-line"
    MULTI_LINE = "multi-line"


class PositionEnum(str, Enum):
    """Horizontal position zone for labels."""

    LEFT = "left"
    CENTER = "center"
    RIGHT = "right"
    VARIES = "varies"
    NOT_FOUND = "not_found"


# =============================================================================
# LLM Review Models (llm_review.py)
# =============================================================================


class SingleReview(BaseModel):
    """A single issue review decision."""

    issue_index: int = Field(
        description="The index of the issue being reviewed (0-based)"
    )
    decision: DecisionEnum = Field(
        description="Whether the current label is correct"
    )
    reasoning: str = Field(
        description="Brief explanation citing evidence from similar words or receipt context"
    )
    suggested_label: Optional[LabelEnum] = Field(
        default=None,
        description="If INVALID, the correct label. Use null if no label applies.",
    )
    confidence: ConfidenceEnum = Field(
        default=ConfidenceEnum.MEDIUM,
        description="Confidence in this decision",
    )

    def to_dict(self) -> dict:
        """Convert to dict format for backwards compatibility."""
        return {
            "decision": self.decision.value,
            "reasoning": self.reasoning,
            "suggested_label": (
                self.suggested_label.value if self.suggested_label else None
            ),
            "confidence": self.confidence.value,
        }


class BatchedReviewResponse(BaseModel):
    """Response for batched LLM review of multiple issues."""

    reviews: list[SingleReview] = Field(
        description="One review per issue, indexed by issue_index"
    )

    def to_ordered_list(self, expected_count: int) -> list[dict]:
        """Convert to ordered list of dicts for backwards compatibility.

        Args:
            expected_count: Expected number of reviews

        Returns:
            List of review dicts, one per issue index, with fallbacks for missing
        """
        reviews_by_index = {r.issue_index: r for r in self.reviews}
        result = []
        for i in range(expected_count):
            if i in reviews_by_index:
                result.append(reviews_by_index[i].to_dict())
            else:
                result.append(
                    {
                        "decision": "NEEDS_REVIEW",
                        "reasoning": f"LLM did not provide review for issue {i}",
                        "suggested_label": None,
                        "confidence": "low",
                    }
                )
        return result


# =============================================================================
# Currency Subagent Models (currency_subagent.py)
# =============================================================================


class CurrencyLabelEnum(str, Enum):
    """Labels valid for currency evaluation."""

    PRODUCT_NAME = "PRODUCT_NAME"
    QUANTITY = "QUANTITY"
    UNIT_PRICE = "UNIT_PRICE"
    LINE_TOTAL = "LINE_TOTAL"
    SUBTOTAL = "SUBTOTAL"
    TAX = "TAX"
    GRAND_TOTAL = "GRAND_TOTAL"
    DISCOUNT = "DISCOUNT"
    CHANGE = "CHANGE"
    CASH_BACK = "CASH_BACK"
    REFUND = "REFUND"


class CurrencyEvaluation(BaseModel):
    """Evaluation result for a single currency word."""

    index: int = Field(
        description="The index of the word being evaluated (0-based)"
    )
    decision: DecisionEnum = Field(
        description="Whether the current label is correct"
    )
    reasoning: str = Field(description="Brief explanation of the decision")
    suggested_label: Optional[CurrencyLabelEnum] = Field(
        default=None, description="If INVALID, the correct label"
    )
    confidence: ConfidenceEnum = Field(
        default=ConfidenceEnum.MEDIUM,
        description="Confidence in this decision",
    )

    def to_dict(self) -> dict:
        """Convert to dict format for backwards compatibility."""
        return {
            "decision": self.decision.value,
            "reasoning": self.reasoning,
            "suggested_label": (
                self.suggested_label.value if self.suggested_label else None
            ),
            "confidence": self.confidence.value,
        }


class CurrencyEvaluationResponse(BaseModel):
    """Response for currency label evaluation."""

    evaluations: list[CurrencyEvaluation] = Field(
        description="One evaluation per currency word"
    )

    def to_ordered_list(self, expected_count: int) -> list[dict]:
        """Convert to ordered list of dicts for backwards compatibility."""
        evals_by_index = {e.index: e for e in self.evaluations}
        result = []
        for i in range(expected_count):
            if i in evals_by_index:
                result.append(evals_by_index[i].to_dict())
            else:
                result.append(
                    {
                        "decision": "NEEDS_REVIEW",
                        "reasoning": "No decision from LLM",
                        "suggested_label": None,
                        "confidence": "low",
                    }
                )
        return result


# =============================================================================
# Metadata Subagent Models (metadata_subagent.py)
# =============================================================================


class MetadataLabelEnum(str, Enum):
    """Labels valid for metadata evaluation."""

    MERCHANT_NAME = "MERCHANT_NAME"
    ADDRESS_LINE = "ADDRESS_LINE"
    PHONE_NUMBER = "PHONE_NUMBER"
    WEBSITE = "WEBSITE"
    STORE_HOURS = "STORE_HOURS"
    DATE = "DATE"
    TIME = "TIME"
    PAYMENT_METHOD = "PAYMENT_METHOD"
    COUPON = "COUPON"
    LOYALTY_ID = "LOYALTY_ID"


class MetadataEvaluation(BaseModel):
    """Evaluation result for a single metadata word."""

    index: int = Field(
        description="The index of the word being evaluated (0-based)"
    )
    decision: DecisionEnum = Field(
        description="Whether the current label is correct"
    )
    reasoning: str = Field(description="Brief explanation of the decision")
    suggested_label: Optional[MetadataLabelEnum] = Field(
        default=None, description="If INVALID, the correct label"
    )
    confidence: ConfidenceEnum = Field(
        default=ConfidenceEnum.MEDIUM,
        description="Confidence in this decision",
    )

    def to_dict(self) -> dict:
        """Convert to dict format for backwards compatibility."""
        return {
            "decision": self.decision.value,
            "reasoning": self.reasoning,
            "suggested_label": (
                self.suggested_label.value if self.suggested_label else None
            ),
            "confidence": self.confidence.value,
        }


class MetadataEvaluationResponse(BaseModel):
    """Response for metadata label evaluation."""

    evaluations: list[MetadataEvaluation] = Field(
        description="One evaluation per metadata word"
    )

    def to_ordered_list(self, expected_count: int) -> list[dict]:
        """Convert to ordered list of dicts for backwards compatibility."""
        evals_by_index = {e.index: e for e in self.evaluations}
        result = []
        for i in range(expected_count):
            if i in evals_by_index:
                result.append(evals_by_index[i].to_dict())
            else:
                result.append(
                    {
                        "decision": "NEEDS_REVIEW",
                        "reasoning": "No decision from LLM",
                        "suggested_label": None,
                        "confidence": "low",
                    }
                )
        return result


# =============================================================================
# Pattern Discovery Models (pattern_discovery.py)
# =============================================================================


class LinesPerItem(BaseModel):
    """Statistics about lines per item."""

    typical: int = Field(description="Most common number of lines per item")
    min: int = Field(description="Minimum lines per item observed")
    max: int = Field(description="Maximum lines per item observed")


class LabelPositions(BaseModel):
    """Expected positions for each label type."""

    PRODUCT_NAME: PositionEnum = Field(default=PositionEnum.LEFT)
    LINE_TOTAL: PositionEnum = Field(default=PositionEnum.RIGHT)
    UNIT_PRICE: Optional[PositionEnum] = Field(default=None)
    QUANTITY: Optional[PositionEnum] = Field(default=None)

    def to_dict(self) -> dict:
        """Convert to dict format for backwards compatibility."""
        result = {
            "PRODUCT_NAME": self.PRODUCT_NAME.value,
            "LINE_TOTAL": self.LINE_TOTAL.value,
        }
        if self.UNIT_PRICE:
            result["UNIT_PRICE"] = self.UNIT_PRICE.value
        if self.QUANTITY:
            result["QUANTITY"] = self.QUANTITY.value
        return result


class PatternDiscoveryResponse(BaseModel):
    """Response for line item pattern discovery."""

    receipt_type: ReceiptTypeEnum = Field(
        description="Whether receipt has itemized products or is a service receipt"
    )
    receipt_type_reason: str = Field(
        description="Brief explanation of receipt type classification"
    )
    item_structure: Optional[ItemStructureEnum] = Field(
        default=None,
        description="Whether items span single or multiple lines (null for service receipts)",
    )
    lines_per_item: Optional[LinesPerItem] = Field(
        default=None,
        description="Statistics about lines per item (null for service receipts)",
    )
    item_start_marker: Optional[str] = Field(
        default=None, description="What indicates the start of a new item"
    )
    item_end_marker: Optional[str] = Field(
        default=None, description="What indicates the end of an item"
    )
    barcode_pattern: Optional[str] = Field(
        default=None, description="Regex pattern for barcodes if found"
    )
    label_positions: Optional[LabelPositions] = Field(
        default=None,
        description="Expected horizontal positions for each label type",
    )
    grouping_rule: Optional[str] = Field(
        default=None,
        description="Plain English description of how to group words into line items",
    )
    special_markers: list[str] = Field(
        default_factory=list, description="Special markers like <A>, *, etc."
    )
    product_name_patterns: list[str] = Field(
        default_factory=list, description="Common patterns for product names"
    )

    def to_dict(self) -> dict:
        """Convert to dict format for backwards compatibility."""
        result = {
            "receipt_type": self.receipt_type.value,
            "receipt_type_reason": self.receipt_type_reason,
            "item_structure": (
                self.item_structure.value if self.item_structure else None
            ),
            "special_markers": self.special_markers,
            "product_name_patterns": self.product_name_patterns,
        }
        if self.lines_per_item:
            result["lines_per_item"] = {
                "typical": self.lines_per_item.typical,
                "min": self.lines_per_item.min,
                "max": self.lines_per_item.max,
            }
        if self.item_start_marker:
            result["item_start_marker"] = self.item_start_marker
        if self.item_end_marker:
            result["item_end_marker"] = self.item_end_marker
        if self.barcode_pattern:
            result["barcode_pattern"] = self.barcode_pattern
        if self.label_positions:
            result["label_positions"] = self.label_positions.to_dict()
        if self.grouping_rule:
            result["grouping_rule"] = self.grouping_rule
        return result
