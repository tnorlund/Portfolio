"""
State definition for the Harmonizer agent.
"""

from typing import Annotated, Any

from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


class HarmonizerAgentState(BaseModel):
    """State for the harmonizer agent workflow."""

    # Target place_id group
    place_id: str = Field(description="Google Place ID being harmonized")
    receipts: list[dict] = Field(
        default_factory=list, description="Receipts in this group"
    )

    # Conversation messages
    messages: Annotated[list[Any], add_messages] = Field(default_factory=list)

    class Config:
        arbitrary_types_allowed = True
