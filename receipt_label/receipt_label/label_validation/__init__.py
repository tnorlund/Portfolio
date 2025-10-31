"""Exports utilities for validating receipt word labels."""

# Core validation infrastructure
from .base import BaseLabelValidator, ValidatorConfig
from .data import (
    LabelValidationResult,
    get_unique_merchants_and_data,
    update_labels,
)
from .orchestrator import ValidationOrchestrator
from .prompt_builder import build_validation_prompt
from .registry import (
    get_all_supported_labels,
    get_missing_labels,
    get_validator,
    register_validator,
)

# Legacy function-based validators (maintained for backward compatibility)
from .validate_address import validate_address
from .validate_currency import validate_currency
from .validate_date import validate_date
from .validate_merchant_name import (
    validate_merchant_name_google,
    validate_merchant_name_pinecone,
)
from .validate_phone_number import validate_phone_number
from .validate_time import validate_time

__all__ = [
    # Core infrastructure
    "BaseLabelValidator",
    "ValidatorConfig",
    "ValidationOrchestrator",
    "build_validation_prompt",
    "get_validator",
    "register_validator",
    "get_all_supported_labels",
    "get_missing_labels",
    # Data structures
    "LabelValidationResult",
    "get_unique_merchants_and_data",
    "update_labels",
    # Legacy validators (backward compatibility)
    "validate_address",
    "validate_currency",
    "validate_merchant_name_google",
    "validate_merchant_name_pinecone",
    "validate_phone_number",
    "validate_date",
    "validate_time",
]
