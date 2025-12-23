"""
Receipt Agent - Agentic validation for receipt place data.

This package provides LangGraph-based agents for validating receipt place data
using ChromaDB similarity search and cross-reference verification.
"""

from receipt_agent.agents.place_validator import PlaceValidatorAgent
from receipt_agent.agents.validation import create_validation_graph
from receipt_agent.clients.factory import (
    create_all_clients,
    create_chroma_client,
    create_dynamo_client,
    create_embed_fn,
    create_places_api,
)
from receipt_agent.state.models import (
    ValidationResult,
    ValidationState,
    ValidationStatus,
    VerificationStep,
)
from receipt_agent.utils.ollama_rate_limit import (
    OllamaCircuitBreaker,
    OllamaRateLimitError,
    RateLimitedLLMInvoker,
    is_rate_limit_error,
    is_server_error,
    is_timeout_error,
)

__version__ = "0.1.0"

__all__ = [
    # Main agent
    "PlaceValidatorAgent",
    # State models
    "ValidationResult",
    "ValidationState",
    "ValidationStatus",
    "VerificationStep",
    # Workflow
    "create_validation_graph",
    # Client factories
    "create_all_clients",
    "create_chroma_client",
    "create_dynamo_client",
    "create_embed_fn",
    "create_places_api",
    # Rate limit utilities
    "OllamaRateLimitError",
    "OllamaCircuitBreaker",
    "RateLimitedLLMInvoker",
    "is_rate_limit_error",
    "is_server_error",
    "is_timeout_error",
]
