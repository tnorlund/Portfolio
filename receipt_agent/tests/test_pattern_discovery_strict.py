"""Tests for strict pattern discovery response parsing."""

from receipt_agent.agents.label_evaluator.pattern_discovery import (
    _build_pattern_response_format,
    _parse_llm_response,
    discover_patterns_with_llm,
    PatternDiscoveryConfig,
)


def test_parse_llm_response_strict_rejects_invalid_schema():
    """Strict parsing should reject JSON that fails the schema."""
    invalid_content = '{"merchant": "Test Merchant"}'
    parsed = _parse_llm_response(
        invalid_content,
        strict_structured_output=True,
    )
    assert parsed is None


def test_parse_llm_response_non_strict_returns_raw_json():
    """Non-strict parsing should preserve backwards-compatible raw JSON."""
    invalid_content = '{"merchant": "Test Merchant"}'
    parsed = _parse_llm_response(
        invalid_content,
        strict_structured_output=False,
    )
    assert parsed == {"merchant": "Test Merchant"}


def test_parse_llm_response_strict_accepts_valid_schema():
    """Strict parsing should accept schema-valid pattern responses."""
    valid_content = """
    {
      "merchant": "Test Merchant",
      "receipt_type": "itemized",
      "receipt_type_reason": "Contains multiple product line items",
      "special_markers": [],
      "confusion_patterns": [
        {
          "actual_label": "O",
          "predicted_label": "MERCHANT_NAME",
          "pattern": "Header-adjacent store numbers look like merchant names",
          "heatmap_rationale": "Errors cluster in the top-center header band",
          "synthetic_receipt_strategy": "Add hard-negative header variants"
        }
      ],
      "synthetic_receipt_guidance": [
        "Add top-center merchant header lookalikes that remain unlabeled"
      ]
    }
    """
    parsed = _parse_llm_response(
        valid_content,
        strict_structured_output=True,
    )
    assert parsed is not None
    assert parsed["receipt_type"] == "itemized"
    assert (
        parsed["confusion_patterns"][0]["predicted_label"] == "MERCHANT_NAME"
    )
    assert parsed["synthetic_receipt_guidance"]


def test_pattern_response_schema_forbids_additional_properties():
    """Pattern discovery strict schema should reject undeclared object fields."""
    schema = _build_pattern_response_format()["json_schema"]["schema"]

    def _all_object_nodes(node):
        if isinstance(node, dict):
            if node.get("type") == "object":
                yield node
            for value in node.values():
                yield from _all_object_nodes(value)
        elif isinstance(node, list):
            for value in node:
                yield from _all_object_nodes(value)

    object_nodes = list(_all_object_nodes(schema))
    assert object_nodes
    assert all(
        node.get("additionalProperties") is False for node in object_nodes
    )


def test_discover_patterns_respects_paid_llm_disable(monkeypatch):
    """No-spend mode should skip the OpenRouter HTTP path."""
    calls = []

    class FakeClient:
        def __init__(self, *args, **kwargs):
            calls.append(("init", args, kwargs))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def post(self, *args, **kwargs):
            calls.append(("post", args, kwargs))
            raise AssertionError("HTTP call should not happen")

    monkeypatch.setattr(
        "receipt_agent.agents.label_evaluator.pattern_discovery.httpx.Client",
        FakeClient,
    )

    result = discover_patterns_with_llm(
        "Analyze this receipt",
        config=PatternDiscoveryConfig(
            openrouter_api_key="test-key",
            disable_paid_llm=True,
        ),
    )

    assert result is None
    assert calls == []


def test_pattern_discovery_config_reads_paid_llm_disable(monkeypatch):
    """Pattern discovery should inherit the process-wide no-spend switch."""
    monkeypatch.setenv("RECEIPT_AGENT_DISABLE_PAID_LLM", "true")
    monkeypatch.setenv("OPENROUTER_API_KEY", "test-key")

    config = PatternDiscoveryConfig.from_env()

    assert config.openrouter_api_key == "test-key"
    assert config.disable_paid_llm is True


def test_pattern_discovery_config_uses_model_profile(monkeypatch):
    """Pattern discovery should share the central model profile resolver."""
    monkeypatch.delenv("OPENROUTER_MODEL", raising=False)
    monkeypatch.delenv("RECEIPT_AGENT_OPENROUTER_MODEL", raising=False)
    monkeypatch.setenv("OPENROUTER_MODEL_PROFILE", "cheap")

    config = PatternDiscoveryConfig.from_env()

    assert config.openrouter_model == "openai/gpt-5.4-mini"


def test_pattern_discovery_config_explicit_model_overrides_profile(
    monkeypatch,
):
    """Explicit model env vars should still win over cost profiles."""
    monkeypatch.setenv("OPENROUTER_MODEL", "provider/specific-model")
    monkeypatch.setenv("OPENROUTER_MODEL_PROFILE", "cheap")

    config = PatternDiscoveryConfig.from_env()

    assert config.openrouter_model == "provider/specific-model"
