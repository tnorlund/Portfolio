"""
Structured Validation Tool with ValidationStatus Enum
====================================================

This module defines a proper LangGraph tool for receipt label validation
using structured Pydantic models and ValidationStatus enum.
"""

from typing import Literal, List
from pydantic import BaseModel, Field, ConfigDict
from langchain_core.tools import tool

from receipt_dynamo.constants import ValidationStatus


class ValidationResult(BaseModel):
    """Structured validation result with ValidationStatus enum."""
    
    model_config = ConfigDict(
        json_schema_extra={
            "examples": [
                {
                    "validation_status": "INVALID",
                    "reasoning": "The word 'USD$' is a currency symbol, not a numeric value. GRAND_TOTAL should be applied to the actual total amount like '25.99', not to currency indicators."
                }
            ]
        }
    )
    
    validation_status: Literal["VALID", "INVALID", "NEEDS_REVIEW", "PENDING"] = Field(
        description="The validation status for this word label",
        examples=["VALID", "INVALID", "NEEDS_REVIEW", "PENDING"]
    )
    
    reasoning: str = Field(
        min_length=15,
        description="Clear, detailed explanation of why this validation status was assigned. Focus on the specific word and its context.",
        examples=[
            "The word 'USD$' is a currency symbol, not a numeric value that represents a total amount",
            "The date '05/09/2024' is in the correct MM/DD/YYYY format and appears in the date section of the receipt",
            "This word appears to be a product name but the surrounding context is ambiguous"
        ]
    )


class ValidationResponse(BaseModel):
    """Response containing multiple validation results for batch processing."""
    
    results: List[ValidationResult] = Field(
        description="List of validation results for the batch"
    )


@tool("validate_word_label", return_direct=True)
def validate_word_label(
    validation_status: Literal["VALID", "INVALID", "NEEDS_REVIEW", "PENDING"],
    reasoning: str
) -> ValidationResult:
    """
    Validate a word label on a receipt.
    
    Use this tool to provide your assessment of whether a word label is correct.
    
    Args:
        validation_status: One of VALID (completely correct), INVALID (definitely wrong), 
                          NEEDS_REVIEW (ambiguous, needs human review), or PENDING (insufficient info)
        reasoning: Detailed explanation of your decision
        
    Returns:
        ValidationResult with the assessment
    """
    return ValidationResult(
        validation_status=validation_status,
        reasoning=reasoning
    )


def create_validation_tools():
    """Create the validation tool for LangGraph."""
    return [validate_word_label]


# For backwards compatibility
def get_validation_tool():
    """Get the validation tool."""
    return validate_word_label