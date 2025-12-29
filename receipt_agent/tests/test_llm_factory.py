"""Tests for LLM Factory module."""

import os
from unittest.mock import MagicMock, patch

import pytest

from receipt_agent.utils.llm_factory import (
    BothProvidersFailedError,
    LLMProvider,
    ResilientLLM,
    create_llm,
    create_production_invoker,
    create_resilient_llm,
    get_default_provider,
    is_fallback_error,
    is_rate_limit_error,
    is_service_error,
)


class TestLLMProvider:
    """Tests for LLMProvider enum."""

    def test_ollama_value(self):
        """Test OLLAMA enum value."""
        assert LLMProvider.OLLAMA.value == "ollama"

    def test_openrouter_value(self):
        """Test OPENROUTER enum value."""
        assert LLMProvider.OPENROUTER.value == "openrouter"

    def test_from_string(self):
        """Test creating provider from string."""
        assert LLMProvider("ollama") == LLMProvider.OLLAMA
        assert LLMProvider("openrouter") == LLMProvider.OPENROUTER


class TestGetDefaultProvider:
    """Tests for get_default_provider function."""

    def test_default_is_ollama(self):
        """Test that default provider is ollama when env not set."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("LLM_PROVIDER", None)
            provider = get_default_provider()
            assert provider == LLMProvider.OLLAMA

    def test_env_ollama(self):
        """Test that env var 'ollama' returns OLLAMA provider."""
        with patch.dict(os.environ, {"LLM_PROVIDER": "ollama"}):
            provider = get_default_provider()
            assert provider == LLMProvider.OLLAMA

    def test_env_openrouter(self):
        """Test that env var 'openrouter' returns OPENROUTER provider."""
        with patch.dict(os.environ, {"LLM_PROVIDER": "openrouter"}):
            provider = get_default_provider()
            assert provider == LLMProvider.OPENROUTER

    def test_env_case_insensitive(self):
        """Test that env var is case insensitive."""
        with patch.dict(os.environ, {"LLM_PROVIDER": "OPENROUTER"}):
            provider = get_default_provider()
            assert provider == LLMProvider.OPENROUTER

    def test_invalid_env_defaults_to_ollama(self):
        """Test that invalid env var defaults to ollama."""
        with patch.dict(os.environ, {"LLM_PROVIDER": "invalid_provider"}):
            provider = get_default_provider()
            assert provider == LLMProvider.OLLAMA


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

    def test_is_service_error_503(self):
        """Test that 503 errors are detected as service errors."""
        assert is_service_error(Exception("503 Service Unavailable"))

    def test_is_service_error_502(self):
        """Test that 502 errors are detected as service errors."""
        assert is_service_error(Exception("502 Bad Gateway"))

    def test_is_fallback_error_rate_limit(self):
        """Test that rate limit errors trigger fallback."""
        assert is_fallback_error(Exception("429 Too Many Requests"))

    def test_is_fallback_error_service(self):
        """Test that service errors trigger fallback."""
        assert is_fallback_error(Exception("503 Service Unavailable"))

    def test_is_fallback_error_non_fallback(self):
        """Test that other errors don't trigger fallback."""
        assert not is_fallback_error(Exception("Invalid API key"))

    def test_is_fallback_error_timeout(self):
        """Test that timeout errors don't trigger fallback."""
        assert not is_fallback_error(Exception("Request timed out"))


class TestCreateLLMOllama:
    """Tests for create_llm with Ollama provider."""

    @patch("receipt_agent.utils.llm_factory.ChatOllama")
    def test_creates_ollama_with_defaults(self, mock_chat_ollama):
        """Test that create_llm creates ChatOllama with default settings."""
        mock_chat_ollama.return_value = MagicMock()

        with patch.dict(os.environ, {
            "OLLAMA_MODEL": "test-model",
            "OLLAMA_BASE_URL": "https://test.ollama.com",
            "OLLAMA_API_KEY": "test-key",
        }):
            _ = create_llm(provider=LLMProvider.OLLAMA)

        mock_chat_ollama.assert_called_once()
        call_kwargs = mock_chat_ollama.call_args[1]
        assert call_kwargs["model"] == "test-model"
        assert call_kwargs["base_url"] == "https://test.ollama.com"
        assert call_kwargs["temperature"] == 0.0

    @patch("receipt_agent.utils.llm_factory.ChatOllama")
    def test_creates_ollama_with_custom_params(self, mock_chat_ollama):
        """Test that create_llm respects custom parameters."""
        mock_chat_ollama.return_value = MagicMock()

        _ = create_llm(
            provider=LLMProvider.OLLAMA,
            model="custom-model",
            base_url="https://custom.url",
            api_key="custom-key",
            temperature=0.7,
            timeout=300,
        )

        mock_chat_ollama.assert_called_once()
        call_kwargs = mock_chat_ollama.call_args[1]
        assert call_kwargs["model"] == "custom-model"
        assert call_kwargs["base_url"] == "https://custom.url"
        assert call_kwargs["temperature"] == 0.7


class TestCreateLLMOpenRouter:
    """Tests for create_llm with OpenRouter provider."""

    @patch("receipt_agent.utils.llm_factory.ChatOpenAI")
    def test_creates_openrouter_with_settings(self, mock_chat_openai):
        """Test that create_llm creates ChatOpenAI for OpenRouter."""
        mock_chat_openai.return_value = MagicMock()

        with patch.dict(os.environ, {
            "OPENROUTER_MODEL": "openai/gpt-oss-120b:free",
            "OPENROUTER_BASE_URL": "https://openrouter.ai/api/v1",
            "OPENROUTER_API_KEY": "or_test_key",
        }):
            _ = create_llm(provider=LLMProvider.OPENROUTER)

        mock_chat_openai.assert_called_once()
        call_kwargs = mock_chat_openai.call_args[1]
        assert call_kwargs["model"] == "openai/gpt-oss-120b:free"
        assert call_kwargs["base_url"] == "https://openrouter.ai/api/v1"
        assert call_kwargs["api_key"] == "or_test_key"

    def test_raises_without_api_key(self):
        """Test that create_llm raises error without OpenRouter API key."""
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("OPENROUTER_API_KEY", None)
            os.environ.pop("RECEIPT_AGENT_OPENROUTER_API_KEY", None)

            with pytest.raises(ValueError, match="OpenRouter API key is required"):
                create_llm(provider=LLMProvider.OPENROUTER)

    @patch("receipt_agent.utils.llm_factory.ChatOpenAI")
    def test_includes_openrouter_headers(self, mock_chat_openai):
        """Test that create_llm includes OpenRouter-specific headers."""
        mock_chat_openai.return_value = MagicMock()

        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "test-key"}):
            _ = create_llm(provider=LLMProvider.OPENROUTER)

        call_kwargs = mock_chat_openai.call_args[1]
        headers = call_kwargs.get("default_headers", {})
        assert "HTTP-Referer" in headers
        assert "X-Title" in headers


class TestResilientLLM:
    """Tests for ResilientLLM class."""

    def test_primary_success(self):
        """Test that successful primary call doesn't use fallback."""
        primary_llm = MagicMock()
        primary_llm.invoke.return_value = "primary response"
        fallback_llm = MagicMock()

        resilient = ResilientLLM(primary_llm=primary_llm, fallback_llm=fallback_llm)
        response = resilient.invoke("test message")

        assert response == "primary response"
        primary_llm.invoke.assert_called_once()
        fallback_llm.invoke.assert_not_called()
        assert resilient.primary_calls == 1
        assert resilient.primary_successes == 1
        assert resilient.fallback_calls == 0

    def test_fallback_on_rate_limit(self):
        """Test that rate limit error triggers fallback."""
        primary_llm = MagicMock()
        primary_llm.invoke.side_effect = Exception("429 Too Many Requests")
        fallback_llm = MagicMock()
        fallback_llm.invoke.return_value = "fallback response"

        resilient = ResilientLLM(primary_llm=primary_llm, fallback_llm=fallback_llm)
        response = resilient.invoke("test message")

        assert response == "fallback response"
        primary_llm.invoke.assert_called_once()
        fallback_llm.invoke.assert_called_once()
        assert resilient.primary_calls == 1
        assert resilient.primary_successes == 0
        assert resilient.fallback_calls == 1
        assert resilient.fallback_successes == 1

    def test_fallback_on_service_error(self):
        """Test that service error (503) triggers fallback."""
        primary_llm = MagicMock()
        primary_llm.invoke.side_effect = Exception("503 Service Unavailable")
        fallback_llm = MagicMock()
        fallback_llm.invoke.return_value = "fallback response"

        resilient = ResilientLLM(primary_llm=primary_llm, fallback_llm=fallback_llm)
        response = resilient.invoke("test message")

        assert response == "fallback response"
        assert resilient.fallback_successes == 1

    def test_no_fallback_on_auth_error(self):
        """Test that auth errors don't trigger fallback."""
        primary_llm = MagicMock()
        primary_llm.invoke.side_effect = Exception("Invalid API key")
        fallback_llm = MagicMock()

        resilient = ResilientLLM(primary_llm=primary_llm, fallback_llm=fallback_llm)

        with pytest.raises(Exception, match="Invalid API key"):
            resilient.invoke("test message")

        primary_llm.invoke.assert_called_once()
        fallback_llm.invoke.assert_not_called()

    def test_both_providers_fail_rate_limit(self):
        """Test BothProvidersFailedError when both hit rate limits."""
        primary_llm = MagicMock()
        primary_llm.invoke.side_effect = Exception("429 Rate Limit")
        fallback_llm = MagicMock()
        fallback_llm.invoke.side_effect = Exception("429 OpenRouter Rate Limit")

        resilient = ResilientLLM(primary_llm=primary_llm, fallback_llm=fallback_llm)

        with pytest.raises(BothProvidersFailedError) as exc_info:
            resilient.invoke("test message")

        assert resilient.both_failed == 1
        assert exc_info.value.primary_error is not None
        assert exc_info.value.fallback_error is not None

    def test_fallback_fails_with_different_error(self):
        """Test that non-rate-limit fallback failures are raised."""
        primary_llm = MagicMock()
        primary_llm.invoke.side_effect = Exception("429 Rate Limit")
        fallback_llm = MagicMock()
        fallback_llm.invoke.side_effect = Exception("OpenRouter auth failed")

        resilient = ResilientLLM(primary_llm=primary_llm, fallback_llm=fallback_llm)

        with pytest.raises(Exception, match="OpenRouter auth failed"):
            resilient.invoke("test message")

    def test_stats_tracking(self):
        """Test that statistics are tracked correctly."""
        primary_llm = MagicMock()
        fallback_llm = MagicMock()

        resilient = ResilientLLM(primary_llm=primary_llm, fallback_llm=fallback_llm)

        # First call succeeds on primary
        primary_llm.invoke.return_value = "response"
        resilient.invoke("msg1")

        # Second call falls back successfully
        primary_llm.invoke.side_effect = Exception("429")
        fallback_llm.invoke.return_value = "fallback"
        resilient.invoke("msg2")

        stats = resilient.get_stats()
        assert stats["primary_calls"] == 2
        assert stats["primary_successes"] == 1
        assert stats["fallback_calls"] == 1
        assert stats["fallback_successes"] == 1
        assert stats["both_failed"] == 0
        assert stats["fallback_rate"] == 0.5
        assert stats["overall_success_rate"] == 1.0


class TestBothProvidersFailedError:
    """Tests for BothProvidersFailedError exception."""

    def test_preserves_original_errors(self):
        """Test that both original errors are preserved."""
        primary = Exception("Ollama 429")
        fallback = Exception("OpenRouter 429")

        error = BothProvidersFailedError(
            "Both failed",
            primary_error=primary,
            fallback_error=fallback,
        )

        assert error.primary_error is primary
        assert error.fallback_error is fallback
        assert "Both failed" in str(error)


class TestCreateResilientLLM:
    """Tests for create_resilient_llm function."""

    @patch("receipt_agent.utils.llm_factory._create_openrouter_llm")
    @patch("receipt_agent.utils.llm_factory._create_ollama_llm")
    def test_creates_both_providers(self, mock_ollama, mock_openrouter):
        """Test that create_resilient_llm creates both provider LLMs."""
        mock_ollama.return_value = MagicMock()
        mock_openrouter.return_value = MagicMock()

        with patch.dict(os.environ, {
            "OLLAMA_API_KEY": "ollama-key",
            "OPENROUTER_API_KEY": "openrouter-key",
        }):
            resilient = create_resilient_llm(temperature=0.5, timeout=60)

        assert isinstance(resilient, ResilientLLM)
        mock_ollama.assert_called_once()
        mock_openrouter.assert_called_once()

        # Check temperature was passed to both
        ollama_kwargs = mock_ollama.call_args[1]
        openrouter_kwargs = mock_openrouter.call_args[1]
        assert ollama_kwargs["temperature"] == 0.5
        assert openrouter_kwargs["temperature"] == 0.5


class TestCreateProductionInvoker:
    """Tests for create_production_invoker function."""

    @patch("receipt_agent.utils.llm_factory.create_resilient_llm")
    @patch("receipt_agent.utils.llm_factory.OllamaCircuitBreaker")
    @patch("receipt_agent.utils.llm_factory.RateLimitedLLMInvoker")
    def test_creates_full_stack(
        self, mock_invoker_cls, mock_breaker_cls, mock_resilient
    ):
        """Test that create_production_invoker creates the full stack."""
        mock_resilient.return_value = MagicMock()
        mock_breaker_cls.return_value = MagicMock()
        mock_invoker_cls.return_value = MagicMock()

        with patch.dict(os.environ, {
            "OLLAMA_API_KEY": "ollama-key",
            "OPENROUTER_API_KEY": "openrouter-key",
        }):
            invoker = create_production_invoker(
                temperature=0.5,
                timeout=60,
                circuit_breaker_threshold=3,
                max_jitter_seconds=0.5,
            )

        # Check ResilientLLM was created
        mock_resilient.assert_called_once_with(
            temperature=0.5,
            timeout=60,
        )

        # Check circuit breaker was created with threshold
        mock_breaker_cls.assert_called_once_with(threshold=3)

        # Check invoker was created with all components
        mock_invoker_cls.assert_called_once()
        call_kwargs = mock_invoker_cls.call_args[1]
        assert call_kwargs["max_jitter_seconds"] == 0.5


class TestIntegrationWithOllamaRateLimit:
    """Tests for integration with ollama_rate_limit module."""

    def test_both_providers_failed_is_rate_limit(self):
        """Test that BothProvidersFailedError is detected as rate limit."""
        from receipt_agent.utils.ollama_rate_limit import (
            is_rate_limit_error as ollama_is_rate_limit,
        )

        error = BothProvidersFailedError(
            "Both Ollama and OpenRouter are rate limited",
            primary_error=Exception("429"),
            fallback_error=Exception("429"),
        )

        assert ollama_is_rate_limit(error)

    def test_circuit_breaker_triggers_on_both_failed(self):
        """Test that circuit breaker counts BothProvidersFailedError."""
        from receipt_agent.utils.ollama_rate_limit import (
            OllamaCircuitBreaker,
            OllamaRateLimitError,
        )

        breaker = OllamaCircuitBreaker(threshold=2)

        error = BothProvidersFailedError(
            "Both failed",
            primary_error=Exception("429"),
            fallback_error=Exception("429"),
        )

        # First failure
        with pytest.raises(OllamaRateLimitError):
            breaker.record_error(error)

        assert breaker.consecutive_errors == 1
        assert not breaker.triggered

        # Second failure - should trigger
        with pytest.raises(OllamaRateLimitError):
            breaker.record_error(error)

        assert breaker.consecutive_errors == 2
        assert breaker.triggered
