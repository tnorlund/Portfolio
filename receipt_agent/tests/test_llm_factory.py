"""Tests for LLM Factory module - OpenRouter-only implementation."""

import asyncio
import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from receipt_agent.utils.llm_factory import (
    # Primary exports
    LLMRateLimitError,
    LLMInvoker,
    EmptyResponseError,
    create_llm,
    create_llm_invoker,
    create_production_invoker,
    is_rate_limit_error,
    is_service_error,
    is_retriable_error,
    is_timeout_error,
    # Backward compatibility aliases
    OllamaRateLimitError,
    BothProvidersFailedError,
    AllProvidersFailedError,
    RateLimitedLLMInvoker,
    ResilientLLM,
    OllamaCircuitBreaker,
    LLMProvider,
    create_resilient_llm,
    get_default_provider,
    is_fallback_error,
)


class TestLLMRateLimitError:
    """Tests for LLMRateLimitError exception."""

    def test_basic_creation(self):
        """Test creating error with message."""
        error = LLMRateLimitError("Rate limit hit")
        assert str(error) == "Rate limit hit"
        assert error.consecutive_errors == 0
        assert error.total_errors == 0

    def test_creation_with_stats(self):
        """Test creating error with error counts."""
        error = LLMRateLimitError(
            "Rate limit hit",
            consecutive_errors=3,
            total_errors=5,
        )
        assert error.consecutive_errors == 3
        assert error.total_errors == 5


class TestBackwardCompatibilityAliases:
    """Tests for backward compatibility aliases."""

    def test_ollama_rate_limit_error_alias(self):
        """Test OllamaRateLimitError is alias for LLMRateLimitError."""
        assert OllamaRateLimitError is LLMRateLimitError

    def test_both_providers_failed_error_alias(self):
        """Test BothProvidersFailedError is alias for LLMRateLimitError."""
        assert BothProvidersFailedError is LLMRateLimitError

    def test_all_providers_failed_error_alias(self):
        """Test AllProvidersFailedError is alias for LLMRateLimitError."""
        assert AllProvidersFailedError is LLMRateLimitError

    def test_rate_limited_invoker_alias(self):
        """Test RateLimitedLLMInvoker is alias for LLMInvoker."""
        assert RateLimitedLLMInvoker is LLMInvoker

    def test_llm_provider_class(self):
        """Test LLMProvider class exists with string values."""
        assert LLMProvider.OLLAMA == "ollama"
        assert LLMProvider.OPENROUTER == "openrouter"

    def test_get_default_provider_returns_openrouter(self):
        """Test get_default_provider returns openrouter."""
        assert get_default_provider() == "openrouter"

    def test_is_fallback_error_alias(self):
        """Test is_fallback_error is alias for is_retriable_error."""
        assert is_fallback_error is is_retriable_error


class TestErrorDetection:
    """Tests for error detection functions."""

    def test_is_rate_limit_error_429(self):
        """Test that 429 errors are detected as rate limit."""
        assert is_rate_limit_error(Exception("HTTP 429: Too Many Requests"))

    def test_is_rate_limit_error_text(self):
        """Test that 'rate limit' text is detected."""
        assert is_rate_limit_error(Exception("Rate limit exceeded"))

    def test_is_rate_limit_error_too_many_requests(self):
        """Test that 'too many requests' is detected."""
        assert is_rate_limit_error(Exception("too many concurrent requests"))

    def test_is_rate_limit_error_capacity(self):
        """Test that 'capacity' is detected."""
        assert is_rate_limit_error(Exception("Server at capacity"))

    def test_is_rate_limit_error_llm_rate_limit_error(self):
        """Test that LLMRateLimitError is detected."""
        assert is_rate_limit_error(LLMRateLimitError("Test"))

    def test_is_rate_limit_error_non_rate_limit(self):
        """Test that non-rate-limit errors are not detected."""
        assert not is_rate_limit_error(Exception("Invalid API key"))

    def test_is_service_error_503(self):
        """Test that 503 errors are detected as service errors."""
        assert is_service_error(Exception("503 Service Unavailable"))

    def test_is_service_error_502(self):
        """Test that 502 errors are detected as service errors."""
        assert is_service_error(Exception("502 Bad Gateway"))

    def test_is_service_error_500(self):
        """Test that 500 errors are detected as service errors."""
        assert is_service_error(Exception("500 Internal Server Error"))

    def test_is_service_error_504(self):
        """Test that 504 errors are detected as service errors."""
        assert is_service_error(Exception("504 Gateway Timeout"))

    def test_is_service_error_non_service(self):
        """Test that non-service errors are not detected."""
        assert not is_service_error(Exception("Invalid API key"))

    def test_is_timeout_error_timeout(self):
        """Test that timeout errors are detected."""
        assert is_timeout_error(Exception("Request timed out"))
        assert is_timeout_error(Exception("Connection timeout"))

    def test_is_timeout_error_non_timeout(self):
        """Test that non-timeout errors are not detected."""
        assert not is_timeout_error(Exception("Invalid API key"))

    def test_is_retriable_error_rate_limit(self):
        """Test that rate limit errors are retriable."""
        assert is_retriable_error(Exception("429 Too Many Requests"))

    def test_is_retriable_error_service(self):
        """Test that service errors are retriable."""
        assert is_retriable_error(Exception("503 Service Unavailable"))

    def test_is_retriable_error_non_retriable(self):
        """Test that other errors are not retriable."""
        assert not is_retriable_error(Exception("Invalid API key"))


class TestCreateLLM:
    """Tests for create_llm function."""

    @patch("receipt_agent.utils.llm_factory.ChatOpenAI")
    def test_creates_openrouter_with_env_vars(self, mock_chat_openai):
        """Test that create_llm creates ChatOpenAI for OpenRouter."""
        mock_chat_openai.return_value = MagicMock()

        with patch.dict(os.environ, {
            "OPENROUTER_MODEL": "openai/gpt-oss-120b",
            "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
            "OPENROUTER_API_KEY": "or_test_key",
        }):
            _ = create_llm()

        mock_chat_openai.assert_called_once()
        call_kwargs = mock_chat_openai.call_args[1]
        assert call_kwargs["model"] == "openai/gpt-oss-120b"
        assert call_kwargs["base_url"] == "https://openrouter.ai/api/v1"
        assert call_kwargs["api_key"] == "or_test_key"
        assert call_kwargs["temperature"] == 0.0

    @patch("receipt_agent.utils.llm_factory.ChatOpenAI")
    def test_creates_with_custom_params(self, mock_chat_openai):
        """Test that create_llm respects custom parameters."""
        mock_chat_openai.return_value = MagicMock()

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            _ = create_llm(
                model="custom-model",
                temperature=0.7,
                timeout=300,
            )

        call_kwargs = mock_chat_openai.call_args[1]
        assert call_kwargs["model"] == "custom-model"
        assert call_kwargs["temperature"] == 0.7
        assert call_kwargs["timeout"] == 300

    @patch("receipt_agent.utils.llm_factory.ChatOpenAI")
    def test_includes_openrouter_headers(self, mock_chat_openai):
        """Test that create_llm includes OpenRouter-specific headers."""
        mock_chat_openai.return_value = MagicMock()

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            _ = create_llm()

        call_kwargs = mock_chat_openai.call_args[1]
        headers = call_kwargs.get("default_headers", {})
        assert "HTTP-Referer" in headers
        assert "X-Title" in headers

    def test_raises_without_api_key(self):
        """Test that create_llm raises error without API key."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("OPENROUTER_API_KEY", None)
            os.environ.pop("RECEIPT_AGENT_OPENROUTER_API_KEY", None)

            with pytest.raises(ValueError, match="OpenRouter API key is required"):
                create_llm()

    @patch("receipt_agent.utils.llm_factory.ChatOpenAI")
    def test_uses_receipt_agent_prefix_env_vars(self, mock_chat_openai):
        """Test that RECEIPT_AGENT_ prefixed env vars are used."""
        mock_chat_openai.return_value = MagicMock()

        with patch.dict(os.environ, {
            "RECEIPT_AGENT_OPENROUTER_MODEL": "prefixed-model",
            "RECEIPT_AGENT_OPENROUTER_BASE_URL": "https://prefixed.url",
            "RECEIPT_AGENT_OPENROUTER_API_KEY": "prefixed-key",
        }, clear=True):
            os.environ.pop("OPENROUTER_MODEL", None)
            os.environ.pop("OPENROUTER_BASE_URL", None)
            os.environ.pop("OPENROUTER_API_KEY", None)
            _ = create_llm()

        call_kwargs = mock_chat_openai.call_args[1]
        assert call_kwargs["model"] == "prefixed-model"
        assert call_kwargs["base_url"] == "https://prefixed.url"
        assert call_kwargs["api_key"] == "prefixed-key"


class TestLLMInvoker:
    """Tests for LLMInvoker class."""

    def test_invoke_success(self):
        """Test successful invoke."""
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "test response"
        mock_llm.invoke.return_value = mock_response

        invoker = LLMInvoker(llm=mock_llm)
        response = invoker.invoke("test message")

        assert response == mock_response
        mock_llm.invoke.assert_called_once()
        assert invoker.call_count == 1
        assert invoker.consecutive_errors == 0

    def test_invoke_with_config(self):
        """Test invoke with config dict."""
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "test response"
        mock_llm.invoke.return_value = mock_response

        invoker = LLMInvoker(llm=mock_llm)
        config = {"callbacks": []}
        response = invoker.invoke("test message", config=config)

        assert response == mock_response
        mock_llm.invoke.assert_called_once_with("test message", config=config)

    def test_invoke_retry_on_rate_limit(self):
        """Test that rate limit errors trigger retry."""
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "success"

        # First call fails, second succeeds
        mock_llm.invoke.side_effect = [
            Exception("429 Rate Limit"),
            mock_response,
        ]

        invoker = LLMInvoker(llm=mock_llm, max_jitter_seconds=0)
        response = invoker.invoke("test message")

        assert response == mock_response
        assert mock_llm.invoke.call_count == 2
        assert invoker.total_errors == 1
        assert invoker.consecutive_errors == 0  # Reset after success

    def test_invoke_retry_exhausted(self):
        """Test that exhausted retries raise LLMRateLimitError."""
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = Exception("429 Rate Limit")

        invoker = LLMInvoker(llm=mock_llm, max_retries=2, max_jitter_seconds=0)

        with pytest.raises(LLMRateLimitError):
            invoker.invoke("test message")

        assert mock_llm.invoke.call_count == 2
        assert invoker.consecutive_errors == 2

    def test_invoke_non_retriable_error(self):
        """Test that non-retriable errors are raised immediately."""
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = Exception("Invalid API key")

        invoker = LLMInvoker(llm=mock_llm, max_retries=3, max_jitter_seconds=0)

        with pytest.raises(Exception, match="Invalid API key"):
            invoker.invoke("test message")

        # Should fail immediately without retries
        assert mock_llm.invoke.call_count == 1

    def test_invoke_empty_response(self):
        """Test that empty responses trigger retry."""
        mock_llm = MagicMock()
        mock_empty_response = MagicMock()
        mock_empty_response.content = ""
        mock_success_response = MagicMock()
        mock_success_response.content = "success"

        mock_llm.invoke.side_effect = [
            mock_empty_response,
            mock_success_response,
        ]

        invoker = LLMInvoker(llm=mock_llm, max_jitter_seconds=0)
        response = invoker.invoke("test message")

        assert response == mock_success_response
        assert mock_llm.invoke.call_count == 2

    def test_with_structured_output(self):
        """Test with_structured_output creates new invoker."""
        mock_llm = MagicMock()
        mock_structured_llm = MagicMock()
        mock_llm.with_structured_output.return_value = mock_structured_llm

        invoker = LLMInvoker(llm=mock_llm, max_jitter_seconds=0.5)
        structured_invoker = invoker.with_structured_output(dict)

        assert isinstance(structured_invoker, LLMInvoker)
        assert structured_invoker.llm == mock_structured_llm
        assert structured_invoker.max_jitter_seconds == 0.5
        mock_llm.with_structured_output.assert_called_once_with(dict)

    def test_get_stats(self):
        """Test get_stats returns correct statistics."""
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "test"
        mock_llm.invoke.return_value = mock_response

        invoker = LLMInvoker(
            llm=mock_llm,
            max_jitter_seconds=0.5,
            max_retries=5,
        )
        invoker.invoke("test")

        stats = invoker.get_stats()
        assert stats["call_count"] == 1
        assert stats["consecutive_errors"] == 0
        assert stats["total_errors"] == 0
        assert stats["max_jitter_seconds"] == 0.5
        assert stats["max_retries"] == 5


class TestLLMInvokerAsync:
    """Tests for async LLMInvoker methods."""

    @pytest.mark.asyncio
    async def test_ainvoke_success(self):
        """Test successful async invoke."""
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "test response"
        mock_llm.ainvoke = AsyncMock(return_value=mock_response)

        invoker = LLMInvoker(llm=mock_llm, max_jitter_seconds=0)
        response = await invoker.ainvoke("test message")

        assert response == mock_response
        mock_llm.ainvoke.assert_called_once()
        assert invoker.call_count == 1

    @pytest.mark.asyncio
    async def test_ainvoke_retry_on_rate_limit(self):
        """Test that async rate limit errors trigger retry."""
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "success"

        mock_llm.ainvoke = AsyncMock(side_effect=[
            Exception("429 Rate Limit"),
            mock_response,
        ])

        invoker = LLMInvoker(llm=mock_llm, max_jitter_seconds=0)
        response = await invoker.ainvoke("test message")

        assert response == mock_response
        assert mock_llm.ainvoke.call_count == 2


class TestCreateLLMInvoker:
    """Tests for create_llm_invoker function."""

    @patch("receipt_agent.utils.llm_factory.create_llm")
    def test_creates_invoker(self, mock_create_llm):
        """Test that create_llm_invoker creates an LLMInvoker."""
        mock_llm = MagicMock()
        mock_create_llm.return_value = mock_llm

        invoker = create_llm_invoker(
            model="test-model",
            temperature=0.5,
            max_jitter_seconds=0.3,
            max_retries=5,
        )

        assert isinstance(invoker, LLMInvoker)
        assert invoker.llm == mock_llm
        assert invoker.max_jitter_seconds == 0.3
        assert invoker.max_retries == 5
        mock_create_llm.assert_called_once_with(
            model="test-model",
            temperature=0.5,
            timeout=120,
        )


class TestCreateProductionInvoker:
    """Tests for create_production_invoker function."""

    @patch("receipt_agent.utils.llm_factory.create_llm_invoker")
    def test_calls_create_llm_invoker(self, mock_create_invoker):
        """Test that create_production_invoker delegates to create_llm_invoker."""
        mock_invoker = MagicMock()
        mock_create_invoker.return_value = mock_invoker

        invoker = create_production_invoker(
            temperature=0.5,
            timeout=60,
            circuit_breaker_threshold=3,  # Ignored
            max_jitter_seconds=0.5,
        )

        assert invoker == mock_invoker
        mock_create_invoker.assert_called_once_with(
            temperature=0.5,
            timeout=60,
            max_jitter_seconds=0.5,
        )


class TestCreateResilientLLM:
    """Tests for create_resilient_llm function."""

    @patch("receipt_agent.utils.llm_factory.create_llm_invoker")
    def test_calls_create_llm_invoker(self, mock_create_invoker):
        """Test that create_resilient_llm delegates to create_llm_invoker."""
        mock_invoker = MagicMock()
        mock_create_invoker.return_value = mock_invoker

        invoker = create_resilient_llm(
            temperature=0.5,
            timeout=60,
        )

        assert invoker == mock_invoker
        mock_create_invoker.assert_called_once_with(
            temperature=0.5,
            timeout=60,
        )


class TestResilientLLMBackwardCompat:
    """Tests for ResilientLLM backward compatibility class."""

    def test_primary_success(self):
        """Test that successful primary call works."""
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "response"
        mock_llm.invoke.return_value = mock_response

        resilient = ResilientLLM(primary_llm=mock_llm)
        response = resilient.invoke("test message")

        assert response == mock_response
        assert resilient.primary_calls == 1
        assert resilient.primary_successes == 1

    def test_fallback_llm_ignored(self):
        """Test that fallback_llm parameter is accepted but ignored."""
        mock_primary = MagicMock()
        mock_fallback = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "response"
        mock_primary.invoke.return_value = mock_response

        # Should not raise even though fallback is provided
        resilient = ResilientLLM(
            primary_llm=mock_primary,
            fallback_llm=mock_fallback,
        )
        response = resilient.invoke("test")

        assert response == mock_response
        mock_fallback.invoke.assert_not_called()

    def test_stats_backward_compat(self):
        """Test that get_stats returns backward-compatible keys."""
        mock_llm = MagicMock()
        mock_response = MagicMock()
        mock_response.content = "response"
        mock_llm.invoke.return_value = mock_response

        resilient = ResilientLLM(primary_llm=mock_llm)
        resilient.invoke("test")

        stats = resilient.get_stats()
        # Check backward-compatible keys exist
        assert "primary_calls" in stats
        assert "primary_successes" in stats
        assert "fallback_calls" in stats
        assert "fallback_successes" in stats
        assert "both_failed" in stats
        assert "fallback_rate" in stats
        assert "overall_success_rate" in stats


class TestOllamaCircuitBreakerBackwardCompat:
    """Tests for OllamaCircuitBreaker backward compatibility class."""

    def test_record_success(self):
        """Test recording a successful call."""
        breaker = OllamaCircuitBreaker(threshold=3)
        breaker.consecutive_errors = 2

        breaker.record_success()

        assert breaker.consecutive_errors == 0
        assert not breaker.triggered

    def test_record_rate_limit_error(self):
        """Test recording a rate limit error."""
        breaker = OllamaCircuitBreaker(threshold=3)

        with pytest.raises(LLMRateLimitError):
            breaker.record_error(Exception("429 Rate Limit"))

        assert breaker.consecutive_errors == 1
        assert breaker.total_rate_limit_errors == 1
        assert not breaker.triggered

    def test_circuit_breaker_triggers(self):
        """Test that circuit breaker triggers after threshold."""
        breaker = OllamaCircuitBreaker(threshold=2)

        with pytest.raises(LLMRateLimitError):
            breaker.record_error(Exception("429"))

        with pytest.raises(LLMRateLimitError):
            breaker.record_error(Exception("429"))

        assert breaker.consecutive_errors == 2
        assert breaker.triggered

    def test_non_rate_limit_error_resets(self):
        """Test that non-rate-limit errors reset consecutive count."""
        breaker = OllamaCircuitBreaker(threshold=3)
        breaker.consecutive_errors = 2

        # Record a non-rate-limit, non-service error
        breaker.record_error(Exception("Invalid API key"))

        assert breaker.consecutive_errors == 0

    def test_get_stats(self):
        """Test get_stats returns circuit breaker statistics."""
        breaker = OllamaCircuitBreaker(threshold=5)

        stats = breaker.get_stats()
        assert stats["threshold"] == 5
        assert stats["consecutive_errors"] == 0
        assert stats["total_rate_limit_errors"] == 0
        assert stats["triggered"] is False


class TestEmptyResponseError:
    """Tests for EmptyResponseError exception."""

    def test_basic_creation(self):
        """Test creating error with default message."""
        error = EmptyResponseError()
        assert "OpenRouter" in str(error)
        assert error.provider == "OpenRouter"

    def test_custom_provider(self):
        """Test creating error with custom provider."""
        error = EmptyResponseError(provider="TestProvider", message="Custom message")
        assert "TestProvider" in str(error)
        assert "Custom message" in str(error)
        assert error.provider == "TestProvider"
