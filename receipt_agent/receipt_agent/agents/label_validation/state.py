"""
State definition for the Label Validation agent.
"""

from typing import Annotated, Any, Optional

from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


class LabelValidationState(BaseModel):
    """State for the label validation agent."""

    # Input
    word_text: str = Field(description="Word text being validated")
    suggested_label_type: str = Field(
        description="Suggested label type to validate"
    )
    merchant_name: Optional[str] = Field(
        default=None, description="Merchant name"
    )
    original_reasoning: str = Field(
        description="Original reasoning from suggestion LLM"
    )
    image_id: str = Field(description="Image ID")
    receipt_id: int = Field(description="Receipt ID")
    line_id: int = Field(description="Line ID")
    word_id: int = Field(description="Word ID")

    # Conversation messages
    messages: Annotated[list[Any], add_messages] = Field(default_factory=list)

    # Terminal state
    decision: Optional[dict] = Field(
        default=None, description="Final decision when complete"
    )

    class Config:
        arbitrary_types_allowed = True
