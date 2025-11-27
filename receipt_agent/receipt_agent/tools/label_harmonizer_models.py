"""Pydantic models for structured LLM output in label harmonizer."""

from pydantic import BaseModel, Field
from typing import Literal, Optional


class OutlierDecision(BaseModel):
    """Structured output for outlier detection."""

    is_outlier: bool = Field(
        description="True if the word does not belong in this label type group, False if it belongs"
    )
    reasoning: Optional[str] = Field(
        default=None,
        description="Brief explanation of the decision"
    )


class LabelTypeSuggestion(BaseModel):
    """Structured output for label type suggestion."""

    suggested_label_type: Optional[str] = Field(
        default=None,
        description="The correct CORE_LABEL type for this word (e.g., PRODUCT_NAME, MERCHANT_NAME, etc.), or None if it doesn't match any CORE_LABEL type"
    )
    reasoning: Optional[str] = Field(
        default=None,
        description="Brief explanation of why this label type was suggested"
    )

