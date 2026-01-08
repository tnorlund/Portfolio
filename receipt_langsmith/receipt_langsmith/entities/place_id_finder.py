"""PlaceIdFinder trace schemas.

This module defines schemas for parsing PlaceIdFinder agent trace
inputs/outputs from LangSmith exports.
"""

from typing import Any, Optional

from pydantic import BaseModel, Field


class ToolCallTrace(BaseModel):
    """A tool call made by the agent."""

    tool_name: str
    """Name of the tool called."""

    tool_input: dict[str, Any] = Field(default_factory=dict)
    """Input to the tool."""

    tool_output: Optional[Any] = None
    """Output from tool."""

    success: bool = True
    """Whether tool call succeeded."""

    error_message: Optional[str] = None
    """Error message if failed."""


class PlaceIdFinderInputs(BaseModel):
    """Inputs to PlaceIdFinder trace."""

    image_id: str
    """Receipt image ID (UUID)."""

    receipt_id: int
    """Receipt ID within image (1-indexed)."""


class PlaceIdFinderOutputs(BaseModel):
    """Outputs from PlaceIdFinder trace."""

    place_id: Optional[str] = None
    """Found Google Place ID."""

    place_name: Optional[str] = None
    """Name associated with the Place ID."""

    address: Optional[str] = None
    """Address from Google Places."""

    confidence: float = 0.0
    """Confidence in the result (0-1)."""

    tool_calls: list[ToolCallTrace] = Field(default_factory=list)
    """Tool calls made during search."""

    reasoning: str = ""
    """Agent's reasoning for the decision."""

    decision: Optional[dict[str, Any]] = None
    """Final decision submitted."""
