"""Tests for LLM validator parsing logic."""

import pytest
from receipt_upload.label_validation.llm_validator import (
    parse_validation_response,
    LLMValidationResult,
)


class TestParseValidationResponse:
    """Test the parse_validation_response function."""

    def test_parse_valid_response(self):
        """Test parsing a valid LLM response."""
        pending_labels = [
            {"line_id": 1, "word_id": 1, "label": "TOTAL"},
            {"line_id": 1, "word_id": 2, "label": "SUBTOTAL"},
        ]

        response_text = """
        [
            {"index": 0, "decision": "VALID", "label": "TOTAL", "confidence": "high", "reasoning": "Correct"},
            {"index": 1, "decision": "CORRECT", "label": "TAX", "confidence": "medium", "reasoning": "Should be TAX"}
        ]
        """

        results = parse_validation_response(response_text, pending_labels)

        assert len(results) == 2
        assert results[0].word_id == "1_1"
        assert results[0].decision == "VALID"
        assert results[0].label == "TOTAL"

        # CORRECT should be normalized to CORRECTED
        assert results[1].word_id == "1_2"
        assert results[1].decision == "CORRECTED"
        assert results[1].label == "TAX"

    def test_parse_no_json_returns_needs_review(self):
        """Test that missing JSON marks all as NEEDS_REVIEW, not VALID."""
        pending_labels = [
            {"line_id": 1, "word_id": 1, "label": "TOTAL"},
        ]

        response_text = "I cannot parse this receipt."

        results = parse_validation_response(response_text, pending_labels)

        assert len(results) == 1
        assert results[0].decision == "NEEDS_REVIEW"
        assert "parsing failed" in results[0].reasoning.lower()

    def test_parse_invalid_json_returns_needs_review(self):
        """Test that invalid JSON marks all as NEEDS_REVIEW."""
        pending_labels = [
            {"line_id": 1, "word_id": 1, "label": "TOTAL"},
        ]

        response_text = "[{invalid json"

        results = parse_validation_response(response_text, pending_labels)

        assert len(results) == 1
        assert results[0].decision == "NEEDS_REVIEW"

    def test_parse_missing_index_returns_needs_review(self):
        """Test that missing indexes are marked as NEEDS_REVIEW, not VALID."""
        pending_labels = [
            {"line_id": 1, "word_id": 1, "label": "TOTAL"},
            {"line_id": 1, "word_id": 2, "label": "SUBTOTAL"},
        ]

        # Only return result for index 0, skip index 1
        response_text = """
        [
            {"index": 0, "decision": "VALID", "label": "TOTAL", "confidence": "high", "reasoning": "Correct"}
        ]
        """

        results = parse_validation_response(response_text, pending_labels)

        assert len(results) == 2
        assert results[0].decision == "VALID"
        # Index 1 is missing - should be NEEDS_REVIEW, not VALID
        assert results[1].decision == "NEEDS_REVIEW"
        assert "did not return" in results[1].reasoning.lower()

    def test_parse_out_of_range_index_dropped(self):
        """Test that out-of-range indexes are dropped."""
        pending_labels = [
            {"line_id": 1, "word_id": 1, "label": "TOTAL"},
        ]

        response_text = """
        [
            {"index": 0, "decision": "VALID", "label": "TOTAL", "confidence": "high", "reasoning": "Correct"},
            {"index": 99, "decision": "VALID", "label": "EXTRA", "confidence": "high", "reasoning": "OOR"}
        ]
        """

        results = parse_validation_response(response_text, pending_labels)

        assert len(results) == 1
        assert results[0].decision == "VALID"

    def test_parse_duplicate_index_keeps_first(self):
        """Test that duplicate indexes keep only the first."""
        pending_labels = [
            {"line_id": 1, "word_id": 1, "label": "TOTAL"},
            {"line_id": 1, "word_id": 2, "label": "SUBTOTAL"},
        ]

        response_text = """
        [
            {"index": 0, "decision": "VALID", "label": "TOTAL", "confidence": "high", "reasoning": "First"},
            {"index": 0, "decision": "CORRECTED", "label": "TAX", "confidence": "high", "reasoning": "Duplicate"},
            {"index": 1, "decision": "VALID", "label": "SUBTOTAL", "confidence": "high", "reasoning": "OK"}
        ]
        """

        results = parse_validation_response(response_text, pending_labels)

        assert len(results) == 2
        # First occurrence of index 0 should be kept
        assert results[0].decision == "VALID"
        assert results[0].label == "TOTAL"
        assert results[0].reasoning == "First"

    def test_parse_string_index_coerced_to_int(self):
        """Test that string indexes are coerced to int."""
        pending_labels = [
            {"line_id": 1, "word_id": 1, "label": "TOTAL"},
        ]

        response_text = """
        [
            {"index": "0", "decision": "VALID", "label": "TOTAL", "confidence": "high", "reasoning": "Correct"}
        ]
        """

        results = parse_validation_response(response_text, pending_labels)

        assert len(results) == 1
        assert results[0].decision == "VALID"

    def test_parse_invalid_index_dropped(self):
        """Test that non-numeric indexes are dropped."""
        pending_labels = [
            {"line_id": 1, "word_id": 1, "label": "TOTAL"},
        ]

        response_text = """
        [
            {"index": "abc", "decision": "VALID", "label": "TOTAL", "confidence": "high", "reasoning": "Bad"},
            {"index": null, "decision": "VALID", "label": "TOTAL", "confidence": "high", "reasoning": "Null"}
        ]
        """

        results = parse_validation_response(response_text, pending_labels)

        assert len(results) == 1
        # Both items have invalid indexes, so the pending label gets NEEDS_REVIEW
        assert results[0].decision == "NEEDS_REVIEW"

    def test_parse_unknown_decision_becomes_needs_review(self):
        """Test that unknown decisions become NEEDS_REVIEW."""
        pending_labels = [
            {"line_id": 1, "word_id": 1, "label": "TOTAL"},
        ]

        response_text = """
        [
            {"index": 0, "decision": "UNKNOWN_DECISION", "label": "TOTAL", "confidence": "high", "reasoning": "?"}
        ]
        """

        results = parse_validation_response(response_text, pending_labels)

        assert len(results) == 1
        assert results[0].decision == "NEEDS_REVIEW"

    def test_parse_invalid_label_rejected_and_corrected_becomes_needs_review(self):
        """Test that invalid corrected labels become NEEDS_REVIEW."""
        pending_labels = [
            {"line_id": 1, "word_id": 1, "label": "TOTAL"},
        ]

        response_text = """
        [
            {"index": 0, "decision": "CORRECTED", "label": "INVALID_LABEL_XYZ", "confidence": "high", "reasoning": "Bad"}
        ]
        """

        results = parse_validation_response(response_text, pending_labels)

        assert len(results) == 1
        # Invalid label with CORRECTED decision should become NEEDS_REVIEW
        assert results[0].decision == "NEEDS_REVIEW"
        # Should keep original label
        assert results[0].label == "TOTAL"
        assert "invalid" in results[0].reasoning.lower()
