"""Additional edge case tests for parsers module."""
from datetime import datetime
from typing import Any, Dict

import pytest
from receipt_dynamo.entities.receipt_metadata import ReceiptMetadata
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel

from receipt_dynamo_stream.parsing.parsers import (
    detect_entity_type,
    parse_entity,
    parse_stream_record,
)

from conftest import MockMetrics


# Test detect_entity_type edge cases


def test_detect_entity_type_metadata_variations() -> None:
    """Test various metadata SK formats."""
    test_cases = [
        "RECEIPT#00001#METADATA",
        "RECEIPT#99999#METADATA",
        "ANYTHING#METADATA",
    ]
    for sk in test_cases:
        assert detect_entity_type(sk) == "RECEIPT_METADATA"


def test_detect_entity_type_word_label_variations() -> None:
    """Test various word label SK formats."""
    test_cases = [
        "RECEIPT#00001#LINE#00001#WORD#00001#LABEL#TOTAL",
        "RECEIPT#00001#LINE#99999#WORD#99999#LABEL#MERCHANT",
        "ANYTHING#LABEL#TEST",
    ]
    for sk in test_cases:
        assert detect_entity_type(sk) == "RECEIPT_WORD_LABEL"


def test_detect_entity_type_compaction_run_variations() -> None:
    """Test various compaction run SK formats."""
    test_cases = [
        "RECEIPT#00001#COMPACTION_RUN#run-abc",
        "RECEIPT#99999#COMPACTION_RUN#2024-01-01",
        "ANYTHING#COMPACTION_RUN#test",
    ]
    for sk in test_cases:
        assert detect_entity_type(sk) == "COMPACTION_RUN"


def test_detect_entity_type_unknown_patterns() -> None:
    """Test SK patterns that don't match any entity type."""
    test_cases = [
        "RECEIPT#00001#LINE#00001",
        "RECEIPT#00001#WORD#00001",
        "RANDOM#PATTERN",
        "",
        "RECEIPT",
    ]
    for sk in test_cases:
        assert detect_entity_type(sk) is None


def test_detect_entity_type_empty_string() -> None:
    """Test with empty SK."""
    assert detect_entity_type("") is None


def test_detect_entity_type_case_sensitive() -> None:
    """Test that detection is case-sensitive."""
    # Lowercase should not match
    assert detect_entity_type("receipt#00001#metadata") is None
    assert detect_entity_type("RECEIPT#00001#metadata") is None


# Test parse_entity edge cases


def test_parse_entity_none_image() -> None:
    """Test parsing with None image."""
    result = parse_entity(None, "RECEIPT_METADATA", "new", "PK", "SK")
    assert result is None


def test_parse_entity_metadata_success() -> None:
    """Test successful metadata parsing."""
    pk = "IMAGE#550e8400-e29b-41d4-a716-446655440000"
    sk = "RECEIPT#00001#METADATA"

    metadata = ReceiptMetadata(
        image_id="550e8400-e29b-41d4-a716-446655440000",
        receipt_id=1,
        place_id="place123",
        merchant_name="Test",
        matched_fields=["name"],
        validated_by="PHONE_LOOKUP",
        timestamp=datetime.fromisoformat("2024-01-01T00:00:00"),
    )

    image = metadata.to_item()
    # Remove PK/SK since parse_entity adds them
    image.pop("PK", None)
    image.pop("SK", None)

    result = parse_entity(image, "RECEIPT_METADATA", "new", pk, sk)

    assert result is not None
    assert isinstance(result, ReceiptMetadata)
    assert result.merchant_name == "Test"


def test_parse_entity_word_label_success() -> None:
    """Test successful word label parsing."""
    pk = "IMAGE#550e8400-e29b-41d4-a716-446655440000"
    sk = "RECEIPT#00001#LINE#00001#WORD#00001#LABEL#TOTAL"

    word_label = ReceiptWordLabel(
        image_id="550e8400-e29b-41d4-a716-446655440000",
        receipt_id=1,
        line_id=1,
        word_id=1,
        label="TOTAL",
        reasoning="test",
        timestamp_added=datetime.fromisoformat("2024-01-01T00:00:00"),
        validation_status="NONE",
    )

    image = word_label.to_item()
    # Remove PK/SK since parse_entity adds them
    image.pop("PK", None)
    image.pop("SK", None)

    result = parse_entity(image, "RECEIPT_WORD_LABEL", "new", pk, sk)

    assert result is not None
    assert isinstance(result, ReceiptWordLabel)
    assert result.label == "TOTAL"


def test_parse_entity_invalid_entity_type() -> None:
    """Test with unknown entity type."""
    image: Dict[str, Mapping[str, object]] = {}
    result = parse_entity(image, "UNKNOWN_TYPE", "new", "PK", "SK")
    assert result is None


def test_parse_entity_value_error_with_metrics() -> None:
    """Test that ValueError is caught and metrics recorded."""
    metrics = MockMetrics()
    pk = "IMAGE#550e8400-e29b-41d4-a716-446655440000"
    sk = "RECEIPT#00001#METADATA"

    # Invalid image that will cause ValueError
    image: Dict[str, Mapping[str, object]] = {
        "invalid_field": {"S": "value"}
    }

    result = parse_entity(
        image, "RECEIPT_METADATA", "old", pk, sk, metrics
    )

    assert result is None
    metric_names = [m[0] for m in metrics.counts]
    assert "EntityParsingError" in metric_names


def test_parse_entity_type_error_with_metrics() -> None:
    """Test that unexpected errors are caught and metrics recorded."""
    metrics = MockMetrics()

    # This should trigger TypeError/KeyError handling
    result = parse_entity(
        {"bad": "data"},  # type: ignore
        "RECEIPT_METADATA",
        "new",
        "PK",
        "SK",
        metrics,
    )

    assert result is None
    # Should record unexpected error metric
    metric_names = [m[0] for m in metrics.counts]
    assert "EntityParsingUnexpectedError" in metric_names


# Test parse_stream_record edge cases


def test_parse_stream_record_missing_keys() -> None:
    """Test with missing Keys field."""
    record: Dict[str, Any] = {
        "eventName": "MODIFY",
        "dynamodb": {},
    }
    result = parse_stream_record(record)
    assert result is None


def test_parse_stream_record_missing_dynamodb() -> None:
    """Test with missing dynamodb field."""
    record: Dict[str, Any] = {"eventName": "MODIFY"}
    result = parse_stream_record(record)
    assert result is None


def test_parse_stream_record_non_image_pk() -> None:
    """Test with PK that doesn't start with IMAGE#."""
    record: Dict[str, Any] = {
        "eventName": "MODIFY",
        "dynamodb": {
            "Keys": {
                "PK": {"S": "RECEIPT#00001"},
                "SK": {"S": "RECEIPT#00001#METADATA"},
            },
        },
    }
    result = parse_stream_record(record)
    assert result is None


def test_parse_stream_record_unknown_entity_type() -> None:
    """Test with SK that doesn't match any entity type."""
    record: Dict[str, Any] = {
        "eventName": "MODIFY",
        "dynamodb": {
            "Keys": {
                "PK": {"S": "IMAGE#550e8400-e29b-41d4-a716-446655440000"},
                "SK": {"S": "RECEIPT#00001#UNKNOWN"},
            },
        },
    }
    result = parse_stream_record(record)
    assert result is None


def test_parse_stream_record_old_entity_parse_failure() -> None:
    """Test when old entity exists but fails to parse."""
    record: Dict[str, Any] = {
        "eventName": "MODIFY",
        "dynamodb": {
            "Keys": {
                "PK": {"S": "IMAGE#550e8400-e29b-41d4-a716-446655440000"},
                "SK": {"S": "RECEIPT#00001#METADATA"},
            },
            "OldImage": {
                "invalid": {"S": "data"}
            },  # Invalid metadata
            "NewImage": {
                "invalid": {"S": "data"}
            },  # Invalid metadata
        },
    }
    result = parse_stream_record(record)
    # Should still return a ParsedStreamRecord but with None entities
    assert result is not None
    assert result.entity_type == "RECEIPT_METADATA"
    assert result.old_entity is None
    assert result.new_entity is None


def test_parse_stream_record_with_metrics() -> None:
    """Test that metrics are recorded on parsing errors."""
    metrics = MockMetrics()

    record: Dict[str, Any] = {
        "eventName": "MODIFY",
        "dynamodb": {
            "invalid": "structure"
        },  # Will cause KeyError
    }

    result = parse_stream_record(record, metrics)

    assert result is None
    metric_names = [m[0] for m in metrics.counts]
    assert "StreamRecordParsingError" in metric_names


def test_parse_stream_record_insert_with_new_image_only() -> None:
    """Test INSERT event with only NewImage."""
    metadata = ReceiptMetadata(
        image_id="550e8400-e29b-41d4-a716-446655440000",
        receipt_id=1,
        place_id="place123",
        merchant_name="Test",
        matched_fields=["name"],
        validated_by="PHONE_LOOKUP",
        timestamp=datetime.fromisoformat("2024-01-01T00:00:00"),
    )

    record: Dict[str, Any] = {
        "eventName": "INSERT",
        "dynamodb": {
            "Keys": metadata.key,
            "NewImage": metadata.to_item(),
        },
    }

    result = parse_stream_record(record)

    assert result is not None
    assert result.entity_type == "RECEIPT_METADATA"
    assert result.new_entity is not None
    assert result.old_entity is None


def test_parse_stream_record_remove_with_old_image_only() -> None:
    """Test REMOVE event with only OldImage."""
    word_label = ReceiptWordLabel(
        image_id="550e8400-e29b-41d4-a716-446655440000",
        receipt_id=1,
        line_id=1,
        word_id=1,
        label="TOTAL",
        reasoning="test",
        timestamp_added=datetime.fromisoformat("2024-01-01T00:00:00"),
        validation_status="NONE",
    )

    record: Dict[str, Any] = {
        "eventName": "REMOVE",
        "dynamodb": {
            "Keys": word_label.key,
            "OldImage": word_label.to_item(),
        },
    }

    result = parse_stream_record(record)

    assert result is not None
    assert result.entity_type == "RECEIPT_WORD_LABEL"
    assert result.old_entity is not None
    assert result.new_entity is None
