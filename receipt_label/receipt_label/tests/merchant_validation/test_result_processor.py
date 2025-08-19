"""Tests for merchant validation result_processor module."""

# Set up test environment before imports
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from test_helpers import setup_test_environment

setup_test_environment()

from datetime import datetime
from unittest.mock import Mock, patch

import pytest
from receipt_dynamo.constants import ValidationMethod

from receipt_label.merchant_validation.result_processor import (
    _validate_match_quality,
    build_receipt_metadata_from_partial_result,
    extract_best_partial_match,
    sanitize_metadata_strings,
    sanitize_string)


@pytest.mark.unit
def test_validate_match_quality_all_high():
    """High-quality name, phone, and address should all be kept."""
    data = {
        "merchant_name": "Store",
        "phone_number": "555-123-4567",
        "address": "123 Main St, City",
        "matched_fields": ["name", "phone", "address"],
    }
    result = _validate_match_quality(data)
    assert set(result) == {"name", "phone", "address"}


@pytest.mark.unit
def test_validate_match_quality_low_quality():
    """Low-quality fields should be discarded."""
    data = {
        "merchant_name": "",
        "phone_number": "123",
        "address": "Unit",
        "matched_fields": ["name", "phone", "address"],
    }
    assert _validate_match_quality(data) == []


@pytest.mark.unit
def test_validate_match_quality_mixed_high_low():
    """Mixed quality: keep high-quality fields, discard low-quality ones."""
    data = {
        "merchant_name": "Starbucks Coffee Company",  # High quality
        "phone_number": "123",  # Low quality (< 10 digits)
        "address": "123 Main Street, San Francisco, CA",  # High quality
        "matched_fields": ["name", "phone", "address"],
    }
    result = _validate_match_quality(data)
    assert set(result) == {"name", "address"}
    assert "phone" not in result


@pytest.mark.unit
def test_validate_match_quality_partial_address():
    """Address with only one meaningful component should be discarded."""
    data = {
        "merchant_name": "Target Store",
        "phone_number": "(415) 555-0123",
        "address": "Unit",  # Only one meaningful component
        "matched_fields": ["name", "phone", "address"],
    }
    result = _validate_match_quality(data)
    assert set(result) == {"name", "phone"}
    assert "address" not in result


@pytest.mark.unit
def test_validate_match_quality_short_name():
    """Very short names should be discarded."""
    data = {
        "merchant_name": "AB",  # Too short (≤ 2 chars)
        "phone_number": "415-555-0123",
        "address": "123 Main St",
        "matched_fields": ["name", "phone", "address"],
    }
    result = _validate_match_quality(data)
    assert set(result) == {"phone", "address"}
    assert "name" not in result


@pytest.mark.unit
def test_extract_best_partial_match_phone_preferred():
    """Phone lookup results take precedence when valid."""
    partial_results = [
        {
            "function": "search_by_phone",
            "result": {
                "place_id": "pid1",
                "name": "Shop",
                "formatted_address": "123 Main St",
                "formatted_phone_number": "(555) 111-2222",
            },
        },
        {
            "function": "search_by_address",
            "result": {
                "place_id": "pid2",
                "name": "Other",
                "formatted_address": "Addr2",
                "formatted_phone_number": "(555) 000-0000",
            },
        },
    ]
    match = extract_best_partial_match(partial_results, {})
    assert match["source"] == "phone_lookup"
    assert match["place_id"] == "pid1"
    assert match["matched_fields"] == ["phone"]


@pytest.mark.unit
def test_extract_best_partial_match_address_fallback():
    """Address lookup is used when phone result fails quality checks."""
    partial_results = [
        {
            "function": "search_by_phone",
            "result": {
                "place_id": "pid1",
                "name": "Shop",
                "formatted_address": "123",
                "formatted_phone_number": "123",
            },
        },
        {
            "function": "search_by_address",
            "result": {
                "place_id": "pid2",
                "name": "Other",
                "formatted_address": "456 Elm St",
                "formatted_phone_number": "(555) 000-0000",
            },
        },
    ]
    match = extract_best_partial_match(partial_results, {})
    assert match["source"] == "address_lookup"
    assert match["place_id"] == "pid2"
    assert match["matched_fields"] == ["address"]


@pytest.mark.unit
def test_extract_best_partial_match_none_returned():
    """Return None when no partial result passes validation."""
    partial_results = [
        {
            "function": "search_by_phone",
            "result": {"place_id": "", "name": ""},
        },
        {
            "function": "search_by_address",
            "result": {"place_id": "", "name": ""},
        },
    ]
    assert extract_best_partial_match(partial_results, {}) is None


@pytest.mark.unit
def test_extract_best_partial_match_text_search_fallback():
    """Text search is used as last resort when phone/address fail."""
    partial_results = [
        {
            "function": "search_by_phone",
            "result": {"place_id": "", "name": ""},  # Empty result
        },
        {
            "function": "search_by_address",
            "result": {"place_id": "pid1", "name": "A"},  # Name too short
        },
        {
            "function": "search_by_text",
            "result": {
                "place_id": "pid3",
                "name": "Whole Foods Market",
                "formatted_address": "123 Market St",
            },
        },
    ]
    match = extract_best_partial_match(partial_results, {})
    assert match["source"] == "text_search"
    assert match["place_id"] == "pid3"
    assert match["matched_fields"] == ["name"]


@pytest.mark.unit
def test_extract_best_partial_match_text_search_short_name():
    """Text search with short name is rejected."""
    partial_results = [
        {
            "function": "search_by_text",
            "result": {
                "place_id": "pid1",
                "name": "AB",  # Too short
                "formatted_address": "123 Main St",
            },
        },
    ]
    assert extract_best_partial_match(partial_results, {}) is None


@pytest.mark.unit
def test_extract_best_partial_match_priority_order():
    """Verify priority: phone > address > text search."""
    partial_results = [
        {
            "function": "search_by_text",
            "result": {
                "place_id": "pid_text",
                "name": "Text Search Result",
                "formatted_address": "789 Text St",
            },
        },
        {
            "function": "search_by_address",
            "result": {
                "place_id": "pid_addr",
                "name": "Address Result",
                "formatted_address": "456 Address Ave",
                "formatted_phone_number": "(555) 222-3333",
            },
        },
        {
            "function": "search_by_phone",
            "result": {
                "place_id": "pid_phone",
                "name": "Phone Result",
                "formatted_address": "123 Phone Ln",
                "formatted_phone_number": "(555) 111-2222",
            },
        },
    ]
    # Phone should win even if it appears last
    match = extract_best_partial_match(partial_results, {})
    assert match["source"] == "phone_lookup"
    assert match["place_id"] == "pid_phone"


@pytest.mark.unit
def test_build_receipt_metadata_from_partial_result():
    """Verify ReceiptMetadata is constructed with mapped fields."""
    partial = {
        "place_id": "pid1",
        "merchant_name": "Store",
        "address": "123 Main St",
        "phone_number": "555-123-4567",
        "matched_fields": ["name", "phone"],
        "source": "phone_lookup",
    }
    mock_metadata = Mock()
    with patch(
        "receipt_label.merchant_validation.result_processor.ReceiptMetadata",
        return_value=mock_metadata) as meta:
        result = build_receipt_metadata_from_partial_result(
            "img",
            1,
            partial,
            ["raw"])
        meta.assert_called_once()
        kwargs = meta.call_args.kwargs
        assert kwargs["image_id"] == "img"
        assert kwargs["receipt_id"] == 1
        assert kwargs["place_id"] == partial["place_id"]
        assert kwargs["merchant_name"] == partial["merchant_name"]
        assert kwargs["validated_by"] == ValidationMethod.PHONE_LOOKUP
        assert kwargs["matched_fields"] == partial["matched_fields"]
        assert isinstance(kwargs["timestamp"], datetime)
        assert result is mock_metadata


@pytest.mark.unit
def test_sanitize_string_various_cases():
    """Ensure sanitize_string handles quoting and non-strings."""
    assert sanitize_string('"Hello"') == "Hello"
    assert sanitize_string("'\"Hello\"'") == "Hello"
    assert sanitize_string(123) == "123"
    assert sanitize_string("  world  ") == "world"


@pytest.mark.unit
@pytest.mark.parametrize(
    "input_value,expected",
    [
        # Basic quote removal
        ('"Hello"', "Hello"),
        ("'Hello'", "Hello"),
        ('""Hello""', "Hello"),  # Double quotes
        ("''Hello''", "'Hello'"),  # Inner single quotes have unmatched quotes
        # Mixed quotes
        ("\"'Hello'\"", "Hello"),  # JSON parsing handles this
        ('"\\"Hello\\""', "Hello"),  # JSON parsing handles escaped quotes
        ('"Hello\'s"', "Hello's"),  # Apostrophe preserved
        # Complex nested quotes
        ('"""Hello"""', "Hello"),  # Multiple iterations strip all quotes
        ("'''Hello'''", "''Hello''"),  # Single quotes don't iterate as deeply
        ('""\\"Nested\\"""', '"Nested"'),  # JSON parsing handles this
        # Unicode quotes
        ('"Hello"', "Hello"),  # Left/right double quotes
        ("'Hello'", "Hello"),  # Left/right single quotes
        ('""Hello""', "Hello"),  # Mixed unicode quotes
        # Mismatched quotes
        ("\"Hello'", "Hello"),
        ("'Hello\"", "Hello"),
        ('"Hello', '"Hello'),  # Keep if no closing quote
        # Edge cases
        ("", ""),
        ("   ", ""),
        (None, ""),
        ('"', '"'),  # Single quote char
        (
            '""',
            '""'),  # Empty quotes - sanitize_string preserves original if result would be empty
        ("'\"'", '"'),  # Quote inside quotes
        # Non-string inputs
        (123, "123"),
        (45.67, "45.67"),
        (True, "True"),
        (False, "False"),
        # Whitespace handling
        ("  Hello  ", "Hello"),
        ("\tHello\n", "Hello"),
        ('"  Hello  "', "Hello"),
        # JSON-like strings
        ('"\\"quoted\\""', "quoted"),  # JSON parsing removes escaped quotes
        (
            '"{\\"key\\": \\"value\\"}"',
            '{"key": "value"}'),  # JSON parsing handles escapes
    ])
def test_sanitize_string_comprehensive(input_value, expected):
    """Comprehensive test cases for sanitize_string."""
    assert sanitize_string(input_value) == expected


@pytest.mark.unit
def test_sanitize_string_preserves_original_on_error():
    """Verify that sanitize_string converts non-strings using str()."""

    # For non-string inputs, the function returns str(value) without processing
    class CustomObject:
        def __str__(self):
            return "  custom object  "

    custom_input = CustomObject()
    # The function converts to string but doesn't strip for non-string inputs
    assert sanitize_string(custom_input) == "  custom object  "


@pytest.mark.unit
def test_sanitize_metadata_strings():
    """Test sanitize_metadata_strings function."""
    # Test with quoted strings in various fields
    metadata = {
        "place_id": '"pid123"',
        "merchant_name": "'Store Name'",
        "address": '""123 Main St""',
        "phone_number": '"(555) 123-4567"',
        "merchant_category": '"restaurant"',
        "reasoning": '"Selected based on phone match"',
        "other_field": '"should not be touched"',  # Non-string fields preserved
        "matched_fields": ["phone", "name"],  # Lists preserved
        "timestamp": datetime.now(),  # Other types preserved
    }

    result = sanitize_metadata_strings(metadata)

    # Check string fields are sanitized
    assert result["place_id"] == "pid123"
    assert result["merchant_name"] == "Store Name"
    assert result["address"] == "123 Main St"
    assert result["phone_number"] == "(555) 123-4567"
    assert result["merchant_category"] == "restaurant"
    assert result["reasoning"] == "Selected based on phone match"

    # Check non-string fields are preserved as-is
    assert result["other_field"] == '"should not be touched"'
    assert result["matched_fields"] == ["phone", "name"]
    assert isinstance(result["timestamp"], datetime)

    # Check original dict is not modified
    assert metadata["place_id"] == '"pid123"'


@pytest.mark.unit
def test_sanitize_metadata_strings_empty():
    """Test sanitize_metadata_strings with empty/missing fields."""
    metadata = {
        "place_id": '""',
        "merchant_name": None,
        "address": "",
        # phone_number is missing
    }

    result = sanitize_metadata_strings(metadata)

    assert (
        result["place_id"] == '""'
    )  # Empty quotes preserved as per sanitize_string
    assert result["merchant_name"] == ""  # None converted to empty string
    assert result["address"] == ""
    assert "phone_number" not in result  # Missing fields not added


@pytest.mark.unit
def test_environment_isolation(clean_env_vars):
    """Demonstrate that environment variables are properly isolated in tests."""
    # Store original value
    original_table_name = os.environ.get("DYNAMODB_TABLE_NAME")

    # Test can modify environment
    os.environ["DYNAMODB_TABLE_NAME"] = "modified-table"
    assert os.environ["DYNAMODB_TABLE_NAME"] == "modified-table"

    # After this test, clean_env_vars fixture will restore original environment
    # This prevents test pollution between tests
