"""
LLM Factory - Resilient LLM creation with automatic failover.

This module provides a unified interface for creating LLM instances that can work
with multiple providers (Ollama Cloud, OpenRouter) with automatic fallback when
the primary provider fails due to rate limits or concurrency errors.

Architecture:
    ┌─────────────────────────────────────────────────────────────┐
    │              Step Function Retry Layer                       │
    │   (OllamaRateLimitError → 30s backoff, 5 attempts)          │
    └─────────────────────────────────────────────────────────────┘
                              │
                              ▼
    ┌─────────────────────────────────────────────────────────────┐
    │              RateLimitedLLMInvoker                           │
    │   (jitter between calls + circuit breaker integration)       │
    └─────────────────────────────────────────────────────────────┘
                              │
                              ▼
    ┌─────────────────────────────────────────────────────────────┐
    │                    ResilientLLM                              │
    │   Ollama (primary) ──429──► OpenRouter (fallback)           │
    │   - If fallback succeeds: return response (no error)        │
    │   - If both fail with 429: raise for circuit breaker        │
    └─────────────────────────────────────────────────────────────┘

Usage:
    # For Lambda handlers - full production stack
    from receipt_agent.utils.llm_factory import create_production_invoker

    invoker = create_production_invoker()
    response = invoker.invoke(messages)

    # For simple scripts - just resilient LLM
    from receipt_agent.utils.llm_factory import create_resilient_llm

    llm = create_resilient_llm()
    response = llm.invoke(messages)

Environment Variables:
    For Ollama (Primary):
        OLLAMA_BASE_URL: Ollama API URL (default: https://ollama.com)
        OLLAMA_API_KEY: Ollama API key
        OLLAMA_MODEL: Model name (default: gpt-oss:120b-cloud)

    For OpenRouter (Fallback):
        OPENROUTER_BASE_URL: OpenRouter API URL (default: https://openrouter.ai/api/v1)
        OPENROUTER_API_KEY: OpenRouter API key
        OPENROUTER_MODEL: Free model name (default: openai/gpt-oss-120b:free)
        OPENROUTER_PAID_MODEL: Paid model name (default: openai/gpt-oss-120b)
"""

import logging
import os
from dataclasses import dataclass, field
from enum import Enum
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:
    from langchain_core.language_models import BaseChatModel

    from receipt_agent.utils.ollama_rate_limit import RateLimitedLLMInvoker

logger = logging.getLogger(__name__)


class LLMProvider(str, Enum):
    """Supported LLM providers."""

    OLLAMA = "ollama"
    OPENROUTER = "openrouter"


# Model name mappings between providers
# Ollama uses "gpt-oss:120b-cloud" format, OpenRouter uses "openai/gpt-oss-120b:free"
MODEL_MAPPINGS = {
    # Ollama -> OpenRouter
    "gpt-oss:120b-cloud": "openai/gpt-oss-120b:free",
    "gpt-oss:20b-cloud": "openai/gpt-oss-20b:free",
}

# Reverse mappings
REVERSE_MODEL_MAPPINGS = {v: k for k, v in MODEL_MAPPINGS.items()}


# Error patterns that indicate rate limiting / capacity issues
# These trigger fallback to OpenRouter
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
]


def is_rate_limit_error(error: Exception) -> bool:
    """
    Check if an error is specifically a rate limit error.

    These errors indicate the API is rejecting requests due to quota/concurrency
    limits and should trigger immediate fallback to another provider.

    Args:
        error: The exception to check

    Returns:
        True if this is a rate limit error
    """
    error_str = str(error).lower()
    return any(pattern in error_str for pattern in RATE_LIMIT_PATTERNS)


def is_service_error(error: Exception) -> bool:
    """
    Check if an error is a temporary service error.

    These errors indicate the service is temporarily unavailable and may
    resolve with retry or fallback.

    Args:
        error: The exception to check

    Returns:
        True if this is a service availability error
    """
    error_str = str(error).lower()
    return any(pattern in error_str for pattern in SERVICE_ERROR_PATTERNS)


def is_fallback_error(error: Exception) -> bool:
    """
    Check if an error should trigger fallback to OpenRouter.

    This is the union of rate limit errors and service errors.

    Args:
        error: The exception to check

    Returns:
        True if this error should trigger fallback
    """
    return is_rate_limit_error(error) or is_service_error(error)


def get_default_provider() -> LLMProvider:
    """Get the default LLM provider from environment.

    Checks both LLM_PROVIDER and RECEIPT_AGENT_LLM_PROVIDER for compatibility
    with both direct environment variable usage and pydantic-settings.
    """
    provider_str = (
        os.environ.get("LLM_PROVIDER")
        or os.environ.get("RECEIPT_AGENT_LLM_PROVIDER", "ollama")
    ).lower()
    try:
        return LLMProvider(provider_str)
    except ValueError:
        logger.warning(
            "Invalid LLM_PROVIDER '%s', defaulting to 'ollama'", provider_str
        )
        return LLMProvider.OLLAMA


# =============================================================================
# Provider-Specific LLM Creation
# =============================================================================


def _create_ollama_llm(
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    temperature: float = 0.0,
    timeout: int = 120,
    **kwargs: Any,
) -> "BaseChatModel":
    """Create a ChatOllama instance."""
    from langchain_ollama import ChatOllama

    _model = model or os.environ.get("OLLAMA_MODEL") or os.environ.get(
        "RECEIPT_AGENT_OLLAMA_MODEL", "gpt-oss:120b-cloud"
    )
    _base_url = base_url or os.environ.get("OLLAMA_BASE_URL") or os.environ.get(
        "RECEIPT_AGENT_OLLAMA_BASE_URL", "https://ollama.com"
    )
    _api_key = api_key or os.environ.get("OLLAMA_API_KEY") or os.environ.get(
        "RECEIPT_AGENT_OLLAMA_API_KEY", ""
    )

    client_kwargs = kwargs.pop("client_kwargs", {})
    if _api_key:
        headers = client_kwargs.get("headers", {})
        headers["Authorization"] = f"Bearer {_api_key}"
        client_kwargs["headers"] = headers
    client_kwargs.setdefault("timeout", timeout)

    logger.debug("Creating Ollama LLM: model=%s, base_url=%s", _model, _base_url)

    return ChatOllama(
        model=_model,
        base_url=_base_url,
        temperature=temperature,
        client_kwargs=client_kwargs,
        **kwargs,
    )


def _create_openrouter_llm(
    model: Optional[str] = None,
    base_url: Optional[str] = None,
    api_key: Optional[str] = None,
    temperature: float = 0.0,
    timeout: int = 120,
    **kwargs: Any,
) -> "BaseChatModel":
    """Create a ChatOpenAI instance configured for OpenRouter."""
    from langchain_openai import ChatOpenAI

    _model = model or os.environ.get("OPENROUTER_MODEL") or os.environ.get(
        "RECEIPT_AGENT_OPENROUTER_MODEL", "openai/gpt-oss-120b:free"
    )
    _base_url = base_url or os.environ.get("OPENROUTER_BASE_URL") or os.environ.get(
        "RECEIPT_AGENT_OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
    )
    _api_key = api_key or os.environ.get("OPENROUTER_API_KEY") or os.environ.get(
        "RECEIPT_AGENT_OPENROUTER_API_KEY", ""
    )

    if not _api_key:
        raise ValueError(
            "OpenRouter API key is required. Set OPENROUTER_API_KEY or "
            "RECEIPT_AGENT_OPENROUTER_API_KEY environment variable."
        )

    # Map Ollama model names to OpenRouter equivalents
    if _model in MODEL_MAPPINGS:
        mapped_model = MODEL_MAPPINGS[_model]
        logger.debug("Mapped model %s -> %s for OpenRouter", _model, mapped_model)
        _model = mapped_model

    logger.debug("Creating OpenRouter LLM: model=%s, base_url=%s", _model, _base_url)

    default_headers = kwargs.pop("default_headers", {})
    default_headers.setdefault("HTTP-Referer", "https://github.com/tnorlund/Portfolio")
    default_headers.setdefault("X-Title", "Receipt Agent")

    return ChatOpenAI(
        model=_model,
        base_url=_base_url,
        api_key=_api_key,
        temperature=temperature,
        timeout=timeout,
        default_headers=default_headers,
        **kwargs,
    )


def create_llm(
    provider: LLMProvider | str | None = None,
    model: str | None = None,
    base_url: str | None = None,
    api_key: str | None = None,
    temperature: float = 0.0,
    timeout: int = 120,
    **kwargs: Any,
) -> "BaseChatModel":
    """
    Create an LLM instance for the specified provider.

    Args:
        provider: LLM provider (default from LLM_PROVIDER env var)
        model: Model name (default from provider-specific env vars)
        base_url: API URL (default from provider-specific env vars)
        api_key: API key (default from provider-specific env vars)
        temperature: LLM temperature (default 0.0)
        timeout: Request timeout in seconds (default 120)
        **kwargs: Additional arguments passed to the underlying LLM class

    Returns:
        Configured LLM instance (ChatOllama or ChatOpenAI)
    """
    if provider is None:
        _provider = get_default_provider()
    elif isinstance(provider, str):
        try:
            _provider = LLMProvider(provider.lower())
        except ValueError as e:
            raise ValueError(
                f"Invalid provider '{provider}'. Must be one of: "
                f"{[p.value for p in LLMProvider]}"
            ) from e
    else:
        _provider = provider

    if _provider == LLMProvider.OLLAMA:
        return _create_ollama_llm(
            model=model,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            timeout=timeout,
            **kwargs,
        )
    elif _provider == LLMProvider.OPENROUTER:
        return _create_openrouter_llm(
            model=model,
            base_url=base_url,
            api_key=api_key,
            temperature=temperature,
            timeout=timeout,
            **kwargs,
        )
    else:
        raise ValueError(f"Unsupported provider: {_provider}")


def create_llm_from_settings(
    temperature: float = 0.0,
    timeout: int = 120,
    **kwargs: Any,
) -> "BaseChatModel":
    """Create an LLM instance using the application settings."""
    from receipt_agent.config.settings import get_settings

    settings = get_settings()
    provider = get_default_provider()

    if provider == LLMProvider.OLLAMA:
        return create_llm(
            provider=provider,
            model=settings.ollama_model,
            base_url=settings.ollama_base_url,
            api_key=settings.ollama_api_key.get_secret_value(),
            temperature=temperature,
            timeout=timeout,
            **kwargs,
        )
    elif provider == LLMProvider.OPENROUTER:
        return create_llm(
            provider=provider,
            model=settings.openrouter_model,
            base_url=settings.openrouter_base_url,
            api_key=settings.openrouter_api_key.get_secret_value(),
            temperature=temperature,
            timeout=timeout,
            **kwargs,
        )
    else:
        raise ValueError(f"Unsupported provider: {provider}")


# =============================================================================
# Resilient LLM with Automatic Fallback
# =============================================================================


class AllProvidersFailedError(Exception):
    """
    Raised when all LLM providers (Ollama, OpenRouter free, OpenRouter paid) fail.

    This error is designed to be caught by the circuit breaker and Step Function
    retry logic. It preserves information about all failures for debugging.
    """

    def __init__(
        self,
        message: str,
        errors: list[Exception],
    ):
        super().__init__(message)
        self.errors = errors

    @property
    def primary_error(self) -> Exception:
        """Backward compatibility: return first error."""
        return self.errors[0] if self.errors else Exception("Unknown error")

    @property
    def fallback_error(self) -> Exception:
        """Backward compatibility: return second error."""
        return self.errors[1] if len(self.errors) > 1 else self.primary_error


# Backward compatibility alias
BothProvidersFailedError = AllProvidersFailedError


@dataclass
class ResilientLLM:
    """
    LLM wrapper with automatic fallback: Ollama → OpenRouter free → OpenRouter paid.

    This class integrates with the existing rate limiting infrastructure:
    - If Ollama fails with rate limit → try OpenRouter free model
    - If OpenRouter free fails with rate limit → try OpenRouter paid model
    - If paid succeeds → return response (circuit breaker sees success)
    - If all three fail → raise AllProvidersFailedError
      (which triggers circuit breaker / Step Function retry)

    Usage:
        llm = ResilientLLM(
            primary_llm=create_llm(provider=LLMProvider.OLLAMA),
            fallback_free_llm=create_llm(provider=LLMProvider.OPENROUTER, model="...free"),
            fallback_paid_llm=create_llm(provider=LLMProvider.OPENROUTER, model="...paid"),
        )
        response = llm.invoke(messages)
    """

    primary_llm: Any  # BaseChatModel - Ollama
    fallback_free_llm: Any  # BaseChatModel - OpenRouter free model
    fallback_paid_llm: Optional[Any] = None  # BaseChatModel - OpenRouter paid model

    # Backward compatibility: accept fallback_llm as alias for fallback_free_llm
    fallback_llm: Any = field(default=None, repr=False)

    # Statistics
    primary_calls: int = field(default=0, init=False)
    primary_successes: int = field(default=0, init=False)
    fallback_free_calls: int = field(default=0, init=False)
    fallback_free_successes: int = field(default=0, init=False)
    fallback_paid_calls: int = field(default=0, init=False)
    fallback_paid_successes: int = field(default=0, init=False)
    all_failed: int = field(default=0, init=False)

    def __post_init__(self):
        """Handle backward compatibility for fallback_llm parameter."""
        if self.fallback_llm is not None and self.fallback_free_llm is None:
            self.fallback_free_llm = self.fallback_llm
        elif self.fallback_llm is not None and self.fallback_free_llm is not None:
            logger.warning(
                "Both fallback_llm and fallback_free_llm provided; "
                "fallback_llm is ignored"
            )
        elif self.fallback_free_llm is None:
            raise ValueError("fallback_free_llm is required")

    # Backward compatibility aliases
    @property
    def fallback_calls(self) -> int:
        return self.fallback_free_calls

    @property
    def fallback_successes(self) -> int:
        return self.fallback_free_successes

    @property
    def both_failed(self) -> int:
        return self.all_failed

    def invoke(
        self,
        messages: Any,
        config: Optional[dict] = None,
        **kwargs: Any,
    ) -> Any:
        """
        Invoke the LLM with automatic fallback through three tiers.

        Fallback chain: Ollama → OpenRouter free → OpenRouter paid

        Args:
            messages: Messages to send to the LLM (LangChain format)
            config: Optional LangChain config dict (for callbacks/tracing)
            **kwargs: Additional arguments passed to the LLM

        Returns:
            LLM response

        Raises:
            AllProvidersFailedError: If all providers fail with rate limits
            Exception: If primary fails with non-rate-limit error
        """
        errors: list[Exception] = []
        self.primary_calls += 1

        # Tier 1: Try primary (Ollama) first
        try:
            if config:
                response = self.primary_llm.invoke(messages, config=config, **kwargs)
            else:
                response = self.primary_llm.invoke(messages, **kwargs)
            self.primary_successes += 1
            return response

        except Exception as primary_error:
            errors.append(primary_error)

            # Check if this is a fallback-worthy error
            if not is_fallback_error(primary_error):
                logger.warning(
                    "Ollama failed with non-fallback error: %s",
                    str(primary_error)[:200],
                )
                raise

            logger.info(
                "Ollama rate limited, trying OpenRouter free: %s",
                str(primary_error)[:100],
            )

        # Tier 2: Try OpenRouter free model
        self.fallback_free_calls += 1
        try:
            if config:
                response = self.fallback_free_llm.invoke(
                    messages, config=config, **kwargs
                )
            else:
                response = self.fallback_free_llm.invoke(messages, **kwargs)
            self.fallback_free_successes += 1
            logger.info("OpenRouter free fallback succeeded")
            return response

        except Exception as free_error:
            errors.append(free_error)

            if not is_fallback_error(free_error):
                logger.error(
                    "OpenRouter free failed with non-fallback error: %s",
                    str(free_error)[:200],
                )
                raise

            # Check if we have a paid fallback
            if self.fallback_paid_llm is None:
                self.all_failed += 1
                logger.error(
                    "Both Ollama and OpenRouter free rate limited (no paid fallback). "
                    "Ollama: %s, OpenRouter: %s",
                    str(errors[0])[:100],
                    str(free_error)[:100],
                )
                raise AllProvidersFailedError(
                    "Ollama and OpenRouter free are rate limited",
                    errors=errors,
                ) from free_error

            logger.info(
                "OpenRouter free rate limited, trying OpenRouter paid: %s",
                str(free_error)[:100],
            )

        # Tier 3: Try OpenRouter paid model
        self.fallback_paid_calls += 1
        try:
            if config:
                response = self.fallback_paid_llm.invoke(
                    messages, config=config, **kwargs
                )
            else:
                response = self.fallback_paid_llm.invoke(messages, **kwargs)
            self.fallback_paid_successes += 1
            logger.info("OpenRouter paid fallback succeeded")
            return response

        except Exception as paid_error:
            errors.append(paid_error)
            self.all_failed += 1

            logger.error(
                "All providers failed. Ollama: %s, Free: %s, Paid: %s",
                str(errors[0])[:80],
                str(errors[1])[:80],
                str(paid_error)[:80],
            )
            raise AllProvidersFailedError(
                "All LLM providers failed (Ollama, OpenRouter free, OpenRouter paid)",
                errors=errors,
            ) from paid_error

    async def ainvoke(
        self,
        messages: Any,
        config: Optional[dict] = None,
        **kwargs: Any,
    ) -> Any:
        """
        Async invoke with automatic fallback through three tiers.

        Fallback chain: Ollama → OpenRouter free → OpenRouter paid
        """
        errors: list[Exception] = []
        self.primary_calls += 1

        # Tier 1: Try primary (Ollama) first
        try:
            if config:
                response = await self.primary_llm.ainvoke(
                    messages, config=config, **kwargs
                )
            else:
                response = await self.primary_llm.ainvoke(messages, **kwargs)
            self.primary_successes += 1
            return response

        except Exception as primary_error:
            errors.append(primary_error)

            if not is_fallback_error(primary_error):
                logger.warning(
                    "Ollama failed with non-fallback error: %s",
                    str(primary_error)[:200],
                )
                raise

            logger.info(
                "Ollama rate limited, trying OpenRouter free: %s",
                str(primary_error)[:100],
            )

        # Tier 2: Try OpenRouter free model
        self.fallback_free_calls += 1
        try:
            if config:
                response = await self.fallback_free_llm.ainvoke(
                    messages, config=config, **kwargs
                )
            else:
                response = await self.fallback_free_llm.ainvoke(messages, **kwargs)
            self.fallback_free_successes += 1
            logger.info("OpenRouter free fallback succeeded")
            return response

        except Exception as free_error:
            errors.append(free_error)

            if not is_fallback_error(free_error):
                logger.error(
                    "OpenRouter free failed with non-fallback error: %s",
                    str(free_error)[:200],
                )
                raise

            # Check if we have a paid fallback
            if self.fallback_paid_llm is None:
                self.all_failed += 1
                logger.error(
                    "Both Ollama and OpenRouter free rate limited (no paid fallback). "
                    "Ollama: %s, OpenRouter: %s",
                    str(errors[0])[:100],
                    str(free_error)[:100],
                )
                raise AllProvidersFailedError(
                    "Ollama and OpenRouter free are rate limited",
                    errors=errors,
                ) from free_error

            logger.info(
                "OpenRouter free rate limited, trying OpenRouter paid: %s",
                str(free_error)[:100],
            )

        # Tier 3: Try OpenRouter paid model
        self.fallback_paid_calls += 1
        try:
            if config:
                response = await self.fallback_paid_llm.ainvoke(
                    messages, config=config, **kwargs
                )
            else:
                response = await self.fallback_paid_llm.ainvoke(messages, **kwargs)
            self.fallback_paid_successes += 1
            logger.info("OpenRouter paid fallback succeeded")
            return response

        except Exception as paid_error:
            errors.append(paid_error)
            self.all_failed += 1

            logger.error(
                "All providers failed. Ollama: %s, Free: %s, Paid: %s",
                str(errors[0])[:80],
                str(errors[1])[:80],
                str(paid_error)[:80],
            )
            raise AllProvidersFailedError(
                "All LLM providers failed (Ollama, OpenRouter free, OpenRouter paid)",
                errors=errors,
            ) from paid_error

    def get_stats(self) -> dict[str, Any]:
        """Get statistics on LLM usage across all three tiers."""
        total = self.primary_calls
        total_successes = (
            self.primary_successes
            + self.fallback_free_successes
            + self.fallback_paid_successes
        )
        return {
            "primary_calls": self.primary_calls,
            "primary_successes": self.primary_successes,
            "fallback_free_calls": self.fallback_free_calls,
            "fallback_free_successes": self.fallback_free_successes,
            "fallback_paid_calls": self.fallback_paid_calls,
            "fallback_paid_successes": self.fallback_paid_successes,
            "all_failed": self.all_failed,
            # Backward compatibility
            "fallback_calls": self.fallback_free_calls,
            "fallback_successes": self.fallback_free_successes,
            "both_failed": self.all_failed,
            # Computed rates
            "fallback_rate": self.fallback_free_calls / total if total > 0 else 0.0,
            "paid_fallback_rate": (
                self.fallback_paid_calls / total if total > 0 else 0.0
            ),
            "overall_success_rate": total_successes / total if total > 0 else 1.0,
        }


def create_resilient_llm(
    temperature: float = 0.0,
    timeout: int = 120,
    **kwargs: Any,
) -> ResilientLLM:
    """
    Create a resilient LLM with three-tier fallback.

    Fallback chain: Ollama → OpenRouter free → OpenRouter paid

    Args:
        temperature: LLM temperature (default 0.0)
        timeout: Request timeout in seconds (default 120)
        **kwargs: Additional arguments passed to all LLMs

    Returns:
        ResilientLLM instance with automatic failover

    Environment Variables:
        OPENROUTER_MODEL: Free model (default: openai/gpt-oss-120b:free)
        OPENROUTER_PAID_MODEL: Paid model (default: openai/gpt-oss-120b)
    """
    # Tier 1: Ollama (primary)
    primary_llm = create_llm(
        provider=LLMProvider.OLLAMA,
        temperature=temperature,
        timeout=timeout,
        **kwargs,
    )

    # Get model names from environment
    free_model = os.environ.get("OPENROUTER_MODEL", "openai/gpt-oss-120b:free")
    paid_model = os.environ.get("OPENROUTER_PAID_MODEL", "openai/gpt-oss-120b")

    # Tier 2: OpenRouter free model
    fallback_free_llm = create_llm(
        provider=LLMProvider.OPENROUTER,
        model=free_model,
        temperature=temperature,
        timeout=timeout,
        **kwargs,
    )

    # Tier 3: OpenRouter paid model (only if different from free)
    fallback_paid_llm = None
    if paid_model and paid_model != free_model:
        fallback_paid_llm = create_llm(
            provider=LLMProvider.OPENROUTER,
            model=paid_model,
            temperature=temperature,
            timeout=timeout,
            **kwargs,
        )
        logger.info(
            "Created three-tier resilient LLM: Ollama → %s → %s",
            free_model,
            paid_model,
        )
    else:
        logger.info(
            "Created two-tier resilient LLM: Ollama → %s (no paid fallback)",
            free_model,
        )

    return ResilientLLM(
        primary_llm=primary_llm,
        fallback_free_llm=fallback_free_llm,
        fallback_paid_llm=fallback_paid_llm,
    )


# =============================================================================
# Production Invoker - Full Stack Integration
# =============================================================================


def create_production_invoker(
    temperature: float = 0.0,
    timeout: int = 120,
    circuit_breaker_threshold: int = 5,
    max_jitter_seconds: float = 0.25,
    **kwargs: Any,
) -> "RateLimitedLLMInvoker":
    """
    Create a production-ready LLM invoker with full resilience stack.

    This combines:
    1. ResilientLLM: Automatic Ollama → OpenRouter fallback
    2. RateLimitedLLMInvoker: Jitter between calls + circuit breaker
    3. OllamaCircuitBreaker: Tracks consecutive failures

    The stack works as follows:
    - Each invoke() adds random jitter to prevent thundering herd
    - Tries Ollama first; if rate limited, tries OpenRouter
    - If both fail, circuit breaker counts the failure
    - After N consecutive failures, raises OllamaRateLimitError
    - Step Function catches this and retries with 30s backoff

    Args:
        temperature: LLM temperature (default 0.0)
        timeout: Request timeout in seconds (default 120)
        circuit_breaker_threshold: Failures before circuit trips (default 5)
        max_jitter_seconds: Max random delay between calls (default 0.25s)
        **kwargs: Additional arguments passed to LLMs

    Returns:
        Configured RateLimitedLLMInvoker with ResilientLLM

    Example:
        invoker = create_production_invoker()

        for issue in issues:
            try:
                response = invoker.invoke(prompt)
            except OllamaRateLimitError:
                # Circuit breaker tripped - let Step Function retry
                raise
    """
    from receipt_agent.utils.ollama_rate_limit import (
        OllamaCircuitBreaker,
        RateLimitedLLMInvoker,
    )

    # Create the resilient LLM (Ollama + OpenRouter fallback)
    resilient_llm = create_resilient_llm(
        temperature=temperature,
        timeout=timeout,
        **kwargs,
    )

    # Create circuit breaker
    circuit_breaker = OllamaCircuitBreaker(threshold=circuit_breaker_threshold)

    # Wrap in rate-limited invoker
    return RateLimitedLLMInvoker(
        llm=resilient_llm,
        circuit_breaker=circuit_breaker,
        max_jitter_seconds=max_jitter_seconds,
    )
