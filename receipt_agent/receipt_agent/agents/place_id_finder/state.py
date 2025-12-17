"""
State definition for the Place ID Finder agent.
"""

from typing import Annotated, Any

from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


class PlaceIdFinderState(BaseModel):
    """State for the place ID finder workflow."""

    # Target receipt
    image_id: str = Field(description="Image ID to find place_id for")
    receipt_id: int = Field(description="Receipt ID to find place_id for")

    # Conversation messages
    messages: Annotated[list[Any], add_messages] = Field(default_factory=list)

    class Config:
        arbitrary_types_allowed = True



