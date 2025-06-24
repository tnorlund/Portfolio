"""Tests for merchant validation result_processor module."""

import os
from datetime import datetime
from importlib import util
from unittest.mock import Mock, patch

import pytest
from receipt_dynamo.constants import ValidationMethod

# Environment must be set before importing the module under test
os.environ.setdefault("DYNAMO_TABLE_NAME", "table")
os.environ.setdefault("PINECONE_API_KEY", "key")
os.environ.setdefault("OPENAI_API_KEY", "key")
os.environ.setdefault("PINECONE_INDEX_NAME", "index")
os.environ.setdefault("PINECONE_HOST", "host")
spec = util.spec_from_file_location(
    "rp", "receipt_label/receipt_label/merchant_validation/result_processor.py"
)
assert spec and spec.loader
rp = util.module_from_spec(spec)
spec.loader.exec_module(rp)  # type: ignore[attr-defined]
# pylint: disable=protected-access


@pytest.mark.unit
def test_validate_match_quality_all_high():
    """High-quality name, phone, and address should all be kept."""
    data = {
        "merchant_name": "Store",
        "phone_number": "555-123-4567",
        "address": "123 Main St, City",
        "matched_fields": ["name", "phone", "address"],
    }
    result = rp._validate_match_quality(data)
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
    assert rp._validate_match_quality(data) == []


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
    match = rp.extract_best_partial_match(partial_results, {})
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
    match = rp.extract_best_partial_match(partial_results, {})
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
    assert rp.extract_best_partial_match(partial_results, {}) is None


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
    with patch.object(
        rp, "ReceiptMetadata", return_value=mock_metadata
    ) as meta:
        result = rp.build_receipt_metadata_from_partial_result(
            "img",
            1,
            partial,
            ["raw"],
        )
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
    assert rp.sanitize_string('"Hello"') == "Hello"
    assert rp.sanitize_string("'\"Hello\"'") == "Hello"
    assert rp.sanitize_string(123) == "123"
    assert rp.sanitize_string("  world  ") == "world"
