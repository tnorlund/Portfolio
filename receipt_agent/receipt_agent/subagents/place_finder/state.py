"""
Receipt Place Finder Sub-Agent State

State definition for the receipt place finder workflow.
"""

from typing import Annotated, Any

from langgraph.graph.message import add_messages
from pydantic import BaseModel, ConfigDict, Field


class ReceiptPlaceFinderState(BaseModel):
    """State for the receipt place finder workflow."""

    # Target receipt
    image_id: str = Field(description="Image ID to find place data for")
    receipt_id: int = Field(description="Receipt ID to find place data for")

    # Conversation messages
    messages: Annotated[list[Any], add_messages] = Field(default_factory=list)

    model_config = ConfigDict(arbitrary_types_allowed=True)

