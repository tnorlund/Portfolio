"""
State definition for the Agentic workflow agent.
"""

from typing import Annotated, Any, Optional

from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


class AgentState(BaseModel):
    """State for the agentic validation workflow."""

    # Target receipt
    image_id: str = Field(description="Image ID being validated")
    receipt_id: int = Field(description="Receipt ID being validated")

    # Conversation messages - use add_messages reducer to accumulate messages
    messages: Annotated[list[Any], add_messages] = Field(default_factory=list)

    # Terminal state
    decision: Optional[dict] = Field(
        default=None, description="Final decision when complete"
    )

    class Config:
        arbitrary_types_allowed = True

