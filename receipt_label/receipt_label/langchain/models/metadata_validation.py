"""Metadata validation models for LLM-based validation."""

from pydantic import BaseModel, Field


class MetadataValidationResponse(BaseModel):
    """LLM response for metadata validation."""
    
    is_valid: bool = Field(
        description="True if ReceiptMetadata matches the receipt text, False otherwise"
    )
    
    reasoning: str = Field(
        description="Explanation of the validation decision"
    )
    
    recommended_merchant_name: str = Field(
        default="",
        description="If invalid, the merchant name that should be used instead (empty if valid)"
    )

