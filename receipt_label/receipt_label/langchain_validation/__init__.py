"""
LangChain Validation Module - Clean Structured Output Implementation
=================================================================

This module provides structured validation using LangChain with Ollama Turbo API.
Includes exact prompt format matching assemble_prompt_batch_optimized.py.
"""

from .structured_validation import (
    StructuredReceiptValidator,
)

from .validation_tool import (
    ValidationResult,
    ValidationResponse,
    validate_word_label,
    create_validation_tools,
)

from .ollama_turbo_client import (
    ChatOllamaTurbo,
    create_ollama_turbo_client,
)

from .context_preparation import (
    ContextPreparationService,
    ContextPreparationConfig,
    prepare_contexts_for_validation,
)

from .models.context import (
    ValidationContext,
    WordContext,
    ReceiptContext,
    SemanticContext,
)

from .config import ValidationConfig, OllamaConfig

from .update_receipt_labels import (
    update_label_with_validation_result,
    update_receipt_labels_batch,
    validate_and_update_labels,
)

__all__ = [
    # Main validator
    "StructuredReceiptValidator",
    # Pydantic models
    "ValidationResult",
    "ValidationResponse",
    # Tools
    "validate_word_label",
    "create_validation_tools",
    # Ollama client
    "ChatOllamaTurbo", 
    "create_ollama_turbo_client",
    # Context preparation
    "ContextPreparationService",
    "ContextPreparationConfig",
    "prepare_contexts_for_validation",
    # Data models
    "ValidationContext",
    "WordContext",
    "ReceiptContext", 
    "SemanticContext",
    # Configuration
    "ValidationConfig",
    "OllamaConfig",
    # Update utilities
    "update_label_with_validation_result",
    "update_receipt_labels_batch", 
    "validate_and_update_labels",
]