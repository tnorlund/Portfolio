"""
State definition for the Label Harmonizer agent.
"""

from typing import Annotated, Any, Optional

from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


class LabelHarmonizerAgentState(BaseModel):
    """State for the label harmonizer agent workflow."""

    # Target receipt
    image_id: str = Field(description="Image ID containing the receipt")
    receipt_id: int = Field(description="Receipt ID within the image")

    # Receipt data (loaded by tools)
    receipt_text: Optional[str] = Field(
        default=None, description="Full receipt text"
    )
    words: list[dict] = Field(
        default_factory=list, description="Receipt words"
    )
    lines: list[dict] = Field(
        default_factory=list, description="Receipt lines"
    )
    labels: list[dict] = Field(
        default_factory=list, description="Current labels"
    )

    # Analysis results
    currency: Optional[str] = Field(
        default=None, description="Detected currency"
    )
    totals_validation: Optional[dict] = Field(
        default=None, description="Totals validation results"
    )
    line_items: list[dict] = Field(
        default_factory=list, description="Parsed line items"
    )

    # Conversation messages
    messages: Annotated[list[Any], add_messages] = Field(default_factory=list)

    # Final result
    harmonization_result: Optional[dict] = Field(
        default=None, description="Final harmonization result"
    )

    class Config:
        arbitrary_types_allowed = True

