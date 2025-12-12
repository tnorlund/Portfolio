"""
CoVe Text Consistency Sub-Agent State

State definition for the CoVe text consistency workflow.
"""

from typing import Annotated, Any, Optional

from langgraph.graph.message import add_messages
from pydantic import BaseModel, Field


class CoveTextConsistencyState(BaseModel):
    """State for the CoVe text consistency workflow."""

    place_id: str = Field(description="Google Place ID being verified")
    canonical_merchant_name: str = Field(
        description="Canonical merchant name from harmonizer"
    )
    canonical_address: Optional[str] = Field(
        default=None, description="Canonical address from harmonizer"
    )
    canonical_phone: Optional[str] = Field(
        default=None, description="Canonical phone from harmonizer"
    )
    receipts: list[dict] = Field(
        default_factory=list,
        description="List of receipts to verify: [{'image_id': str, 'receipt_id': int}, ...]",
    )

    # Conversation messages
    messages: Annotated[list[Any], add_messages] = Field(default_factory=list)

    class Config:
        arbitrary_types_allowed = True


