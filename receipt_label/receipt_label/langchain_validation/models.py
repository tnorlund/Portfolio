"""
LangChain Validation Models
===========================

DEPRECATED: This file is kept for backward compatibility.
Please import from the models package instead:

from receipt_label.langchain_validation.models import (
    ValidationResult,
    ValidationResponse,
    EnhancedValidationResult,
    BatchValidationResponse,
    ValidationTarget,
    BatchValidationContext
)
"""

# Re-export all models for backward compatibility
from .models import *

# Explicit imports for clarity
from .models.simple import ValidationResult, ValidationResponse
from .models.enhanced import EnhancedValidationResult
from .models.batch import BatchValidationResponse, SimilarExample
from .models.context import ValidationTarget, BatchValidationContext