"""AgenticValidation trace schemas.

This module defines schemas for parsing AgenticValidation agent trace
inputs/outputs from LangSmith exports. This agent uses a ReAct pattern
with tools for receipt metadata validation.
"""

from typing import Any, Optional

from pydantic import BaseModel, Field
from receipt_langsmith.entities.place_id_finder import ToolCallTrace


class AgenticValidationInputs(BaseModel):
    """Inputs to AgenticValidation trace."""

    image_id: str
    """Receipt image ID (UUID)."""

    receipt_id: int
    """Receipt ID within image (1-indexed)."""


class AgenticValidationOutputs(BaseModel):
    """Outputs from AgenticValidation trace."""

    decision: Optional[dict[str, Any]] = None
    """Final decision with validated metadata."""

    tool_calls: list[ToolCallTrace] = Field(default_factory=list)
    """Tool calls made during validation."""

    reasoning: str = ""
    """Agent's reasoning for the decision."""

    validation_status: Optional[str] = None
    """Validation status: 'validated', 'invalid', 'needs_review'."""

    validated_merchant_name: Optional[str] = None
    """Validated merchant name."""

    validated_place_id: Optional[str] = None
    """Validated Google Place ID."""

    validated_address: Optional[str] = None
    """Validated address."""

    validated_phone: Optional[str] = None
    """Validated phone number."""
