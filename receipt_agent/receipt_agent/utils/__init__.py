"""Receipt Agent utility modules."""

from .llm_factory import RateLimitedLLMInvoker  # alias for LLMInvoker
from .llm_factory import create_resilient_llm  # alias for create_llm_invoker
from .llm_factory import is_fallback_error  # alias for is_retriable_error
from .llm_factory import is_server_error  # alias for is_service_error
from .llm_factory import (  # Primary exports; Backward compatibility aliases (kept for existing code)
    CostTrackingCallback,
    EmptyResponseError,
    LLMInvoker,
    LLMRateLimitError,
    create_llm,
    create_llm_from_settings,
    create_llm_invoker,
    create_production_invoker,
    is_rate_limit_error,
    is_retriable_error,
    is_service_error,
    is_timeout_error,
)
from .structured_output import (
    DEFAULT_STRICT_STRUCTURED_OUTPUT,
    DEFAULT_STRUCTURED_OUTPUT_RETRIES,
    StructuredOutputResult,
    ainvoke_structured_with_retry,
    build_structured_failure_decisions,
    get_structured_output_settings,
    invoke_structured_with_retry,
)

__all__ = [
    # LLM Factory - Primary
    "CostTrackingCallback",
    "LLMRateLimitError",
    "LLMInvoker",
    "EmptyResponseError",
    "create_llm",
    "create_llm_invoker",
    "create_llm_from_settings",
    "create_production_invoker",
    "is_rate_limit_error",
    "is_service_error",
    "is_timeout_error",
    "is_retriable_error",
    # LLM Factory - Backward Compatibility Aliases
    "RateLimitedLLMInvoker",
    "create_resilient_llm",
    "is_fallback_error",
    "is_server_error",
    # Structured output utilities
    "StructuredOutputResult",
    "DEFAULT_STRICT_STRUCTURED_OUTPUT",
    "DEFAULT_STRUCTURED_OUTPUT_RETRIES",
    "get_structured_output_settings",
    "invoke_structured_with_retry",
    "ainvoke_structured_with_retry",
    "build_structured_failure_decisions",
]
