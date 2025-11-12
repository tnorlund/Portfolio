"""Metadata validation models for LLM-based validation."""

from pydantic import BaseModel, Field
from typing import Optional


class FieldValidationResult(BaseModel):
    """Validation result for a single field."""

    is_valid: bool = Field(
        description="True if this field matches between ReceiptMetadata and receipt text"
    )

    reasoning: str = Field(
        description="Explanation of why this field is valid or invalid"
    )

    recommended_value: Optional[str] = Field(
        default=None,
        description="If invalid, the correct value from the receipt (None if valid or not found)"
    )


class MetadataValidationResponse(BaseModel):
    """LLM response for comprehensive metadata validation."""

    overall_is_valid: bool = Field(
        description="True if ReceiptMetadata overall matches the receipt text, False otherwise. "
                   "Should be False if any critical field (merchant_name) is invalid."
    )

    reasoning: str = Field(
        description="Overall explanation of the validation decision"
    )

    # Field-specific validation results
    merchant_name: Optional[FieldValidationResult] = Field(
        default=None,
        description="Validation result for merchant_name field (CRITICAL - must be valid for overall to be valid)"
    )

    phone_number: Optional[FieldValidationResult] = Field(
        default=None,
        description="Validation result for phone_number field"
    )

    address: Optional[FieldValidationResult] = Field(
        default=None,
        description="Validation result for address field"
    )

    # Backward compatibility - use merchant_name.recommended_value if available
    @property
    def recommended_merchant_name(self) -> str:
        """Backward compatibility property."""
        if self.merchant_name and self.merchant_name.recommended_value:
            return self.merchant_name.recommended_value
        return ""

    @property
    def is_valid(self) -> bool:
        """Backward compatibility property."""
        return self.overall_is_valid

