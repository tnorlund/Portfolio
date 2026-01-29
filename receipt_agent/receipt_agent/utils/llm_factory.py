"""
LLM Factory - OpenRouter-only LLM creation with retry logic.

This module provides a unified interface for creating LLM instances that work
with OpenRouter as the single provider, with built-in retry logic for handling
transient errors.

Architecture:
    ┌─────────────────────────────────────────────────────────────┐
    │              Step Function Retry Layer                       │
    │   (LLMRateLimitError → 30s backoff, 5 attempts)             │
    └─────────────────────────────────────────────────────────────┘
                              │
                              ▼
    ┌─────────────────────────────────────────────────────────────┐
    │                    LLMInvoker                               │
    │   Jitter + retry on 429/5xx → raise LLMRateLimitError       │
    └─────────────────────────────────────────────────────────────┘
                              │
                              ▼
    ┌─────────────────────────────────────────────────────────────┐
    │                  ChatOpenAI (OpenRouter)                     │
    │   Single provider - paid tier only                          │
    └─────────────────────────────────────────────────────────────┘

Usage:
    # For Lambda handlers - with jitter and retry
    from receipt_agent.utils.llm_factory import create_llm_invoker

    invoker = create_llm_invoker()
    response = invoker.invoke(messages)

    # For simple scripts - just the LLM
    from receipt_agent.utils.llm_factory import create_llm

    llm = create_llm()
    response = llm.invoke(messages)

Environment Variables:
    OPENROUTER_BASE_URL: OpenRouter API URL (default: https://openrouter.ai/api/v1)
    OPENROUTER_API_KEY: OpenRouter API key (required)
    OPENROUTER_MODEL: Model name (default: openai/gpt-oss-120b)
"""

import asyncio
import logging
import os
import random
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

logger = logging.getLogger(__name__)


# =============================================================================
# Custom Exceptions
# =============================================================================


class LLMRateLimitError(Exception):
    """
    Raised when OpenRouter returns rate limit or server errors after retries.

    This exception is caught by AWS Step Functions retry logic, which applies
    a longer delay (30s) and more retry attempts (5) compared to standard errors.

    The Step Function ASL should include:
    ```json
    {
        "Retry": [
            {
                "ErrorEquals": ["LLMRateLimitError"],
                "IntervalSeconds": 30,
                "MaxAttempts": 5,
                "BackoffRate": 1.5
            }
        ]
    }
    ```
    """

    def __init__(
        self,
        message: str,
        consecutive_errors: int = 0,
        total_errors: int = 0,
    ):
        super().__init__(message)
        self.consecutive_errors = consecutive_errors
        self.total_errors = total_errors


class EmptyResponseError(Exception):
    """
    Raised when the LLM returns an empty response.

    This can happen when providers are under heavy load and return
    successful HTTP responses but with empty content.
    """

    def __init__(
        self,
        provider: str = "OpenRouter",
        message: str = "LLM returned empty response",
    ):
        super().__init__(f"{provider}: {message}")
        self.provider = provider


# =============================================================================
# Error Detection Functions
# =============================================================================

# Error patterns that indicate rate limiting / capacity issues
RATE_LIMIT_PATTERNS = [
    "429",
    "rate limit",
    "rate_limit",
    "ratelimit",
    "too many requests",
    "too many concurrent requests",
    "capacity",
    "overloaded",
]

# Patterns that indicate temporary service issues (may resolve with retry)
SERVICE_ERROR_PATTERNS = [
    "503",
    "service unavailable",
    "502",
    "bad gateway",
    "500",
    "internal server error",
    "504",
    "gateway timeout",
]


def is_rate_limit_error(error: Exception) -> bool:
    """
    Check if an error is specifically a rate limit error.

    Args:
        error: The exception to check

    Returns:
        True if this is a rate limit error
    """
    # Check for our custom error types
    if isinstance(error, LLMRateLimitError):
        return True

    error_str = str(error).lower()
    return any(pattern in error_str for pattern in RATE_LIMIT_PATTERNS)


def is_service_error(error: Exception) -> bool:
    """
    Check if an error is a temporary service error.

    Args:
        error: The exception to check

    Returns:
        True if this is a service availability error
    """
    error_str = str(error).lower()
    return any(pattern in error_str for pattern in SERVICE_ERROR_PATTERNS)


def is_timeout_error(error: Exception) -> bool:
    """
    Check if an exception is a timeout error.

    Args:
        error: The exception to check

    Returns:
        True if this appears to be a timeout error
    """
    error_str = str(error).lower()
    return "timeout" in error_str or "timed out" in error_str


def is_retriable_error(error: Exception) -> bool:
    """
    Check if an error should trigger a retry.

    This is the union of rate limit errors, service errors, and timeout errors.
    Timeouts are transient and should be retried.

    Args:
        error: The exception to check

    Returns:
        True if this error should trigger a retry
    """
    return (
        is_rate_limit_error(error)
        or is_service_error(error)
        or is_timeout_error(error)
    )


# Backward compatibility alias
is_fallback_error = is_retriable_error
is_server_error = is_service_error


# =============================================================================
# LLM Creation
# =============================================================================


def create_llm(
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    temperature: float = 0.0,
    timeout: int = 120,
    reasoning: Optional[bool] = None,
    **kwargs: Any,
) -> "BaseChatModel":
    """
    Create a ChatOpenAI instance configured for OpenRouter.

    Args:
        model: Model name (default from OPENROUTER_MODEL env var)
        base_url: API URL (default from OPENROUTER_BASE_URL env var)
        api_key: API key (default from OPENROUTER_API_KEY env var)
        temperature: LLM temperature (default 0.0)
        timeout: Request timeout in seconds (default 120)
        reasoning: Enable/disable reasoning tokens for supported models
            (e.g., Grok 4.1 Fast, Claude with extended thinking).
            None = use model's default behavior.
            True = explicitly enable reasoning.
            False = explicitly disable reasoning.
        **kwargs: Additional arguments passed to ChatOpenAI

    Returns:
        Configured ChatOpenAI instance for OpenRouter

    Raises:
        ValueError: If OpenRouter API key is not provided
    """
    from langchain_openai import ChatOpenAI

    _model = (
        model
        or os.environ.get("OPENROUTER_MODEL")
        or os.environ.get(
            "RECEIPT_AGENT_OPENROUTER_MODEL", "openai/gpt-oss-120b"
        )
    )
    _base_url = (
        base_url
        or os.environ.get("OPENROUTER_BASE_URL")
        or os.environ.get(
            "RECEIPT_AGENT_OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
        )
    )
    _api_key = (
        api_key
        or os.environ.get("OPENROUTER_API_KEY")
        or os.environ.get("RECEIPT_AGENT_OPENROUTER_API_KEY", "")
    )

    if not _api_key:
        raise ValueError(
            "OpenRouter API key is required. Set OPENROUTER_API_KEY or "
            "RECEIPT_AGENT_OPENROUTER_API_KEY environment variable."
        )

    logger.debug(
        "Creating OpenRouter LLM: model=%s, base_url=%s, reasoning=%s",
        _model, _base_url, reasoning
    )

    default_headers = kwargs.pop("default_headers", {})
    default_headers.setdefault(
        "HTTP-Referer", "https://github.com/tnorlund/Portfolio"
    )
    default_headers.setdefault("X-Title", "Receipt Agent")

    # Build extra_body for OpenRouter-specific parameters
    extra_body = kwargs.pop("extra_body", {})
    if reasoning is not None:
        extra_body["reasoning"] = {"enabled": reasoning}

    # Only pass extra_body if it has content
    if extra_body:
        kwargs["extra_body"] = extra_body

    return ChatOpenAI(
        model=_model,
        base_url=_base_url,
        api_key=_api_key,
        temperature=temperature,
        timeout=timeout,
        default_headers=default_headers,
        **kwargs,
    )


def create_llm_from_settings(
    temperature: float = 0.0,
    timeout: int = 120,
    **kwargs: Any,
) -> "BaseChatModel":
    """Create an LLM instance using the application settings."""
    from receipt_agent.config.settings import get_settings

    settings = get_settings()

    return create_llm(
        model=settings.openrouter_model,
        base_url=settings.openrouter_base_url,
        api_key=settings.openrouter_api_key.get_secret_value(),
        temperature=temperature,
        timeout=timeout,
        **kwargs,
    )


# =============================================================================
# Empty Response Detection
# =============================================================================


def _is_empty_response(response: Any) -> bool:
    """
    Check if an LLM response is empty or invalid.

    Empty responses can occur when providers are under load and return
    HTTP 200 but with no actual content.

    Args:
        response: The LLM response object

    Returns:
        True if the response is empty/invalid
    """
    if response is None:
        return True

    # Get content from response
    content = None
    if hasattr(response, "content"):
        content = response.content
    elif isinstance(response, str):
        content = response
    elif isinstance(response, dict):
        content = response.get("content", "")

    # Check various empty content types
    if content is None:
        return True

    # Empty string or whitespace-only
    if isinstance(content, str) and not content.strip():
        return True

    # List content (multimodal content blocks)
    if isinstance(content, list):
        # Empty list
        if len(content) == 0:
            return True
        # List with all empty text blocks
        # e.g., [{"type": "text", "text": ""}] or [{"text": ""}]
        all_empty = True
        for block in content:
            if isinstance(block, dict):
                text = block.get("text", "")
                if isinstance(text, str) and text.strip():
                    all_empty = False
                    break
            elif isinstance(block, str) and block.strip():
                all_empty = False
                break
        if all_empty:
            return True

    return False


# =============================================================================
# LLM Invoker with Jitter and Retry
# =============================================================================


@dataclass
class LLMInvoker:
    """
    LLM wrapper with jitter and retry logic.

    Provides:
    - Random jitter between calls to prevent thundering herd
    - Automatic retry on transient errors (429, 5xx)
    - Empty response detection

    Attributes:
        llm: The LangChain LLM instance
        max_jitter_seconds: Maximum random jitter between calls (default 0.25s)
        max_retries: Maximum retry attempts (default 3)
        call_count: Number of calls made
        consecutive_errors: Current consecutive error count
        total_errors: Total errors encountered
    """

    llm: Any  # BaseChatModel
    max_jitter_seconds: float = 0.25
    max_retries: int = 3
    call_count: int = field(default=0, init=False)
    consecutive_errors: int = field(default=0, init=False)
    total_errors: int = field(default=0, init=False)
    _async_lock: Optional[asyncio.Lock] = field(
        default=None, init=False, repr=False
    )

    def _get_async_lock(self) -> asyncio.Lock:
        """Get or create the async lock (lazy initialization).

        This avoids DeprecationWarning in Python 3.10+ by creating
        the lock inside an async context rather than at instantiation time.
        """
        if self._async_lock is None:
            self._async_lock = asyncio.Lock()
        return self._async_lock

    def _apply_jitter(self) -> None:
        """Apply random jitter between calls to prevent thundering herd."""
        if self.call_count > 0 and self.max_jitter_seconds > 0:
            jitter = random.uniform(0, self.max_jitter_seconds)
            if jitter > 0:
                time.sleep(jitter)

    async def _apply_jitter_async(self) -> None:
        """Apply random jitter between calls (async version)."""
        if self.call_count > 0 and self.max_jitter_seconds > 0:
            jitter = random.uniform(0, self.max_jitter_seconds)
            if jitter > 0:
                await asyncio.sleep(jitter)

    def invoke(
        self, messages: Any, config: Optional[dict] = None, **kwargs
    ) -> Any:
        """
        Invoke the LLM with retry logic.

        Args:
            messages: Messages to send to the LLM (LangChain format)
            config: Optional LangChain config dict (for callbacks/tracing)
            **kwargs: Additional arguments passed to the LLM

        Returns:
            LLM response

        Raises:
            LLMRateLimitError: If rate limit hit after all retries
        """
        last_error = None

        for attempt in range(self.max_retries):
            self._apply_jitter()
            self.call_count += 1

            try:
                if config:
                    response = self.llm.invoke(
                        messages, config=config, **kwargs
                    )
                else:
                    response = self.llm.invoke(messages, **kwargs)

                # Check for empty response
                if _is_empty_response(response):
                    raise EmptyResponseError(
                        "OpenRouter", "Empty response received"
                    )

                # Success - reset consecutive errors
                self.consecutive_errors = 0
                return response

            except EmptyResponseError:
                self.consecutive_errors += 1
                self.total_errors += 1
                last_error = LLMRateLimitError(
                    "Empty response received",
                    consecutive_errors=self.consecutive_errors,
                    total_errors=self.total_errors,
                )
                logger.warning(
                    "Empty response on attempt %d/%d",
                    attempt + 1,
                    self.max_retries,
                )

            except Exception as e:
                if is_retriable_error(e):
                    self.consecutive_errors += 1
                    self.total_errors += 1
                    last_error = LLMRateLimitError(
                        str(e),
                        consecutive_errors=self.consecutive_errors,
                        total_errors=self.total_errors,
                    )
                    logger.warning(
                        "Retriable error on attempt %d/%d: %s",
                        attempt + 1,
                        self.max_retries,
                        str(e)[:100],
                    )
                else:
                    # Non-retriable error, raise immediately
                    raise

        # All retries exhausted
        logger.error(
            "All %d retries exhausted, raising LLMRateLimitError",
            self.max_retries,
        )
        raise last_error or LLMRateLimitError(
            "All retries exhausted",
            consecutive_errors=self.consecutive_errors,
            total_errors=self.total_errors,
        )

    async def ainvoke(
        self, messages: Any, config: Optional[dict] = None, **kwargs
    ) -> Any:
        """
        Async invoke the LLM with retry logic.

        Args:
            messages: Messages to send to the LLM (LangChain format)
            config: Optional LangChain config dict (for callbacks/tracing)
            **kwargs: Additional arguments passed to the LLM

        Returns:
            LLM response

        Raises:
            LLMRateLimitError: If rate limit hit after all retries
        """
        last_error = None

        for attempt in range(self.max_retries):
            # Apply jitter outside the lock to avoid blocking other coroutines
            await self._apply_jitter_async()
            async with self._get_async_lock():
                self.call_count += 1

            try:
                if config:
                    response = await self.llm.ainvoke(
                        messages, config=config, **kwargs
                    )
                else:
                    response = await self.llm.ainvoke(messages, **kwargs)

                # Check for empty response
                if _is_empty_response(response):
                    raise EmptyResponseError(
                        "OpenRouter", "Empty response received"
                    )

                # Success - reset consecutive errors
                async with self._get_async_lock():
                    self.consecutive_errors = 0
                return response

            except EmptyResponseError:
                async with self._get_async_lock():
                    self.consecutive_errors += 1
                    self.total_errors += 1
                last_error = LLMRateLimitError(
                    "Empty response received",
                    consecutive_errors=self.consecutive_errors,
                    total_errors=self.total_errors,
                )
                logger.warning(
                    "Empty response on attempt %d/%d",
                    attempt + 1,
                    self.max_retries,
                )

            except Exception as e:
                if is_retriable_error(e):
                    async with self._get_async_lock():
                        self.consecutive_errors += 1
                        self.total_errors += 1
                    last_error = LLMRateLimitError(
                        str(e),
                        consecutive_errors=self.consecutive_errors,
                        total_errors=self.total_errors,
                    )
                    logger.warning(
                        "Retriable error on attempt %d/%d: %s",
                        attempt + 1,
                        self.max_retries,
                        str(e)[:100],
                    )
                else:
                    # Non-retriable error, raise immediately
                    raise

        # All retries exhausted
        logger.error(
            "All %d retries exhausted, raising LLMRateLimitError",
            self.max_retries,
        )
        raise last_error or LLMRateLimitError(
            "All retries exhausted",
            consecutive_errors=self.consecutive_errors,
            total_errors=self.total_errors,
        )

    def with_structured_output(self, schema: type) -> "LLMInvoker":
        """
        Create a new LLMInvoker with structured output.

        Uses LangChain's with_structured_output() to enforce JSON schema at
        the API level (via function calling or JSON mode).

        Args:
            schema: A Pydantic model class defining the expected output structure

        Returns:
            New LLMInvoker instance with structured output enabled

        Example:
            from pydantic import BaseModel

            class ReviewResponse(BaseModel):
                decision: str
                reasoning: str

            structured_invoker = invoker.with_structured_output(ReviewResponse)
            response = structured_invoker.invoke(messages)  # Returns ReviewResponse
        """
        if hasattr(self.llm, "with_structured_output"):
            structured_llm = self.llm.with_structured_output(schema)
        else:
            logger.warning(
                "LLM %s does not support with_structured_output",
                type(self.llm).__name__,
            )
            structured_llm = self.llm

        return LLMInvoker(
            llm=structured_llm,
            max_jitter_seconds=self.max_jitter_seconds,
            max_retries=self.max_retries,
        )

    def get_stats(self) -> dict[str, Any]:
        """Get invoker statistics."""
        return {
            "call_count": self.call_count,
            "consecutive_errors": self.consecutive_errors,
            "total_errors": self.total_errors,
            "max_jitter_seconds": self.max_jitter_seconds,
            "max_retries": self.max_retries,
        }


# Backward compatibility alias
RateLimitedLLMInvoker = LLMInvoker


# =============================================================================
# Factory Functions
# =============================================================================


def create_llm_invoker(
    model: Optional[str] = None,
    temperature: float = 0.0,
    timeout: int = 120,
    max_jitter_seconds: float = 0.25,
    max_retries: int = 3,
    reasoning: Optional[bool] = None,
    **kwargs: Any,
) -> LLMInvoker:
    """
    Create an LLM invoker with jitter and retry logic.

    This is the recommended way to create an LLM for production use.

    Args:
        model: Model name (default from OPENROUTER_MODEL env var)
        temperature: LLM temperature (default 0.0)
        timeout: Request timeout in seconds (default 120)
        max_jitter_seconds: Max random delay between calls (default 0.25s)
        max_retries: Max retry attempts (default 3)
        reasoning: Enable/disable reasoning tokens for supported models.
            None = use model's default, True = enable, False = disable.
        **kwargs: Additional arguments passed to create_llm()

    Returns:
        Configured LLMInvoker
    """
    llm = create_llm(
        model=model,
        temperature=temperature,
        timeout=timeout,
        reasoning=reasoning,
        **kwargs,
    )

    return LLMInvoker(
        llm=llm,
        max_jitter_seconds=max_jitter_seconds,
        max_retries=max_retries,
    )


# Backward compatibility aliases
def create_production_invoker(
    temperature: float = 0.0,
    timeout: int = 120,
    circuit_breaker_threshold: int = 5,  # Ignored - kept for API compatibility
    max_jitter_seconds: float = 0.25,
    **kwargs: Any,
) -> LLMInvoker:
    """
    Create a production-ready LLM invoker.

    This is an alias for create_llm_invoker() for backward compatibility.

    Args:
        temperature: LLM temperature (default 0.0)
        timeout: Request timeout in seconds (default 120)
        circuit_breaker_threshold: Ignored (kept for API compatibility)
        max_jitter_seconds: Max random delay between calls (default 0.25s)
        **kwargs: Additional arguments passed to create_llm()

    Returns:
        Configured LLMInvoker
    """
    _ = circuit_breaker_threshold  # Ignored
    return create_llm_invoker(
        temperature=temperature,
        timeout=timeout,
        max_jitter_seconds=max_jitter_seconds,
        **kwargs,
    )


def create_resilient_llm(
    temperature: float = 0.0,
    timeout: int = 120,
    **kwargs: Any,
) -> LLMInvoker:
    """
    Create an LLM invoker with retry logic.

    This is an alias for create_llm_invoker() for backward compatibility.

    Args:
        temperature: LLM temperature (default 0.0)
        timeout: Request timeout in seconds (default 120)
        **kwargs: Additional arguments passed to create_llm()

    Returns:
        Configured LLMInvoker
    """
    return create_llm_invoker(
        temperature=temperature,
        timeout=timeout,
        **kwargs,
    )
