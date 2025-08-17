"""
Pydantic models for structured validation responses
===================================================

These models ensure type-safe, validated responses from the LLM.
"""

from typing import List, Optional
from pydantic import BaseModel, Field, field_validator


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
        description="The correct label if is_valid is false (must be from ALLOWED LABELS)"
    )
    confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Confidence score between 0 and 1 for this validation"
    )
    reasoning: Optional[str] = Field(
        default=None,
        description="Brief explanation for the validation decision"
    )
    
    @field_validator('id')
    @classmethod
    def validate_id_format(cls, v: str) -> str:
        """Ensure ID follows expected format"""
        if not v or '#' not in v:
            raise ValueError(f"ID must contain # separators, got: {v}")
        
        # Check it has the expected parts
        parts = v.split('#')
        expected_keys = ['IMAGE', 'RECEIPT', 'LINE', 'WORD', 'LABEL']
        
        # Extract keys (every other element starting from 0)
        keys = [parts[i] for i in range(0, len(parts), 2) if i < len(parts)]
        
        if not all(k in keys for k in expected_keys):
            raise ValueError(
                f"ID must contain IMAGE, RECEIPT, LINE, WORD, and LABEL sections. Got: {v}"
            )
        
        return v
    
    @field_validator('correct_label')
    @classmethod
    def validate_correct_label(cls, v: Optional[str], info) -> Optional[str]:
        """Ensure correct_label is only set when is_valid is False"""
        if v is not None:
            # Check if is_valid is False (correct_label should only be set for invalid labels)
            is_valid = info.data.get('is_valid', True)
            if is_valid:
                raise ValueError(
                    "correct_label should only be set when is_valid is False"
                )
            
            # List of allowed labels from CORE_LABELS
            allowed_labels = [
                'ADDRESS', 'CASHIER', 'CATEGORY', 'CITY', 'COUNTRY', 'CURRENCY',
                'DATE', 'DESCRIPTION', 'DISCOUNT', 'EMAIL', 'MERCHANT_NAME',
                'PAYMENT_METHOD', 'PHONE_NUMBER', 'PRODUCT_NAME', 'QUANTITY',
                'RECEIPT_NUMBER', 'REGISTER', 'STATE', 'STORE_NUMBER', 'SUBTOTAL',
                'TAX', 'TAX_RATE', 'TIME', 'TIPS', 'TOTAL', 'TRANSACTION_NUMBER',
                'UNIT_PRICE', 'URL', 'ZIP_CODE', 'OTHER', 'NONE'
            ]
            
            if v.upper() not in allowed_labels:
                raise ValueError(
                    f"correct_label must be one of the allowed labels. Got: {v}"
                )
            
            return v.upper()
        
        return v


class ValidationResponse(BaseModel):
    """Complete validation response containing all results"""
    
    results: List[ValidationResult] = Field(
        description="List of validation results for each target"
    )
    
    total_validated: Optional[int] = Field(
        default=None,
        description="Total number of labels validated"
    )
    
    valid_count: Optional[int] = Field(
        default=None,
        description="Number of labels that were valid"
    )
    
    invalid_count: Optional[int] = Field(
        default=None,
        description="Number of labels that were invalid"
    )
    
    average_confidence: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Average confidence across all validations"
    )
    
    processing_notes: Optional[str] = Field(
        default=None,
        description="Any notes about the validation process"
    )
    
    def compute_statistics(self) -> None:
        """Compute statistics from results"""
        if self.results:
            self.total_validated = len(self.results)
            self.valid_count = sum(1 for r in self.results if r.is_valid)
            self.invalid_count = sum(1 for r in self.results if not r.is_valid)
            
            confidences = [r.confidence for r in self.results if r.confidence is not None]
            if confidences:
                self.average_confidence = sum(confidences) / len(confidences)
    
    class Config:
        # This helps with JSON serialization
        json_schema_extra = {
            "example": {
                "results": [
                    {
                        "id": "IMAGE#abc123#RECEIPT#00001#LINE#00001#WORD#00001#LABEL#MERCHANT_NAME",
                        "is_valid": True,
                        "confidence": 0.95
                    },
                    {
                        "id": "IMAGE#abc123#RECEIPT#00001#LINE#00005#WORD#00002#LABEL#TOTAL",
                        "is_valid": False,
                        "correct_label": "SUBTOTAL",
                        "confidence": 0.88,
                        "reasoning": "This appears before tax, so it's a subtotal not total"
                    }
                ],
                "total_validated": 2,
                "valid_count": 1,
                "invalid_count": 1,
                "average_confidence": 0.915
            }
        }


class ValidationRequest(BaseModel):
    """Request model for validation (for API usage)"""
    
    image_id: str = Field(description="Receipt image ID")
    receipt_id: int = Field(description="Receipt ID in database")
    labels: List[dict] = Field(
        description="List of labels to validate with line_id, word_id, label fields"
    )
    use_cache: bool = Field(
        default=True,
        description="Whether to use context caching"
    )
    skip_database_update: bool = Field(
        default=False,
        description="If True, don't update database after validation"
    )


class ValidationState(BaseModel):
    """State model for validation workflow"""
    
    formatted_prompt: str
    validation_targets: List[dict]
    validation_response: Optional[ValidationResponse] = None
    error: Optional[str] = None
    completed: bool = False
    llm_calls: int = 0
    used_cache: bool = False
    database_updated: bool = False