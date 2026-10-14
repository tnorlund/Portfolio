"""Offline HTTP contracts for the explicitly selected direct provider."""

import json
from typing import Any
from unittest.mock import Mock

import httpx2
import pytest
from anthropic import Anthropic

from receipt_upload.label_validation import llm_validator
from receipt_upload.label_validation.anthropic_client import (
    AnthropicValidationError,
)

PENDING = [{"line_id": 1, "word_id": 1, "label": "GRAND_TOTAL"}]
DECISION = {
    "index": 0,
    "decision": "VALID",
    "label": "GRAND_TOTAL",
    "confidence": "high",
    "reasoning": "Total matches.",
}


@pytest.fixture(autouse=True)
def direct_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RECEIPT_VALIDATION_PROVIDER", "anthropic")
    monkeypatch.setenv("RECEIPT_ANTHROPIC_MODEL", "claude-sonnet-5-5")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "unit-test-only")
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    monkeypatch.delenv("ANTHROPIC_CUSTOM_HEADERS", raising=False)
    monkeypatch.delenv("RECEIPT_TRACE_BUCKET", raising=False)
    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "false")
    monkeypatch.setattr(
        llm_validator,
        "create_resilient_llm",
        Mock(side_effect=AssertionError("No OpenRouter fallback allowed")),
    )


def wire_response(
    decisions: list[dict[str, Any]] | None = None,
    stop_reason: str = "end_turn",
) -> dict[str, Any]:
    return {
        "id": "msg_unit_test",
        "type": "message",
        "role": "assistant",
        "model": "claude-sonnet-5-5",
        "content": [
            {
                "type": "text",
                "text": json.dumps(
                    {
                        "decisions": (
                            [DECISION] if decisions is None else decisions
                        )
                    }
                ),
            }
        ],
        "stop_reason": stop_reason,
        "stop_sequence": None,
        "usage": {"input_tokens": 100, "output_tokens": 50},
    }


def transport_validator(
    monkeypatch: pytest.MonkeyPatch,
    replies: list[tuple[int, dict[str, Any]]],
) -> tuple[llm_validator.LLMBatchValidator, list[httpx2.Request]]:
    requests: list[httpx2.Request] = []

    def respond(request: httpx2.Request) -> httpx2.Response:
        requests.append(request)
        code, payload = replies[min(len(requests) - 1, len(replies) - 1)]
        return httpx2.Response(
            code,
            json=payload,
            headers={"request-id": "req_unit_test", "retry-after-ms": "1"},
        )

    def client(**kwargs: Any) -> Anthropic:
        return Anthropic(
            **kwargs,
            http_client=httpx2.Client(transport=httpx2.MockTransport(respond)),
        )

    monkeypatch.setattr(
        "receipt_upload.label_validation.anthropic_client.Anthropic", client
    )
    return llm_validator.LLMBatchValidator(), requests


def test_direct_json_schema_request_and_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://untrusted.invalid")
    validator, requests = transport_validator(
        monkeypatch, [(200, wire_response())]
    )
    result = validator.validate_receipt_labels(PENDING, [], {})[0]
    assert result.decision == "VALID"
    assert '"provider": "anthropic"' in result.reasoning
    assert '"request_id": "req_unit_test"' in result.reasoning
    request = requests[0]
    assert str(request.url) == "https://api.anthropic.com/v1/messages"
    assert request.headers["x-api-key"] == "unit-test-only"
    assert "authorization" not in request.headers
    body = json.loads(request.content)
    assert body["max_tokens"] == 8192
    assert body["output_config"]["format"]["type"] == "json_schema"
    assert (
        body["output_config"]["format"]["schema"]["additionalProperties"]
        is False
    )
    assert "temperature" not in body  # Newer Claude models reject this.
    stats = validator.get_stats()["llm_stats"]
    assert stats["usage"]["input_tokens"] == 100
    assert stats["latency_ms"] >= 0
    fingerprint = stats["request_fingerprint"]
    validator.validate_receipt_labels(PENDING, [], {})
    assert (
        validator.get_stats()["llm_stats"]["request_fingerprint"]
        == fingerprint
    )


@pytest.mark.parametrize("reason", ["max_tokens", "refusal", "tool_use"])
def test_incomplete_response_never_becomes_review_or_valid(
    monkeypatch: pytest.MonkeyPatch, reason: str
) -> None:
    validator, requests = transport_validator(
        monkeypatch, [(200, wire_response(stop_reason=reason))]
    )
    with pytest.raises(AnthropicValidationError, match="incomplete"):
        validator.validate_receipt_labels(PENDING, [], {})
    assert len(requests) == 1


@pytest.mark.parametrize(
    "decisions",
    [
        [],
        [DECISION, DECISION],
        [{**DECISION, "index": 9}],
        [{**DECISION, "index": "0"}],
        [{**DECISION, "label": "INVENTED"}],
    ],
)
def test_invalid_contract_is_rejected_before_any_decisions(
    monkeypatch: pytest.MonkeyPatch, decisions: list[dict[str, Any]]
) -> None:
    validator, _ = transport_validator(
        monkeypatch, [(200, wire_response(decisions))]
    )
    with pytest.raises(AnthropicValidationError):
        validator.validate_receipt_labels(PENDING, [], {})


@pytest.mark.parametrize("code", [429, 500, 529])
def test_transient_errors_retry_same_provider_then_succeed(
    monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    validator, requests = transport_validator(
        monkeypatch,
        [
            (code, {"error": {"type": "api_error", "message": "retry"}}),
            (200, wire_response()),
        ],
    )
    assert (
        validator.validate_receipt_labels(PENDING, [], {})[0].decision
        == "VALID"
    )
    assert len(requests) == 2
    assert requests[0].content == requests[1].content


@pytest.mark.parametrize(
    "code,attempts", [(401, 1), (403, 1), (429, 3), (500, 3)]
)
def test_failure_is_bounded_sanitized_and_never_falls_back(
    monkeypatch: pytest.MonkeyPatch, code: int, attempts: int
) -> None:
    validator, requests = transport_validator(
        monkeypatch,
        [(code, {"error": {"type": "api_error", "message": "private-body"}})],
    )
    with pytest.raises(AnthropicValidationError) as caught:
        validator.validate_receipt_labels(PENDING, [], {})
    assert "private-body" not in str(caught.value)
    assert len(requests) == attempts


@pytest.mark.parametrize(
    "missing", ["ANTHROPIC_API_KEY", "RECEIPT_ANTHROPIC_MODEL"]
)
def test_missing_config_fails_before_provider_creation(
    monkeypatch: pytest.MonkeyPatch, missing: str
) -> None:
    monkeypatch.delenv(missing)
    with pytest.raises(AnthropicValidationError):
        llm_validator.LLMBatchValidator()


def test_subscription_token_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "subscription-test-only")
    with pytest.raises(AnthropicValidationError, match="bearer"):
        llm_validator.LLMBatchValidator()


def test_custom_headers_cannot_override_authentication(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(
        "ANTHROPIC_CUSTOM_HEADERS",
        "X-Api-Key: different-key\nAuthorization: Bearer subscription-token",
    )
    with pytest.raises(AnthropicValidationError, match="Custom"):
        llm_validator.LLMBatchValidator()


@pytest.mark.parametrize("extra_at_root", [True, False])
def test_extra_response_fields_fail_the_strict_contract(
    monkeypatch: pytest.MonkeyPatch, extra_at_root: bool
) -> None:
    response = wire_response()
    payload = json.loads(response["content"][0]["text"])
    target = payload if extra_at_root else payload["decisions"][0]
    target["unexpected"] = "must not be ignored"
    response["content"][0]["text"] = json.dumps(payload)
    validator, _ = transport_validator(monkeypatch, [(200, response)])
    with pytest.raises(AnthropicValidationError, match="schema"):
        validator.validate_receipt_labels(PENDING, [], {})


def test_uncertain_correction_stays_unresolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    validator, _ = transport_validator(
        monkeypatch,
        [
            (
                200,
                wire_response(
                    [{**DECISION, "decision": "NEEDS_REVIEW", "label": "TAX"}]
                ),
            )
        ],
    )
    result = validator.validate_receipt_labels(PENDING, [], {})[0]
    assert result.decision == "NEEDS_REVIEW"
    assert result.label == "GRAND_TOTAL"


def test_unknown_provider_never_defaults_to_openrouter(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("RECEIPT_VALIDATION_PROVIDER", "typo")
    with pytest.raises(AnthropicValidationError, match="Unsupported"):
        llm_validator.LLMBatchValidator()


def test_default_keeps_existing_openrouter_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RECEIPT_VALIDATION_PROVIDER")
    factory = Mock()
    monkeypatch.setattr(llm_validator, "create_resilient_llm", factory)
    validator = llm_validator.LLMBatchValidator()
    assert validator.provider == "openrouter"
    factory.assert_called_once_with(temperature=0.0, timeout=120)
