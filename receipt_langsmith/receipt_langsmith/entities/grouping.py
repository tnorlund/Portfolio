"""ReceiptGrouping trace schemas.

This module defines schemas for parsing ReceiptGrouping agent trace
inputs/outputs from LangSmith exports. This agent determines correct
receipt groupings within images.
"""

from typing import Any, Optional

from pydantic import BaseModel, Field

from receipt_langsmith.entities.place_id_finder import ToolCallTrace


class ReceiptBoundary(BaseModel):
    """Boundary coordinates for a receipt within an image."""

    receipt_id: int
    """Receipt ID (1-indexed)."""

    top: float
    """Top edge (normalized 0-1)."""

    bottom: float
    """Bottom edge (normalized 0-1)."""

    left: float
    """Left edge (normalized 0-1)."""

    right: float
    """Right edge (normalized 0-1)."""


class GroupingProposal(BaseModel):
    """A proposed grouping of lines/words into receipts."""

    receipt_count: int
    """Number of receipts in this proposal."""

    boundaries: list[ReceiptBoundary] = Field(default_factory=list)
    """Boundaries for each receipt."""

    confidence: float = 0.0
    """Confidence in this grouping (0-1)."""

    reasoning: str = ""
    """Reasoning for this grouping."""


class GroupingInputs(BaseModel):
    """Inputs to ReceiptGrouping trace."""

    image_id: str
    """Image ID to analyze for receipt grouping."""


class GroupingOutputs(BaseModel):
    """Outputs from ReceiptGrouping trace."""

    grouping: Optional[GroupingProposal] = None
    """Final grouping decision."""

    receipt_count: int = 0
    """Number of receipts detected."""

    tool_calls: list[ToolCallTrace] = Field(default_factory=list)
    """Tool calls made during analysis."""

    reasoning: str = ""
    """Agent's reasoning for the grouping."""

    alternatives_considered: list[GroupingProposal] = Field(
        default_factory=list
    )
    """Alternative groupings that were considered."""

    submission: Optional[dict[str, Any]] = None
    """Final submission."""
