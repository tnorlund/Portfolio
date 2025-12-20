"""
State definition for the Receipt Grouping agent.
"""

from typing import Annotated, Any, Optional

from langgraph.graph.message import add_messages
from pydantic import BaseModel, ConfigDict, Field


class GroupingState(BaseModel):
    """State for the receipt grouping workflow."""

    # Target image
    image_id: str = Field(description="Image ID being analyzed")

    # Conversation messages - use add_messages reducer to accumulate messages
    messages: Annotated[list[Any], add_messages] = Field(default_factory=list)

    # Terminal state
    grouping: Optional[dict] = Field(
        default=None, description="Final grouping when complete"
    )

    model_config = ConfigDict(arbitrary_types_allowed=True)


