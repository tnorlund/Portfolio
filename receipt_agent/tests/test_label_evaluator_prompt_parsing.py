"""Unit tests for label evaluator prompt response parsing."""

import json

import pytest
from receipt_agent.prompts.label_evaluator import (
    parse_batched_llm_response,
    parse_llm_response,
)


@pytest.mark.unit
def test_parse_llm_response_infers_label_from_reasoning():
    """INVALID decisions infer CORE label from assignment-style reasoning."""
    payload = {
        "decision": "INVALID",
        "reasoning": "Current label is wrong; correct label should be unit price.",
        "suggested_label": "OTHER",
        "confidence": "high",
    }

    result = parse_llm_response(json.dumps(payload))

    assert result["decision"] == "INVALID"
    assert result["suggested_label"] == "UNIT_PRICE"
    assert result["confidence"] == "high"


@pytest.mark.unit
def test_parse_llm_response_drops_suggested_label_for_valid():
    """VALID decisions should never carry a suggested_label."""
    payload = {
        "decision": "VALID",
        "reasoning": "The current label is correct.",
        "suggested_label": "PRODUCT_NAME",
        "confidence": "medium",
    }

    result = parse_llm_response(json.dumps(payload))

    assert result["decision"] == "VALID"
    assert result["suggested_label"] is None


@pytest.mark.unit
def test_parse_batched_llm_response_normalizes_invalid_and_valid_cases():
    """Batched parsing applies the same normalization rules per review."""
    payload = {
        "reviews": [
            {
                "issue_index": 0,
                "decision": "INVALID",
                "reasoning": "Correct label should be grand total.",
                "suggested_label": None,
                "confidence": "high",
            },
            {
                "issue_index": 1,
                "decision": "VALID",
                "reasoning": "Already labeled correctly.",
                "suggested_label": "PHONE_NUMBER",
                "confidence": "medium",
            },
        ]
    }

    result = parse_batched_llm_response(json.dumps(payload), expected_count=2)

    assert result[0]["decision"] == "INVALID"
    assert result[0]["suggested_label"] == "GRAND_TOTAL"
    assert result[1]["decision"] == "VALID"
    assert result[1]["suggested_label"] is None
