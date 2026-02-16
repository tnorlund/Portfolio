"""Tests for strict pattern discovery response parsing."""

from receipt_agent.agents.label_evaluator.pattern_discovery import (
    _build_pattern_response_format,
    _parse_llm_response,
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
      "special_markers": []
    }
    """
    parsed = _parse_llm_response(
        valid_content,
        strict_structured_output=True,
    )
    assert parsed is not None
    assert parsed["receipt_type"] == "itemized"


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
    assert all(node.get("additionalProperties") is False for node in object_nodes)
