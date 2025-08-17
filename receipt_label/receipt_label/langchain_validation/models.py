"""
Pydantic models for structured validation responses
===================================================

These models ensure type-safe, validated responses from the LLM.
"""

from typing import List, Optional
from pydantic import BaseModel, Field, field_validator
from receipt_label.constants import CORE_LABELS


class ValidationResult(BaseModel):
    """Single validation result for a receipt label"""

    # Simple identifier - could be anything, just for tracking
    id: str = Field(
        description="Identifier for this validation (for tracking purposes)"
    )
    is_valid: bool = Field(
        description="Whether the proposed label is correct for this word"
    )
    correct_label: Optional[str] = Field(
        default=None,
        description="The correct label if is_valid is false (must be from ALLOWED LABELS, or None if word shouldn't be labeled)",
    )

    @field_validator("correct_label")
    @classmethod
    def validate_correct_label(cls, v: Optional[str], info) -> Optional[str]:
        """Validate the correct_label field"""
        if v is not None:
            # Check if is_valid is False (correct_label should only be set for invalid labels)
            is_valid = info.data.get("is_valid", True)
            if is_valid:
                # If valid, we don't need a correct_label
                return None

            # Normalize to uppercase
            v_upper = v.upper()

            # Check if it's a valid label (or explicitly None for "no label needed")
            if v_upper in CORE_LABELS:
                return v_upper
            elif v_upper in ["NONE", "NULL", ""]:
                # Word shouldn't be labeled
                return None
            else:
                # Unknown label - default to None
                return None

        return v


class ValidationResponse(BaseModel):
    """Complete validation response containing all results"""

    results: List[ValidationResult] = Field(
        description="List of validation results for each target"
    )

    class Config:
        # This helps with JSON serialization
        json_schema_extra = {
            "example": {
                "results": [
                    {
                        "id": "IMAGE#abc123#RECEIPT#00001#LINE#00001#WORD#00001#LABEL#MERCHANT_NAME",
                        "is_valid": True,
                    },
                    {
                        "id": "IMAGE#abc123#RECEIPT#00001#LINE#00005#WORD#00002#LABEL#GRAND_TOTAL",
                        "is_valid": False,
                        "correct_label": "SUBTOTAL",
                    },
                ]
            }
        }


# Removed ValidationRequest and ValidationState - keeping models minimal
# Only ValidationResult and ValidationResponse are needed for structured output
