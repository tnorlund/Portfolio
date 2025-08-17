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

    id: str = Field(
        description="The exact ID from the target in format IMAGE#xxx#RECEIPT#xxxxx#LINE#xxxxx#WORD#xxxxx#LABEL#xxx"
    )
    is_valid: bool = Field(
        description="Whether the proposed label is correct for this word"
    )
    correct_label: Optional[str] = Field(
        default=None,
        description="The correct label if is_valid is false (must be from ALLOWED LABELS)",
    )
    # Removed confidence and reasoning - keep it deterministic and simple

    @field_validator("id")
    @classmethod
    def validate_id_format(cls, v: str) -> str:
        """Ensure ID follows expected format"""
        if not v or "#" not in v:
            raise ValueError(f"ID must contain # separators, got: {v}")

        # Check it has the expected parts
        parts = v.split("#")
        expected_keys = ["IMAGE", "RECEIPT", "LINE", "WORD", "LABEL"]

        # Extract keys (every other element starting from 0)
        keys = [parts[i] for i in range(0, len(parts), 2) if i < len(parts)]

        if not all(k in keys for k in expected_keys):
            raise ValueError(
                f"ID must contain IMAGE, RECEIPT, LINE, WORD, and LABEL sections. Got: {v}"
            )

        return v

    @field_validator("correct_label")
    @classmethod
    def validate_correct_label(cls, v: Optional[str], info) -> Optional[str]:
        """Ensure correct_label is only set when is_valid is False"""
        if v is not None:
            # Check if is_valid is False (correct_label should only be set for invalid labels)
            is_valid = info.data.get("is_valid", True)
            if is_valid:
                raise ValueError(
                    "correct_label should only be set when is_valid is False"
                )

            # Use the actual CORE_LABELS from constants
            if v.upper() not in CORE_LABELS:
                raise ValueError(
                    f"correct_label must be one of the CORE_LABELS. Got: {v}"
                )

            return v.upper()

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
