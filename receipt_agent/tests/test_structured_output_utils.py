"""Unit tests for strict structured output utilities."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from receipt_agent.utils import (
    LLMRateLimitError,
    ainvoke_structured_with_retry,
    get_structured_output_settings,
    invoke_structured_with_retry,
)


class DummySchema:
    """Simple placeholder schema type for structured output tests."""


class TestStructuredOutputSettings:
    """Tests for environment-driven strict structured output settings."""

    def test_defaults_when_env_missing(self):
        """Defaults are strict=true and retries=3 when env vars are absent."""
        with patch.dict("os.environ", {}, clear=True):
            strict_enabled, retries = get_structured_output_settings()

        assert strict_enabled is True
        assert retries == 3

    def test_invalid_env_values_fall_back(self):
        """Invalid env values should fall back to safe defaults."""
        with patch.dict(
            "os.environ",
            {
                "LLM_STRICT_STRUCTURED_OUTPUT": "definitely",
                "LLM_STRUCTURED_OUTPUT_RETRIES": "abc",
            },
            clear=True,
        ):
            strict_enabled, retries = get_structured_output_settings()

        assert strict_enabled is True
        assert retries == 3

    def test_normalizes_retry_floor(self):
        """Retry count should never drop below 1."""
        with patch.dict(
            "os.environ",
            {
                "LLM_STRICT_STRUCTURED_OUTPUT": "false",
                "LLM_STRUCTURED_OUTPUT_RETRIES": "0",
            },
            clear=True,
        ):
            strict_enabled, retries = get_structured_output_settings()

        assert strict_enabled is False
        assert retries == 1


class TestInvokeStructuredWithRetry:
    """Tests for sync structured output invocation helper."""

    def test_success(self):
        """Returns structured response when invocation succeeds."""
        llm = MagicMock()
        structured_llm = MagicMock()
        structured_response = MagicMock()
        structured_llm.invoke.return_value = structured_response
        llm.with_structured_output.return_value = structured_llm

        result = invoke_structured_with_retry(
            llm=llm,
            schema=DummySchema,
            input_payload="hello",
            retries=2,
        )

        assert result.success is True
        assert result.response == structured_response
        assert result.attempts == 1
        llm.with_structured_output.assert_called_once_with(DummySchema)

    def test_failure_returns_metadata(self):
        """Returns structured failure metadata after retries are exhausted."""
        llm = MagicMock()
        llm.with_structured_output.side_effect = RuntimeError("boom")

        result = invoke_structured_with_retry(
            llm=llm,
            schema=DummySchema,
            input_payload="hello",
            retries=2,
        )

        assert result.success is False
        assert result.response is None
        assert result.attempts == 2
        assert result.error_type == "RuntimeError"
        assert "boom" in (result.error_message or "")

    def test_rate_limit_propagates(self):
        """Rate limit errors are propagated for Step Functions retry handling."""
        llm = MagicMock()
        structured_llm = MagicMock()
        structured_llm.invoke.side_effect = LLMRateLimitError("429")
        llm.with_structured_output.return_value = structured_llm

        with pytest.raises(LLMRateLimitError):
            _ = invoke_structured_with_retry(
                llm=llm,
                schema=DummySchema,
                input_payload="hello",
                retries=3,
            )

    def test_missing_structured_support(self):
        """Returns deterministic failure when with_structured_output is missing."""
        llm = object()
        result = invoke_structured_with_retry(
            llm=llm,
            schema=DummySchema,
            input_payload="hello",
            retries=2,
        )

        assert result.success is False
        assert result.response is None
        assert result.attempts == 0
        assert result.error_type == "missing_with_structured_output"


class TestAinvokeStructuredWithRetry:
    """Tests for async structured output invocation helper."""

    @pytest.mark.asyncio
    async def test_success(self):
        """Returns structured response when async invocation succeeds."""
        llm = MagicMock()
        structured_llm = MagicMock()
        structured_response = MagicMock()
        structured_llm.ainvoke = AsyncMock(return_value=structured_response)
        llm.with_structured_output.return_value = structured_llm

        result = await ainvoke_structured_with_retry(
            llm=llm,
            schema=DummySchema,
            input_payload="hello",
            retries=2,
        )

        assert result.success is True
        assert result.response == structured_response
        assert result.attempts == 1

    @pytest.mark.asyncio
    async def test_missing_structured_support(self):
        """Returns deterministic failure when with_structured_output is missing."""
        llm = object()
        result = await ainvoke_structured_with_retry(
            llm=llm,
            schema=DummySchema,
            input_payload="hello",
            retries=2,
        )

        assert result.success is False
        assert result.response is None
        assert result.attempts == 0
        assert result.error_type == "missing_with_structured_output"

    @pytest.mark.asyncio
    async def test_rate_limit_propagates(self):
        """Rate limit errors are propagated for Step Functions retry handling."""
        llm = MagicMock()
        structured_llm = MagicMock()
        structured_llm.ainvoke = AsyncMock(side_effect=LLMRateLimitError("429"))
        llm.with_structured_output.return_value = structured_llm

        with pytest.raises(LLMRateLimitError):
            _ = await ainvoke_structured_with_retry(
                llm=llm,
                schema=DummySchema,
                input_payload="hello",
                retries=3,
            )
