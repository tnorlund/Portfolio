"""
Ollama Rate Limit Handling Utilities.

This module provides reusable utilities for handling Ollama Cloud API rate limits
in LangChain/LangGraph applications. It includes:

- OllamaRateLimitError: Custom exception for Step Function retry logic
- Error detection functions: Identify rate limits, server errors, timeouts
- OllamaCircuitBreaker: Circuit breaker pattern to prevent API spam
- RateLimitedLLMInvoker: Wrapper for LLM calls with automatic rate limiting

Usage:
    from receipt_agent.utils.ollama_rate_limit import (
        OllamaRateLimitError,
        OllamaCircuitBreaker,
        RateLimitedLLMInvoker,
    )

    # Create circuit breaker
    circuit_breaker = OllamaCircuitBreaker(threshold=5)

    # Create rate-limited invoker with random jitter
    invoker = RateLimitedLLMInvoker(
        llm=my_langchain_llm,
        circuit_breaker=circuit_breaker,
        max_jitter_seconds=0.25,  # Random jitter 0-0.25s between calls
    )

    # Make LLM calls with automatic rate limiting
    for prompt in prompts:
        try:
            response = invoker.invoke(prompt)
        except OllamaRateLimitError:
            # Circuit breaker triggered - let Step Function retry
            raise
"""

import logging
import random
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Optional, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


# =============================================================================
# Custom Exception
# =============================================================================


class OllamaRateLimitError(Exception):
    """
    Custom exception for Ollama rate limit errors.

    This exception is caught by AWS Step Functions retry logic, which applies
    a longer delay (30s) and more retry attempts (5) compared to standard errors.

    The Step Function ASL should include:
    ```json
    {
        "Retry": [
            {
                "ErrorEquals": ["OllamaRateLimitError"],
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


# =============================================================================
# Error Detection Functions
# =============================================================================


def is_rate_limit_error(error: Exception) -> bool:
    """
    Check if an exception is a rate limit error.

    Detects various rate limit indicators:
    - HTTP 429 status code
    - "rate limit" or "rate_limit" in error message
    - "too many requests" or "too many concurrent requests"

    Args:
        error: The exception to check

    Returns:
        True if this appears to be a rate limit error
    """
    error_str = str(error).lower()
    return (
        "429" in str(error)
        or "rate limit" in error_str
        or "rate_limit" in error_str
        or "too many concurrent requests" in error_str
        or "too many requests" in error_str
        or "ratelimit" in error_str
    )


def is_server_error(error: Exception) -> bool:
    """
    Check if an exception is a server error (5xx).

    Args:
        error: The exception to check

    Returns:
        True if this appears to be a 5xx server error
    """
    error_str = str(error).lower()
    return (
        "status 500" in error_str
        or "status 502" in error_str
        or "status 503" in error_str
        or "status 504" in error_str
        or "http 500" in error_str
        or "http 502" in error_str
        or "http 503" in error_str
        or "http 504" in error_str
        or "server error" in error_str
        or "internal server error" in error_str
        or "bad gateway" in error_str
        or "service unavailable" in error_str
        or "gateway timeout" in error_str
    )


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


# =============================================================================
# Circuit Breaker
# =============================================================================


@dataclass
class OllamaCircuitBreaker:
    """
    Circuit breaker for Ollama API calls.

    Tracks consecutive rate limit errors and triggers when threshold is reached.
    When triggered, raises OllamaRateLimitError to let Step Function retry later.

    The circuit breaker resets after a successful call.

    Attributes:
        threshold: Number of consecutive rate limit errors before triggering
        consecutive_errors: Current count of consecutive rate limit errors
        total_rate_limit_errors: Total rate limit errors seen (doesn't reset)
        total_server_errors: Total server errors seen
        total_timeout_errors: Total timeout errors seen
        triggered: Whether the circuit breaker has been triggered

    Usage:
        circuit_breaker = OllamaCircuitBreaker(threshold=5)

        for item in items:
            try:
                result = make_llm_call(item)
                circuit_breaker.record_success()
            except Exception as e:
                circuit_breaker.record_error(e)  # May raise OllamaRateLimitError
    """

    threshold: int = 5
    consecutive_errors: int = field(default=0, init=False)
    total_rate_limit_errors: int = field(default=0, init=False)
    total_server_errors: int = field(default=0, init=False)
    total_timeout_errors: int = field(default=0, init=False)
    triggered: bool = field(default=False, init=False)

    def record_success(self) -> None:
        """Record a successful API call, resetting consecutive error count."""
        self.consecutive_errors = 0
        self.triggered = False

    def record_error(self, error: Exception) -> None:
        """
        Record an API error and check if circuit breaker should trigger.

        Args:
            error: The exception that occurred

        Raises:
            OllamaRateLimitError: If circuit breaker threshold is reached
            Exception: Timeout errors are re-raised for Step Function retry
            None: Server/other errors are logged but not raised
        """
        if is_rate_limit_error(error):
            self.consecutive_errors += 1
            self.total_rate_limit_errors += 1
            logger.warning(
                "Rate limit error %d/%d: %s",
                self.consecutive_errors,
                self.threshold,
                error,
            )

            if self.consecutive_errors >= self.threshold:
                self.triggered = True
                logger.error(
                    "Circuit breaker triggered: %d consecutive rate limit "
                    "errors. Stopping to prevent API spam.",
                    self.consecutive_errors,
                )
                raise OllamaRateLimitError(
                    f"Circuit breaker triggered after {self.consecutive_errors} "
                    f"consecutive rate limit errors",
                    consecutive_errors=self.consecutive_errors,
                    total_errors=self.total_rate_limit_errors,
                ) from error

            # Single rate limit error (below threshold) - still raise for retry
            raise OllamaRateLimitError(
                f"Rate limit error: {error}",
                consecutive_errors=self.consecutive_errors,
                total_errors=self.total_rate_limit_errors,
            ) from error

        elif is_server_error(error):
            self.total_server_errors += 1
            # Don't count toward circuit breaker, but log it
            logger.warning(
                "Server error (not counted for circuit breaker): %s", error
            )

        elif is_timeout_error(error):
            self.total_timeout_errors += 1
            # Timeouts should be retried by Step Function
            logger.warning("Timeout error: %s", error)
            raise error  # Re-raise for Step Function retry

        else:
            # Other errors - reset consecutive count but don't trigger
            self.consecutive_errors = 0
            logger.warning("Other error (consecutive count reset): %s", error)

    def get_stats(self) -> dict[str, Any]:
        """Get circuit breaker statistics."""
        return {
            "threshold": self.threshold,
            "consecutive_errors": self.consecutive_errors,
            "total_rate_limit_errors": self.total_rate_limit_errors,
            "total_server_errors": self.total_server_errors,
            "total_timeout_errors": self.total_timeout_errors,
            "triggered": self.triggered,
        }


# =============================================================================
# Rate-Limited LLM Invoker
# =============================================================================


@dataclass
class RateLimitedLLMInvoker:
    """
    Wrapper for LangChain LLM with automatic rate limiting.

    Provides:
    - Random jitter between calls to prevent thundering herd
    - Circuit breaker integration
    - Automatic error classification and handling

    Attributes:
        llm: The LangChain LLM instance (ChatOllama, etc.)
        circuit_breaker: Optional circuit breaker instance
        max_jitter_seconds: Maximum random jitter between calls (default 0.25s)
        call_count: Number of calls made

    Usage:
        from langchain_ollama import ChatOllama

        llm = ChatOllama(model="gpt-oss:20b-cloud", ...)
        circuit_breaker = OllamaCircuitBreaker(threshold=5)
        invoker = RateLimitedLLMInvoker(
            llm=llm,
            circuit_breaker=circuit_breaker,
            max_jitter_seconds=0.25,
        )

        for prompt in prompts:
            response = invoker.invoke(prompt)
    """

    llm: Any  # LangChain LLM instance
    circuit_breaker: Optional[OllamaCircuitBreaker] = None
    max_jitter_seconds: float = 0.25
    call_count: int = field(default=0, init=False)

    def _apply_jitter(self) -> None:
        """Apply random jitter between calls to prevent thundering herd."""
        if self.call_count > 0 and self.max_jitter_seconds > 0:
            jitter = random.uniform(0, self.max_jitter_seconds)
            if jitter > 0:
                time.sleep(jitter)

    def invoke(self, messages: Any, config: Optional[dict] = None) -> Any:
        """
        Invoke the LLM with rate limiting and circuit breaker protection.

        Args:
            messages: Messages to send to the LLM (LangChain format)
            config: Optional LangChain config dict (for callbacks/tracing)

        Returns:
            LLM response

        Raises:
            OllamaRateLimitError: If rate limit hit or circuit breaker triggers
        """
        self._apply_jitter()
        self.call_count += 1

        try:
            if config:
                response = self.llm.invoke(messages, config=config)
            else:
                response = self.llm.invoke(messages)
            if self.circuit_breaker:
                self.circuit_breaker.record_success()
            return response

        except OllamaRateLimitError:
            # Already an OllamaRateLimitError, just re-raise
            raise
        except Exception as e:
            if self.circuit_breaker:
                # This may raise OllamaRateLimitError
                self.circuit_breaker.record_error(e)
            raise

    def invoke_with_fallback(
        self,
        messages: Any,
        fallback_fn: Callable[[], T],
    ) -> Any:
        """
        Invoke LLM with a fallback function for non-rate-limit errors.

        Args:
            messages: Messages to send to the LLM
            fallback_fn: Function to call if LLM fails (non-rate-limit error)

        Returns:
            LLM response or fallback result

        Raises:
            OllamaRateLimitError: If rate limit is hit (no fallback for this)
        """
        try:
            return self.invoke(messages)
        except OllamaRateLimitError:
            # Rate limits should propagate up for Step Function retry
            raise
        except Exception as e:
            logger.warning("LLM call failed, using fallback: %s", e)
            return fallback_fn()

    def get_stats(self) -> dict[str, Any]:
        """Get invoker statistics."""
        stats: dict[str, Any] = {
            "call_count": self.call_count,
            "max_jitter_seconds": self.max_jitter_seconds,
        }
        if self.circuit_breaker:
            stats["circuit_breaker"] = self.circuit_breaker.get_stats()
        return stats
