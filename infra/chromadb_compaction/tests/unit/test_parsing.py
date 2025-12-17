"""
Unit tests for DynamoDB stream record parsing.

Tests entity detection and parsing logic without AWS dependencies.
"""

from datetime import datetime

import pytest

from receipt_dynamo.constants import ValidationMethod, ValidationStatus
from receipt_dynamo.entities.compaction_run import CompactionRun
from receipt_dynamo.entities.receipt_metadata import ReceiptMetadata
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel

from ...lambdas.stream_processor import (
    ParsedStreamRecord,
    _is_compaction_run,
    _parse_compaction_run,
    parse_stream_record,
)


# Helper functions to create test entities
def create_test_receipt_metadata(
    merchant_name: str = "Test Merchant",
    canonical_merchant_name: str = "",
    **kwargs,
) -> ReceiptMetadata:
    """Create a test ReceiptMetadata entity with sensible defaults."""
    defaults = {
        "image_id": "550e8400-e29b-41d4-a716-446655440000",
        "receipt_id": 1,
        "place_id": "place123",
        "merchant_name": merchant_name,
        "canonical_merchant_name": canonical_merchant_name,
        "matched_fields": ["name"],
        "validated_by": ValidationMethod.PHONE_LOOKUP,
        "timestamp": datetime.fromisoformat("2024-01-01T00:00:00"),
    }
    defaults.update(kwargs)
    return ReceiptMetadata(**defaults)


def create_test_receipt_word_label(
    label: str = "TEST_LABEL", **kwargs
) -> ReceiptWordLabel:
    """Create a test ReceiptWordLabel entity with sensible defaults."""
    defaults = {
        "image_id": "550e8400-e29b-41d4-a716-446655440000",
        "receipt_id": 1,
        "line_id": 2,
        "word_id": 3,
        "label": label,
        "validation_status": ValidationStatus.VALID,
        "reasoning": "Test reasoning",
        "timestamp_added": datetime.fromisoformat("2024-01-01T00:00:00"),
    }
    defaults.update(kwargs)
    return ReceiptWordLabel(**defaults)


def create_stream_record_from_entities(
    event_name: str,
    old_entity=None,
    new_entity=None,
    event_id: str = "test-event-1",
    aws_region: str = "us-east-1",
):
    """Create a DynamoDB stream record from entity objects."""
    entity = old_entity or new_entity
    if not entity:
        raise ValueError("At least one entity (old or new) must be provided")

    keys = entity.key

    record = {
        "eventID": event_id,
        "eventName": event_name,
        "awsRegion": aws_region,
        "dynamodb": {"Keys": keys},
    }

    if old_entity:
        record["dynamodb"]["OldImage"] = old_entity.to_item()
    if new_entity:
        record["dynamodb"]["NewImage"] = new_entity.to_item()

    return record


class TestParseStreamRecord:
    """Test DynamoDB stream record parsing logic."""

    def test_parse_receipt_metadata_modify_event(self):
        """Test parsing RECEIPT_METADATA MODIFY event."""
        old_entity = create_test_receipt_metadata(merchant_name="Old Merchant")
        new_entity = create_test_receipt_metadata(merchant_name="New Merchant")

        record = create_stream_record_from_entities(
            event_name="MODIFY", old_entity=old_entity, new_entity=new_entity
        )

        result = parse_stream_record(record)

        assert result is not None
        assert isinstance(result, ParsedStreamRecord)
        assert result.entity_type == "RECEIPT_METADATA"
        expected_key = old_entity.key
        assert result.pk == expected_key["PK"]["S"]
        assert result.sk == expected_key["SK"]["S"]
        assert result.old_entity is not None
        assert result.new_entity is not None
        assert isinstance(result.old_entity, ReceiptMetadata)
        assert isinstance(result.new_entity, ReceiptMetadata)
        assert result.old_entity.merchant_name == old_entity.merchant_name
        assert result.new_entity.merchant_name == new_entity.merchant_name

    def test_parse_receipt_word_label_remove_event(self):
        """Test parsing RECEIPT_WORD_LABEL REMOVE event."""
        old_entity = create_test_receipt_word_label(label="TOTAL")

        record = create_stream_record_from_entities(
            event_name="REMOVE", old_entity=old_entity, new_entity=None
        )

        result = parse_stream_record(record)

        assert result is not None
        assert isinstance(result, ParsedStreamRecord)
        assert result.entity_type == "RECEIPT_WORD_LABEL"
        expected_key = old_entity.key
        assert result.pk == expected_key["PK"]["S"]
        assert result.sk == expected_key["SK"]["S"]
        assert result.old_entity is not None
        assert result.new_entity is None
        assert isinstance(result.old_entity, ReceiptWordLabel)
        assert result.old_entity.label == old_entity.label
        assert (
            result.old_entity.validation_status == old_entity.validation_status
        )

    def test_parse_non_image_pk(self):
        """Test that non-IMAGE PKs are ignored."""
        record = {
            "eventName": "MODIFY",
            "dynamodb": {
                "Keys": {
                    "PK": {"S": "BATCH#12345"},
                    "SK": {"S": "RECEIPT#00001#METADATA"},
                }
            },
        }

        result = parse_stream_record(record)
        assert result is None

    def test_parse_receipt_line_ignored(self):
        """Test that RECEIPT_LINE entities are ignored."""
        record = {
            "eventName": "MODIFY",
            "dynamodb": {
                "Keys": {
                    "PK": {"S": "IMAGE#550e8400-e29b-41d4-a716-446655440000"},
                    "SK": {"S": "RECEIPT#00001#LINE#00002"},
                }
            },
        }

        result = parse_stream_record(record)
        assert result is None

    def test_parse_receipt_word_ignored(self):
        """Test that RECEIPT_WORD entities are ignored."""
        record = {
            "eventName": "MODIFY",
            "dynamodb": {
                "Keys": {
                    "PK": {"S": "IMAGE#550e8400-e29b-41d4-a716-446655440000"},
                    "SK": {"S": "RECEIPT#00001#LINE#00002#WORD#00003"},
                }
            },
        }

        result = parse_stream_record(record)
        assert result is None

    def test_parse_invalid_record_format(self):
        """Test parsing record with missing required fields."""
        record = {
            "eventName": "MODIFY",
            "dynamodb": {
                # Missing Keys
            },
        }

        result = parse_stream_record(record)
        assert result is None


class TestCompactionRunParsing:
    """Test COMPACTION_RUN INSERT event parsing and handling."""

    def test_is_compaction_run_detection(self):
        """Test _is_compaction_run correctly identifies compaction run items."""
        # Valid compaction run
        assert _is_compaction_run(
            "IMAGE#7e2bd911-7afb-4e0a-84de-57f51ce4daff",
            "RECEIPT#00001#COMPACTION_RUN#test-run-123"
        )

        # Not a compaction run (metadata)
        assert not _is_compaction_run(
            "IMAGE#7e2bd911-7afb-4e0a-84de-57f51ce4daff",
            "RECEIPT#00001#METADATA"
        )

        # Not an IMAGE PK
        assert not _is_compaction_run(
            "BATCH#12345",
            "RECEIPT#00001#COMPACTION_RUN#test-run-123"
        )

    def test_parse_compaction_run(self):
        """Test _parse_compaction_run extracts fields correctly."""
        compaction_run = CompactionRun(
            run_id="550e8400-e29b-41d4-a716-446655440001",  # Valid UUID
            image_id="7e2bd911-7afb-4e0a-84de-57f51ce4daff",
            receipt_id=1,
            lines_delta_prefix="s3://bucket/lines/delta",
            words_delta_prefix="s3://bucket/words/delta",
            lines_state="PENDING",
            words_state="PENDING",
            lines_merged_vectors=0,
            words_merged_vectors=0,
            created_at="2025-08-08T03:53:53.200541+00:00",
        )

        new_image = compaction_run.to_item()
        pk = "IMAGE#7e2bd911-7afb-4e0a-84de-57f51ce4daff"
        sk = "RECEIPT#00001#COMPACTION_RUN#550e8400-e29b-41d4-a716-446655440001"

        parsed = _parse_compaction_run(new_image, pk, sk)

        assert isinstance(parsed, CompactionRun)
        assert parsed.run_id == "550e8400-e29b-41d4-a716-446655440001"
        assert parsed.image_id == "7e2bd911-7afb-4e0a-84de-57f51ce4daff"
        assert parsed.receipt_id == 1
        assert parsed.lines_delta_prefix == "s3://bucket/lines/delta"
        assert parsed.words_delta_prefix == "s3://bucket/words/delta"

    def test_compaction_run_parsed_by_standard_parser(self):
        """Test that parse_stream_record CAN parse COMPACTION_RUN."""
        compaction_run = CompactionRun(
            run_id="550e8400-e29b-41d4-a716-446655440002",  # Valid UUID
            image_id="7e2bd911-7afb-4e0a-84de-57f51ce4daff",
            receipt_id=1,
            lines_delta_prefix="s3://bucket/lines/delta",
            words_delta_prefix="s3://bucket/words/delta",
            lines_state="PENDING",
            words_state="PENDING",
            lines_merged_vectors=0,
            words_merged_vectors=0,
            created_at="2025-08-08T03:53:53.200541+00:00",
        )

        record = {
            "eventID": "compaction-test-1",
            "eventName": "INSERT",
            "awsRegion": "us-east-1",
            "dynamodb": {
                "Keys": compaction_run.key,
                "NewImage": compaction_run.to_item(),
            }
        }

        # parse_stream_record CAN parse COMPACTION_RUN 
        # (though lambda_handler uses a fast-path for INSERT events)
        result = parse_stream_record(record)
        assert result is not None
        assert result.entity_type == "COMPACTION_RUN"
        assert result.pk == "IMAGE#7e2bd911-7afb-4e0a-84de-57f51ce4daff"


if __name__ == "__main__":
    pytest.main([__file__])

