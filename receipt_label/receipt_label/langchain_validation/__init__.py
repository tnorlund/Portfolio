"""
LangChain Validation Module - Optimized for Minimal LLM Usage
"""

from .graph_design import (
    # Context preparation (no LLM)
    prepare_validation_context,
    
    # Graph components
    create_minimal_validation_graph,
    MinimalValidationState,
    
    # Database operations (no LLM)
    update_database_with_results,
    
    # Main validation functions
    validate_receipt_labels_optimized,
    CachedValidator,
    
    # LLM configuration
    get_ollama_llm,
)

from .implementation import (
    # Main validator class
    OptimizedReceiptValidator,
    
    # Configuration
    SimpleConfig,
    
    # Convenience functions
    validate_receipt_labels,
    test_ollama_connection,
)

from .config import (
    ValidationConfig,
    OllamaConfig
)

__all__ = [
    # Context and database (no LLM usage)
    "prepare_validation_context",
    "update_database_with_results",
    
    # Graph components
    "create_minimal_validation_graph",
    "MinimalValidationState",
    
    # Validation functions
    "validate_receipt_labels",
    "validate_receipt_labels_optimized",
    
    # Classes
    "OptimizedReceiptValidator",
    "CachedValidator",
    "SimpleConfig",
    
    # Configuration
    "ValidationConfig",
    "OllamaConfig",
    
    # Utilities
    "get_ollama_llm",
    "test_ollama_connection",
]