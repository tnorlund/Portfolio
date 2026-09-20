"""
Receipt Agent - LangGraph agents for receipt data.

This package provides LangGraph-based agents for receipt place resolution,
label evaluation, and question answering over DynamoDB-backed receipt data.
"""

from receipt_agent.clients.factory import (
    create_all_clients,
    create_dynamo_client,
    create_embed_fn,
    create_places_api,
)
from receipt_agent.exceptions import (
    AgentExecutionError,
    EmptyResponseError,
    LLMError,
    LLMInvocationError,
    LLMRateLimitError,
    ReceiptAgentConfigurationError,
    ReceiptAgentError,
)
from receipt_agent.state.models import (
    ValidationResult,
    ValidationStatus,
    VerificationStep,
)
from receipt_agent.utils.llm_factory import (
    is_rate_limit_error,
    is_service_error,
    is_timeout_error,
)

__version__ = "0.1.0"

__all__ = [
    # State models
    "ValidationResult",
    "ValidationStatus",
    "VerificationStep",
    # Client factories
    "create_all_clients",
    "create_dynamo_client",
    "create_embed_fn",
    "create_places_api",
    # Package errors
    "ReceiptAgentError",
    "ReceiptAgentConfigurationError",
    "AgentExecutionError",
    "LLMError",
    "LLMInvocationError",
    "LLMRateLimitError",
    "EmptyResponseError",
    # LLM utilities
    "is_rate_limit_error",
    "is_service_error",
    "is_timeout_error",
]
