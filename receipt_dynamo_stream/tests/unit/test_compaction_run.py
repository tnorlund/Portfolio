"""Comprehensive unit tests for compaction_run parsing module."""

from typing import Any, Dict, Optional

import pytest

from receipt_dynamo_stream.parsing.compaction_run import (
    is_compaction_run,
    is_embeddings_completed,
    parse_compaction_run,
)

# Test is_compaction_run


def test_is_compaction_run_valid() -> None:
    """Test detection of valid compaction run PK/SK."""
    pk = "IMAGE#550e8400-e29b-41d4-a716-446655440000"
    sk = "RECEIPT#00001#COMPACTION_RUN#run-abc-123"
    assert is_compaction_run(pk, sk) is True


def test_is_compaction_run_different_formats() -> None:
    """Test various valid compaction run SK formats."""
    pk = "IMAGE#550e8400-e29b-41d4-a716-446655440000"

    test_cases = [
        "RECEIPT#00001#COMPACTION_RUN#run-123",
        "RECEIPT#99999#COMPACTION_RUN#abc",
        "RECEIPT#00001#COMPACTION_RUN#2024-01-01T00:00:00",
    ]

    for sk in test_cases:
        assert is_compaction_run(pk, sk) is True


def test_is_compaction_run_invalid_pk() -> None:
    """Test with invalid PK (not starting with IMAGE#)."""
    pk = "RECEIPT#550e8400-e29b-41d4-a716-446655440000"
    sk = "RECEIPT#00001#COMPACTION_RUN#run-abc"
    assert is_compaction_run(pk, sk) is False


def test_is_compaction_run_invalid_sk() -> None:
    """Test with SK that doesn't contain COMPACTION_RUN."""
    pk = "IMAGE#550e8400-e29b-41d4-a716-446655440000"

    test_cases = [
        "RECEIPT#00001#PLACE",
        "RECEIPT#00001#LINE#00001",
        "RECEIPT#00001#LABEL#TOTAL",
    ]

    for sk in test_cases:
        assert is_compaction_run(pk, sk) is False


def test_is_compaction_run_empty_strings() -> None:
    """Test with empty PK/SK."""
    assert is_compaction_run("", "") is False
    assert is_compaction_run("IMAGE#test", "") is False
    assert is_compaction_run("", "RECEIPT#00001#COMPACTION_RUN#run") is False


# Test parse_compaction_run


def test_parse_compaction_run_complete_data() -> None:
    """Test parsing with all fields present."""
    pk = "IMAGE#550e8400-e29b-41d4-a716-446655440000"
    sk = "RECEIPT#00001#COMPACTION_RUN#run-abc-123"
    new_image: Dict[str, Any] = {
        "run_id": {"S": "run-abc-123"},
        "receipt_id": {"N": "1"},
        "lines_delta_prefix": {"S": "s3://bucket/lines/delta/"},
        "words_delta_prefix": {"S": "s3://bucket/words/delta/"},
    }

    result = parse_compaction_run(new_image, pk, sk)

    assert result["run_id"] == "run-abc-123"
    assert result["image_id"] == "550e8400-e29b-41d4-a716-446655440000"
    assert result["receipt_id"] == 1
    assert result["lines_delta_prefix"] == "s3://bucket/lines/delta/"
    assert result["words_delta_prefix"] == "s3://bucket/words/delta/"


def test_parse_compaction_run_run_id_from_sk() -> None:
    """Test extracting run_id from SK when not in new_image."""
    pk = "IMAGE#550e8400-e29b-41d4-a716-446655440000"
    sk = "RECEIPT#00001#COMPACTION_RUN#run-xyz"
    new_image: Dict[str, Any] = {
        "receipt_id": {"N": "1"},
        "lines_delta_prefix": {"S": "s3://bucket/lines/"},
        "words_delta_prefix": {"S": "s3://bucket/words/"},
    }

    result = parse_compaction_run(new_image, pk, sk)

    assert result["run_id"] == "run-xyz"


def test_parse_compaction_run_receipt_id_from_sk() -> None:
    """Test extracting receipt_id from SK."""
    pk = "IMAGE#550e8400-e29b-41d4-a716-446655440000"
    sk = "RECEIPT#00042#COMPACTION_RUN#run-abc"
    new_image: Dict[str, Any] = {
        "run_id": {"S": "run-abc"},
        "lines_delta_prefix": {"S": "s3://bucket/lines/"},
        "words_delta_prefix": {"S": "s3://bucket/words/"},
    }

    result = parse_compaction_run(new_image, pk, sk)

    assert result["receipt_id"] == 42


def test_parse_compaction_run_receipt_id_from_new_image() -> None:
    """Test using receipt_id from new_image when SK parsing fails."""
    pk = "IMAGE#550e8400-e29b-41d4-a716-446655440000"
    sk = "RECEIPT#INVALID#COMPACTION_RUN#run-abc"  # Invalid receipt number
    new_image: Dict[str, Any] = {
        "run_id": {"S": "run-abc"},
        "receipt_id": {"N": "99"},
        "lines_delta_prefix": {"S": "s3://bucket/lines/"},
        "words_delta_prefix": {"S": "s3://bucket/words/"},
    }

    result = parse_compaction_run(new_image, pk, sk)

    assert result["receipt_id"] == 99


def test_parse_compaction_run_missing_pk() -> None:
    """Test with missing PK."""
    sk = "RECEIPT#00001#COMPACTION_RUN#run-abc"
    new_image: Dict[str, Any] = {}

    with pytest.raises(ValueError, match="PK and SK are required"):
        parse_compaction_run(new_image, "", sk)


def test_parse_compaction_run_missing_sk() -> None:
    """Test with missing SK."""
    pk = "IMAGE#550e8400-e29b-41d4-a716-446655440000"
    new_image: Dict[str, Any] = {}

    with pytest.raises(ValueError, match="PK and SK are required"):
        parse_compaction_run(new_image, pk, "")


def test_parse_compaction_run_missing_receipt_id() -> None:
    """Test when receipt_id cannot be parsed from SK or new_image."""
    pk = "IMAGE#550e8400-e29b-41d4-a716-446655440000"
    sk = "RECEIPT#INVALID#COMPACTION_RUN#run-abc"
    new_image: Dict[str, Any] = {
        "run_id": {"S": "run-abc"},
        # No receipt_id field
    }

    with pytest.raises(ValueError, match="Could not parse receipt_id"):
        parse_compaction_run(new_image, pk, sk)


def test_parse_compaction_run_none_delta_prefixes() -> None:
    """Test with missing delta prefix fields."""
    pk = "IMAGE#550e8400-e29b-41d4-a716-446655440000"
    sk = "RECEIPT#00001#COMPACTION_RUN#run-abc"
    new_image: Dict[str, Any] = {
        "run_id": {"S": "run-abc"},
        "receipt_id": {"N": "1"},
        # No delta prefixes
    }

    result = parse_compaction_run(new_image, pk, sk)

    assert result["lines_delta_prefix"] is None
    assert result["words_delta_prefix"] is None


def test_parse_compaction_run_complex_image_id() -> None:
    """Test parsing with various image ID formats."""
    test_cases = [
        "IMAGE#550e8400-e29b-41d4-a716-446655440000",
        "IMAGE#abc-def-ghi",
        "IMAGE#12345",
    ]

    for pk in test_cases:
        sk = "RECEIPT#00001#COMPACTION_RUN#run-abc"
        new_image: Dict[str, Any] = {
            "receipt_id": {"N": "1"},
        }

        result = parse_compaction_run(new_image, pk, sk)
        expected_image_id = pk.split("#", 1)[-1]
        assert result["image_id"] == expected_image_id


# Test is_embeddings_completed


def test_is_embeddings_completed_both_states_completed() -> None:
    """Test when both states are COMPLETED."""
    new_image: Dict[str, Any] = {
        "lines_state": {"S": "COMPLETED"},
        "words_state": {"S": "COMPLETED"},
    }
    assert is_embeddings_completed(new_image) is True


def test_is_embeddings_completed_both_timestamps_present() -> None:
    """Test when both finished_at timestamps exist."""
    new_image: Dict[str, Any] = {
        "lines_finished_at": {"S": "2024-01-01T00:00:00Z"},
        "words_finished_at": {"S": "2024-01-01T00:00:00Z"},
    }
    assert is_embeddings_completed(new_image) is True


def test_is_embeddings_completed_states_and_timestamps() -> None:
    """Test when both states and timestamps indicate completion."""
    new_image: Dict[str, Any] = {
        "lines_state": {"S": "COMPLETED"},
        "words_state": {"S": "COMPLETED"},
        "lines_finished_at": {"S": "2024-01-01T00:00:00Z"},
        "words_finished_at": {"S": "2024-01-01T00:00:00Z"},
    }
    assert is_embeddings_completed(new_image) is True


def test_is_embeddings_completed_only_lines_completed() -> None:
    """Test when only lines is completed."""
    new_image: Dict[str, Any] = {
        "lines_state": {"S": "COMPLETED"},
        "words_state": {"S": "PROCESSING"},
    }
    assert is_embeddings_completed(new_image) is False


def test_is_embeddings_completed_only_words_completed() -> None:
    """Test when only words is completed."""
    new_image: Dict[str, Any] = {
        "lines_state": {"S": "PROCESSING"},
        "words_state": {"S": "COMPLETED"},
    }
    assert is_embeddings_completed(new_image) is False


def test_is_embeddings_completed_only_lines_timestamp() -> None:
    """Test when only lines has finished_at timestamp."""
    new_image: Dict[str, Any] = {
        "lines_finished_at": {"S": "2024-01-01T00:00:00Z"},
    }
    assert is_embeddings_completed(new_image) is False


def test_is_embeddings_completed_only_words_timestamp() -> None:
    """Test when only words has finished_at timestamp."""
    new_image: Dict[str, Any] = {
        "words_finished_at": {"S": "2024-01-01T00:00:00Z"},
    }
    assert is_embeddings_completed(new_image) is False


def test_is_embeddings_completed_neither_completed() -> None:
    """Test when neither are completed."""
    new_image: Dict[str, Any] = {
        "lines_state": {"S": "PROCESSING"},
        "words_state": {"S": "PROCESSING"},
    }
    assert is_embeddings_completed(new_image) is False


def test_is_embeddings_completed_empty_image() -> None:
    """Test with empty new_image."""
    assert is_embeddings_completed({}) is False


def test_is_embeddings_completed_none_image() -> None:
    """Test with None new_image - tests runtime behavior with invalid input.

    This test verifies the function handles None gracefully. While the function
    signature expects Dict[str, Any], this tests the actual runtime behavior when
    None is passed, which can occur if DynamoDB returns unexpected data.
    """
    assert is_embeddings_completed(None) is False  # type: ignore[arg-type]


def test_is_embeddings_completed_invalid_timestamp_structure() -> None:
    """Test when timestamps exist but don't have 'S' key."""
    new_image: Dict[str, Any] = {
        "lines_finished_at": {"N": "123"},  # Wrong type
        "words_finished_at": {"N": "456"},
    }
    assert is_embeddings_completed(new_image) is False


def test_is_embeddings_completed_mixed_indicators() -> None:
    """Test when one uses state and one uses timestamp."""
    new_image: Dict[str, Any] = {
        "lines_state": {"S": "COMPLETED"},
        "words_finished_at": {"S": "2024-01-01T00:00:00Z"},
    }
    # Should be False because not both conditions are met
    assert is_embeddings_completed(new_image) is False
