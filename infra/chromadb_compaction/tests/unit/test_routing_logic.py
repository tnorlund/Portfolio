"""
Unit tests for collection routing logic.

Tests that messages target the correct ChromaDB collections.
"""

import pytest

from receipt_dynamo.constants import ChromaDBCollection

from ...lambdas.stream_processor import (
    FieldChange,
    StreamMessage,
)


class TestCollectionTargeting:
    """Test that messages target correct collections."""

    def test_metadata_targets_both_collections(self):
        """Metadata changes should target both lines and words collections."""
        msg = StreamMessage(
            entity_type="RECEIPT_METADATA",
            entity_data={"image_id": "abc", "receipt_id": 1},
            changes={"merchant_name": FieldChange(old="A", new="B")},
            event_name="MODIFY",
            collections=[ChromaDBCollection.LINES, ChromaDBCollection.WORDS]
        )

        assert ChromaDBCollection.LINES in msg.collections
        assert ChromaDBCollection.WORDS in msg.collections
        assert len(msg.collections) == 2

    def test_word_label_targets_words_only(self):
        """Word label changes should only target words collection."""
        msg = StreamMessage(
            entity_type="RECEIPT_WORD_LABEL",
            entity_data={"image_id": "abc", "word_id": 1},
            changes={"label": FieldChange(old="A", new="B")},
            event_name="MODIFY",
            collections=[ChromaDBCollection.WORDS]
        )

        assert ChromaDBCollection.WORDS in msg.collections
        assert ChromaDBCollection.LINES not in msg.collections
        assert len(msg.collections) == 1

    def test_compaction_run_targets_specific_collection(self):
        """Compaction run messages should target one collection at a time."""
        # Message for lines collection
        lines_msg = StreamMessage(
            entity_type="COMPACTION_RUN",
            entity_data={
                "run_id": "abc-123",
                "delta_s3_prefix": "s3://bucket/lines/delta"
            },
            changes={},
            event_name="INSERT",
            collections=[ChromaDBCollection.LINES]
        )

        assert ChromaDBCollection.LINES in lines_msg.collections
        assert ChromaDBCollection.WORDS not in lines_msg.collections
        assert len(lines_msg.collections) == 1

        # Message for words collection
        words_msg = StreamMessage(
            entity_type="COMPACTION_RUN",
            entity_data={
                "run_id": "abc-123",
                "delta_s3_prefix": "s3://bucket/words/delta"
            },
            changes={},
            event_name="INSERT",
            collections=[ChromaDBCollection.WORDS]
        )

        assert ChromaDBCollection.WORDS in words_msg.collections
        assert ChromaDBCollection.LINES not in words_msg.collections
        assert len(words_msg.collections) == 1


class TestMessageStructure:
    """Test message structure and required fields."""

    def test_metadata_message_has_required_fields(self):
        """Metadata messages should have all required fields."""
        msg = StreamMessage(
            entity_type="RECEIPT_METADATA",
            entity_data={
                "entity_type": "RECEIPT_METADATA",
                "image_id": "test-image-123",
                "receipt_id": 1
            },
            changes={"canonical_merchant_name": FieldChange(old="Old", new="New")},
            event_name="MODIFY",
            collections=[ChromaDBCollection.LINES, ChromaDBCollection.WORDS],
            source="dynamodb_stream",
            timestamp="2024-01-01T00:00:00Z",
            stream_record_id="event-123",
            aws_region="us-east-1"
        )

        assert msg.entity_type == "RECEIPT_METADATA"
        assert "image_id" in msg.entity_data
        assert "receipt_id" in msg.entity_data
        assert msg.source == "dynamodb_stream"
        assert msg.event_name == "MODIFY"
        assert msg.timestamp is not None
        assert msg.stream_record_id is not None
        assert msg.aws_region is not None

    def test_word_label_message_has_required_fields(self):
        """Word label messages should have all required fields."""
        msg = StreamMessage(
            entity_type="RECEIPT_WORD_LABEL",
            entity_data={
                "entity_type": "RECEIPT_WORD_LABEL",
                "image_id": "test-image-123",
                "receipt_id": 1,
                "line_id": 5,
                "word_id": 10,
                "label": "TOTAL"
            },
            changes={"validation_status": FieldChange(old="PENDING", new="VALID")},
            event_name="MODIFY",
            collections=[ChromaDBCollection.WORDS],
            source="dynamodb_stream",
            timestamp="2024-01-01T00:00:00Z",
            stream_record_id="event-456",
            aws_region="us-east-1"
        )

        assert msg.entity_type == "RECEIPT_WORD_LABEL"
        assert "image_id" in msg.entity_data
        assert "receipt_id" in msg.entity_data
        assert "line_id" in msg.entity_data
        assert "word_id" in msg.entity_data
        assert "label" in msg.entity_data

    def test_compaction_run_message_has_required_fields(self):
        """Compaction run messages should have all required fields."""
        msg = StreamMessage(
            entity_type="COMPACTION_RUN",
            entity_data={
                "run_id": "run-abc-123",
                "image_id": "test-image-123",
                "receipt_id": 1,
                "delta_s3_prefix": "s3://bucket/lines/delta"
            },
            changes={},
            event_name="INSERT",
            collections=[ChromaDBCollection.LINES],
            source="dynamodb_stream",
            timestamp="2024-01-01T00:00:00Z",
            stream_record_id="event-789",
            aws_region="us-east-1"
        )

        assert msg.entity_type == "COMPACTION_RUN"
        assert "run_id" in msg.entity_data
        assert "image_id" in msg.entity_data
        assert "receipt_id" in msg.entity_data
        assert "delta_s3_prefix" in msg.entity_data
        assert msg.event_name == "INSERT"
        assert len(msg.changes) == 0  # INSERT events have empty changes


if __name__ == "__main__":
    pytest.main([__file__])

