"""Focused tests for failures at the shared agent invocation boundary."""

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from receipt_agent.exceptions import LLMInvocationError, LLMRateLimitError
from receipt_agent.utils.agent_common import create_agent_node_with_retry


def test_agent_node_raises_specific_rate_limit_error_with_cause() -> None:
    llm = MagicMock()
    provider_error = RuntimeError("HTTP 429: too many requests")
    llm.invoke.side_effect = provider_error
    node = create_agent_node_with_retry(llm, agent_name="place_finder")

    with pytest.raises(LLMRateLimitError) as exc_info:
        node(SimpleNamespace(messages=["find it"]))

    assert str(exc_info.value) == (
        "Rate limit error in place_finder: HTTP 429: too many requests"
    )
    assert exc_info.value.__cause__ is provider_error
    llm.invoke.assert_called_once_with(["find it"])


def test_agent_node_wraps_non_retryable_provider_error_with_context() -> None:
    llm = MagicMock()
    provider_error = RuntimeError("invalid model response")
    llm.invoke.side_effect = provider_error
    node = create_agent_node_with_retry(llm, agent_name="currency")

    with pytest.raises(LLMInvocationError) as exc_info:
        node(SimpleNamespace(messages=[]))

    assert str(exc_info.value) == (
        "Failed to get LLM response in currency: invalid model response"
    )
    assert exc_info.value.__cause__ is provider_error


def test_agent_node_wraps_retry_exhaustion_and_preserves_last_cause() -> None:
    llm = MagicMock()
    first_error = RuntimeError("503 first failure")
    final_error = RuntimeError("503 final failure")
    llm.invoke.side_effect = [first_error, final_error]
    node = create_agent_node_with_retry(
        llm,
        agent_name="financial",
        max_retries=2,
        base_delay=0,
        max_wait=0,
    )

    with pytest.raises(LLMInvocationError) as exc_info:
        node(SimpleNamespace(messages=[]))

    assert str(exc_info.value) == (
        "Failed to get LLM response in financial: 503 final failure"
    )
    assert exc_info.value.__cause__ is final_error
    assert llm.invoke.call_count == 2
