"""
LangChain Validation Module for Receipt Processing
"""

from .graph_design import (
    create_validation_graph,
    validate_receipt_labels,
    ValidationState,
    ValidateLabelsInput,
    LabelValidationResult
)

from .implementation import (
    ReceiptValidator,
    validate_receipt_labels_v2,
    ValidationConfig as ConfigFromImplementation
)

from .config import (
    ValidationConfig,
    OllamaConfig
)

__all__ = [
    # Graph components
    "create_validation_graph",
    "validate_receipt_labels",
    "ValidationState",
    "ValidateLabelsInput",
    "LabelValidationResult",
    
    # Implementation
    "ReceiptValidator",
    "validate_receipt_labels_v2",
    
    # Configuration
    "ValidationConfig",
    "OllamaConfig"
]