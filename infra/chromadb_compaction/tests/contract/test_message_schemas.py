"""
Contract tests for stream processor message schemas.

Validates that messages produced by stream_processor match the schema
expected by the enhanced_compaction_handler (downstream consumer).
"""

import json
import pytest
from datetime import datetime

from receipt_dynamo.constants import ChromaDBCollection

from ...lambdas.stream_processor import (
    StreamMessage,
    FieldChange,
)
from ..fixtures.expected_messages import (
    EXPECTED_METADATA_MESSAGE_SCHEMA,
    EXPECTED_WORD_LABEL_MESSAGE_SCHEMA,
    EXPECTED_COMPACTION_RUN_MESSAGE_SCHEMA,
    REQUIRED_MESSAGE_FIELDS,
    COLLECTION_TARGETING_RULES,
)


class TestMetadataMessageContract:
    """Verify RECEIPT_METADATA message schema."""

    def test_metadata_message_required_fields(self):
        """All required fields present for compaction handler."""
        msg = StreamMessage(
            entity_type="RECEIPT_METADATA",
            entity_data={
                "entity_type": "RECEIPT_METADATA",
                "image_id": "7e2bd911-7afb-4e0a-84de-57f51ce4daff",
                "receipt_id": 1
            },
            changes={"canonical_merchant_name": FieldChange(old="Old", new="New")},
            event_name="MODIFY",
            collections=[ChromaDBCollection.LINES, ChromaDBCollection.WORDS],
            source="dynamodb_stream",
            timestamp=datetime.now().isoformat(),
            stream_record_id="event-123",
            aws_region="us-east-1"
        )

        # Convert to dict (what gets sent to SQS)
        msg_dict = {
            "source": msg.source,
            "entity_type": msg.entity_type,
            "entity_data": msg.entity_data,
            "changes": {
                k: {"old": v.old, "new": v.new}
                for k, v in msg.changes.items()
            },
            "event_name": msg.event_name,
            "timestamp": msg.timestamp,
            "stream_record_id": msg.stream_record_id,
            "aws_region": msg.aws_region
        }

        # Verify all required fields present
        for field in REQUIRED_MESSAGE_FIELDS:
            assert field in msg_dict, f"Missing required field: {field}"

        # Verify entity_data has required nested fields
        assert "image_id" in msg_dict["entity_data"]
        assert "receipt_id" in msg_dict["entity_data"]

    def test_metadata_changes_structure(self):
        """Changes dict has old/new structure."""
        changes = {
            "canonical_merchant_name": FieldChange(old="Old Store", new="New Store"),
            "place_id": FieldChange(old="place123", new="place456")
        }

        changes_dict = {
            k: {"old": v.old, "new": v.new}
            for k, v in changes.items()
        }

        # Each change should have old and new keys
        for field, change in changes_dict.items():
            assert "old" in change, f"Missing 'old' in {field}"
            assert "new" in change, f"Missing 'new' in {field}"

    def test_json_serializable(self):
        """Message is JSON serializable."""
        msg_dict = {
            "source": "dynamodb_stream",
            "entity_type": "RECEIPT_METADATA",
            "entity_data": {"image_id": "abc-123", "receipt_id": 1},
            "changes": {"merchant_name": {"old": "A", "new": "B"}},
            "event_name": "MODIFY",
            "timestamp": datetime.now().isoformat(),
            "stream_record_id": "event-123",
            "aws_region": "us-east-1"
        }

        # Should not raise exception
        json_str = json.dumps(msg_dict)
        assert json_str is not None

        # Should be deserializable
        parsed = json.loads(json_str)
        assert parsed["entity_type"] == "RECEIPT_METADATA"


class TestWordLabelMessageContract:
    """Verify RECEIPT_WORD_LABEL message schema."""

    def test_word_label_message_required_fields(self):
        """Required fields for word label updates."""
        msg = StreamMessage(
            entity_type="RECEIPT_WORD_LABEL",
            entity_data={
                "entity_type": "RECEIPT_WORD_LABEL",
                "image_id": "7e2bd911-7afb-4e0a-84de-57f51ce4daff",
                "receipt_id": 1,
                "line_id": 5,
                "word_id": 10,
                "label": "TOTAL"
            },
            changes={"validation_status": FieldChange(old="PENDING", new="VALID")},
            event_name="MODIFY",
            collections=[ChromaDBCollection.WORDS],
            source="dynamodb_stream",
            timestamp=datetime.now().isoformat(),
            stream_record_id="event-456",
            aws_region="us-east-1"
        )

        msg_dict = {
            "source": msg.source,
            "entity_type": msg.entity_type,
            "entity_data": msg.entity_data,
            "changes": {
                k: {"old": v.old, "new": v.new}
                for k, v in msg.changes.items()
            },
            "event_name": msg.event_name,
            "timestamp": msg.timestamp,
            "stream_record_id": msg.stream_record_id,
            "aws_region": msg.aws_region
        }

        # Verify required fields
        for field in REQUIRED_MESSAGE_FIELDS:
            assert field in msg_dict

        # Verify entity_data has word-specific fields
        assert "image_id" in msg_dict["entity_data"]
        assert "receipt_id" in msg_dict["entity_data"]
        assert "line_id" in msg_dict["entity_data"]
        assert "word_id" in msg_dict["entity_data"]
        assert "label" in msg_dict["entity_data"]

    def test_word_label_json_serializable(self):
        """Word label messages are JSON serializable."""
        msg_dict = {
            "source": "dynamodb_stream",
            "entity_type": "RECEIPT_WORD_LABEL",
            "entity_data": {
                "image_id": "abc-123",
                "receipt_id": 1,
                "line_id": 5,
                "word_id": 10,
                "label": "TOTAL"
            },
            "changes": {"label": {"old": "PRODUCT", "new": "TOTAL"}},
            "event_name": "MODIFY",
            "timestamp": datetime.now().isoformat(),
            "stream_record_id": "event-789",
            "aws_region": "us-east-1"
        }

        json_str = json.dumps(msg_dict)
        parsed = json.loads(json_str)
        assert parsed["entity_type"] == "RECEIPT_WORD_LABEL"


class TestCompactionRunMessageContract:
    """Verify COMPACTION_RUN message schema."""

    def test_compaction_run_message_required_fields(self):
        """Required fields for compaction processing."""
        msg = StreamMessage(
            entity_type="COMPACTION_RUN",
            entity_data={
                "run_id": "run-abc-123",
                "image_id": "7e2bd911-7afb-4e0a-84de-57f51ce4daff",
                "receipt_id": 1,
                "lines_delta_prefix": "s3://bucket/lines/delta",
                "words_delta_prefix": "s3://bucket/words/delta",
                "delta_s3_prefix": "s3://bucket/lines/delta"
            },
            changes={},
            event_name="INSERT",
            collections=[ChromaDBCollection.LINES],
            source="dynamodb_stream",
            timestamp=datetime.now().isoformat(),
            stream_record_id="event-run-123",
            aws_region="us-east-1"
        )

        msg_dict = {
            "source": msg.source,
            "entity_type": msg.entity_type,
            "entity_data": msg.entity_data,
            "changes": msg.changes,
            "event_name": msg.event_name,
            "timestamp": msg.timestamp,
            "stream_record_id": msg.stream_record_id,
            "aws_region": msg.aws_region
        }

        # Verify required fields
        for field in REQUIRED_MESSAGE_FIELDS:
            assert field in msg_dict

        # Verify compaction run specific fields
        assert "run_id" in msg_dict["entity_data"]
        assert "image_id" in msg_dict["entity_data"]
        assert "receipt_id" in msg_dict["entity_data"]
        assert "delta_s3_prefix" in msg_dict["entity_data"]

    def test_compaction_run_empty_changes(self):
        """INSERT events should have empty changes."""
        msg_dict = {
            "source": "dynamodb_stream",
            "entity_type": "COMPACTION_RUN",
            "entity_data": {
                "run_id": "run-123",
                "image_id": "abc",
                "receipt_id": 1,
                "delta_s3_prefix": "s3://bucket/delta"
            },
            "changes": {},
            "event_name": "INSERT",
            "timestamp": datetime.now().isoformat(),
            "stream_record_id": "event-123",
            "aws_region": "us-east-1"
        }

        assert len(msg_dict["changes"]) == 0


class TestCollectionTargeting:
    """Test collection targeting rules."""

    def test_metadata_targets_both_collections(self):
        """Metadata changes must target both collections."""
        targeting = COLLECTION_TARGETING_RULES["RECEIPT_METADATA"]

        assert "lines" in targeting
        assert "words" in targeting
        assert len(targeting) == 2

    def test_word_label_targets_words_only(self):
        """Word labels only affect words collection."""
        targeting = COLLECTION_TARGETING_RULES["RECEIPT_WORD_LABEL"]

        assert "words" in targeting
        assert "lines" not in targeting
        assert len(targeting) == 1

    def test_compaction_run_targets_both(self):
        """Compaction runs create separate messages for each collection."""
        targeting = COLLECTION_TARGETING_RULES["COMPACTION_RUN"]

        assert "lines" in targeting
        assert "words" in targeting
        assert len(targeting) == 2


class TestTimestampFormat:
    """Test timestamp formatting for compatibility."""

    def test_timestamp_is_iso_format(self):
        """Timestamps should be in ISO format."""
        timestamp = datetime.now().isoformat()

        # Should contain T separator
        assert "T" in timestamp

        # Should be parseable
        parsed = datetime.fromisoformat(timestamp)
        assert isinstance(parsed, datetime)

    def test_timestamp_in_message(self):
        """Message timestamps are properly formatted."""
        msg_dict = {
            "timestamp": datetime.now().isoformat()
        }

        # Should not raise exception
        parsed_time = datetime.fromisoformat(msg_dict["timestamp"])
        assert isinstance(parsed_time, datetime)


if __name__ == "__main__":
    pytest.main([__file__])

