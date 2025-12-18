"""
Financial Validation Sub-Agent State

State definition for the financial validation sub-agent.
"""

from typing import TYPE_CHECKING, Annotated, Any, Optional

from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field

if TYPE_CHECKING:
    from receipt_dynamo.data.dynamo_client import DynamoClient


class FinancialValidationState(BaseModel):
    """State for the financial validation sub-agent."""

    # Receipt data
    receipt_text: str = Field(description="Full receipt text")
    labels: list[dict] = Field(default_factory=list, description="Current labels")
    words: list[dict] = Field(default_factory=list, description="Receipt words")

    # Analysis results
    currency: Optional[str] = Field(default=None, description="Detected currency")
    financial_issues: list[dict] = Field(
        default_factory=list, description="Financial validation issues found"
    )
    label_corrections: list[dict] = Field(
        default_factory=list, description="Label corrections needed"
    )

    # Conversation messages
    messages: Annotated[list[Any], add_messages] = Field(default_factory=list)

    class Config:
        arbitrary_types_allowed = True
