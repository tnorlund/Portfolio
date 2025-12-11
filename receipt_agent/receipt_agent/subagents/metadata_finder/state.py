"""
Receipt Metadata Finder Sub-Agent State

State definition for the receipt metadata finder workflow.
"""

from typing import Annotated, Any

from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


class ReceiptMetadataFinderState(BaseModel):
    """State for the receipt metadata finder workflow."""

    # Target receipt
    image_id: str = Field(description="Image ID to find metadata for")
    receipt_id: int = Field(description="Receipt ID to find metadata for")

    # Conversation messages
    messages: Annotated[list[Any], add_messages] = Field(default_factory=list)

    class Config:
        arbitrary_types_allowed = True
