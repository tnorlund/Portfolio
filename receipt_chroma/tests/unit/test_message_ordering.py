"""Unit tests for message ordering and deduplication logic."""

import pytest

from receipt_dynamo.constants import ChromaDBCollection
from receipt_dynamo_stream.models import StreamMessage, StreamRecordContext

from receipt_chroma.compaction.message_ordering import (
    _get_entity_key,
    sort_and_deduplicate_messages,
)


class TestGetEntityKey:
    """Test the _get_entity_key function."""

    def test_receipt_place_key(self):
        """Test entity key extraction for RECEIPT_PLACE."""
        msg = StreamMessage(
            entity_type="RECEIPT_PLACE",
            entity_data={"image_id": "img-1", "receipt_id": 1},
            changes={},
            event_name="MODIFY",
            collections=(ChromaDBCollection.LINES,),
            context=StreamRecordContext(
                timestamp="2025-01-01T00:00:00Z",
                record_id="msg-1",
                aws_region="us-east-1",
            ),
        )
        key = _get_entity_key(msg)
        assert key == ("img-1", "1")

    def test_receipt_key(self):
        """Test entity key extraction for RECEIPT."""
        msg = StreamMessage(
            entity_type="RECEIPT",
            entity_data={"image_id": "img-2", "receipt_id": 2},
            changes={},
            event_name="INSERT",
            collections=(ChromaDBCollection.LINES,),
            context=StreamRecordContext(
                timestamp="2025-01-01T00:00:00Z",
                record_id="msg-2",
                aws_region="us-east-1",
            ),
        )
        key = _get_entity_key(msg)
        assert key == ("img-2", "2")

    def test_receipt_line_key(self):
        """Test entity key extraction for RECEIPT_LINE."""
        msg = StreamMessage(
            entity_type="RECEIPT_LINE",
            entity_data={"image_id": "img-3", "receipt_id": 3, "line_id": 5},
            changes={},
            event_name="MODIFY",
            collections=(ChromaDBCollection.LINES,),
            context=StreamRecordContext(
                timestamp="2025-01-01T00:00:00Z",
                record_id="msg-3",
                aws_region="us-east-1",
            ),
        )
        key = _get_entity_key(msg)
        assert key == ("img-3", "3", "5")

    def test_receipt_word_key(self):
        """Test entity key extraction for RECEIPT_WORD."""
        msg = StreamMessage(
            entity_type="RECEIPT_WORD",
            entity_data={
                "image_id": "img-4",
                "receipt_id": 4,
                "line_id": 6,
                "word_id": 7,
            },
            changes={},
            event_name="MODIFY",
            collections=(ChromaDBCollection.WORDS,),
            context=StreamRecordContext(
                timestamp="2025-01-01T00:00:00Z",
                record_id="msg-4",
                aws_region="us-east-1",
            ),
        )
        key = _get_entity_key(msg)
        assert key == ("img-4", "4", "6", "7")

    def test_receipt_word_label_key(self):
        """Test entity key extraction for RECEIPT_WORD_LABEL."""
        msg = StreamMessage(
            entity_type="RECEIPT_WORD_LABEL",
            entity_data={
                "image_id": "img-5",
                "receipt_id": 5,
                "line_id": 8,
                "word_id": 9,
            },
            changes={},
            event_name="REMOVE",
            collections=(ChromaDBCollection.WORDS,),
            context=StreamRecordContext(
                timestamp="2025-01-01T00:00:00Z",
                record_id="msg-5",
                aws_region="us-east-1",
            ),
        )
        key = _get_entity_key(msg)
        assert key == ("img-5", "5", "8", "9")

    def test_unknown_entity_type_key(self):
        """Test entity key extraction for unknown entity type."""
        msg = StreamMessage(
            entity_type="UNKNOWN_TYPE",
            entity_data={"foo": "bar", "baz": 123},
            changes={},
            event_name="INSERT",
            collections=(ChromaDBCollection.LINES,),
            context=StreamRecordContext(
                timestamp="2025-01-01T00:00:00Z",
                record_id="msg-6",
                aws_region="us-east-1",
            ),
        )
        key = _get_entity_key(msg)
        assert key[0] == "UNKNOWN_TYPE"
        # Second element is stringified sorted items
        assert "baz" in key[1]
        assert "foo" in key[1]


class TestSortAndDeduplicateMessages:
    """Test the sort_and_deduplicate_messages function."""

    def test_empty_list(self):
        """Test with empty message list."""
        result = sort_and_deduplicate_messages([])
        assert result == []

    def test_sort_remove_before_insert(self):
        """Test that REMOVE messages are sorted before INSERT/MODIFY."""
        insert_msg = StreamMessage(
            entity_type="RECEIPT_PLACE",
            entity_data={"image_id": "img-1", "receipt_id": 1},
            changes={},
            event_name="INSERT",
            collections=(ChromaDBCollection.LINES,),
            context=StreamRecordContext(
                timestamp="2025-01-01T00:00:01Z",
                record_id="msg-insert",
                aws_region="us-east-1",
            ),
        )
        remove_msg = StreamMessage(
            entity_type="RECEIPT_PLACE",
            entity_data={"image_id": "img-1", "receipt_id": 1},
            changes={},
            event_name="REMOVE",
            collections=(ChromaDBCollection.LINES,),
            context=StreamRecordContext(
                timestamp="2025-01-01T00:00:02Z",
                record_id="msg-remove",
                aws_region="us-east-1",
            ),
        )

        # Pass in wrong order
        result = sort_and_deduplicate_messages([insert_msg, remove_msg])

        # REMOVE should come first, INSERT should be dropped (same entity)
        assert len(result) == 1
        assert result[0].event_name == "REMOVE"

    def test_dedup_drops_insert_after_remove_same_entity(self):
        """Test that INSERT is dropped when REMOVE exists for same entity."""
        remove_msg = StreamMessage(
            entity_type="RECEIPT_WORD",
            entity_data={
                "image_id": "img-1",
                "receipt_id": 1,
                "line_id": 1,
                "word_id": 1,
            },
            changes={},
            event_name="REMOVE",
            collections=(ChromaDBCollection.WORDS,),
            context=StreamRecordContext(
                timestamp="2025-01-01T00:00:00Z",
                record_id="msg-remove",
                aws_region="us-east-1",
            ),
        )
        insert_msg = StreamMessage(
            entity_type="RECEIPT_WORD",
            entity_data={
                "image_id": "img-1",
                "receipt_id": 1,
                "line_id": 1,
                "word_id": 1,
            },
            changes={},
            event_name="INSERT",
            collections=(ChromaDBCollection.WORDS,),
            context=StreamRecordContext(
                timestamp="2025-01-01T00:00:01Z",
                record_id="msg-insert",
                aws_region="us-east-1",
            ),
        )

        result = sort_and_deduplicate_messages([insert_msg, remove_msg])

        # Only REMOVE should remain, INSERT dropped
        assert len(result) == 1
        assert result[0].context.record_id == "msg-remove"

    def test_dedup_keeps_insert_for_different_entity(self):
        """Test that INSERT is kept when it's for a different entity."""
        remove_msg = StreamMessage(
            entity_type="RECEIPT_WORD",
            entity_data={
                "image_id": "img-1",
                "receipt_id": 1,
                "line_id": 1,
                "word_id": 1,
            },
            changes={},
            event_name="REMOVE",
            collections=(ChromaDBCollection.WORDS,),
            context=StreamRecordContext(
                timestamp="2025-01-01T00:00:00Z",
                record_id="msg-remove",
                aws_region="us-east-1",
            ),
        )
        insert_msg = StreamMessage(
            entity_type="RECEIPT_WORD",
            entity_data={
                "image_id": "img-1",
                "receipt_id": 1,
                "line_id": 1,
                "word_id": 2,  # Different word_id
            },
            changes={},
            event_name="INSERT",
            collections=(ChromaDBCollection.WORDS,),
            context=StreamRecordContext(
                timestamp="2025-01-01T00:00:01Z",
                record_id="msg-insert",
                aws_region="us-east-1",
            ),
        )

        result = sort_and_deduplicate_messages([insert_msg, remove_msg])

        # Both should remain (different entities)
        assert len(result) == 2
        # REMOVE first
        assert result[0].event_name == "REMOVE"
        assert result[1].event_name == "INSERT"

    def test_multiple_removes_and_inserts(self):
        """Test with mixed batch of REMOVEs and INSERTs."""
        messages = [
            # Entity 1: INSERT then REMOVE
            StreamMessage(
                entity_type="RECEIPT_PLACE",
                entity_data={"image_id": "img-1", "receipt_id": 1},
                changes={},
                event_name="INSERT",
                collections=(ChromaDBCollection.LINES,),
                context=StreamRecordContext(
                    timestamp="2025-01-01T00:00:00Z",
                    record_id="e1-insert",
                    aws_region="us-east-1",
                ),
            ),
            StreamMessage(
                entity_type="RECEIPT_PLACE",
                entity_data={"image_id": "img-1", "receipt_id": 1},
                changes={},
                event_name="REMOVE",
                collections=(ChromaDBCollection.LINES,),
                context=StreamRecordContext(
                    timestamp="2025-01-01T00:00:01Z",
                    record_id="e1-remove",
                    aws_region="us-east-1",
                ),
            ),
            # Entity 2: Only INSERT
            StreamMessage(
                entity_type="RECEIPT_PLACE",
                entity_data={"image_id": "img-2", "receipt_id": 2},
                changes={},
                event_name="INSERT",
                collections=(ChromaDBCollection.LINES,),
                context=StreamRecordContext(
                    timestamp="2025-01-01T00:00:02Z",
                    record_id="e2-insert",
                    aws_region="us-east-1",
                ),
            ),
            # Entity 3: Only REMOVE
            StreamMessage(
                entity_type="RECEIPT_PLACE",
                entity_data={"image_id": "img-3", "receipt_id": 3},
                changes={},
                event_name="REMOVE",
                collections=(ChromaDBCollection.LINES,),
                context=StreamRecordContext(
                    timestamp="2025-01-01T00:00:03Z",
                    record_id="e3-remove",
                    aws_region="us-east-1",
                ),
            ),
        ]

        result = sort_and_deduplicate_messages(messages)

        # Should have: e1-remove, e3-remove, e2-insert
        # (e1-insert dropped because e1 was deleted)
        assert len(result) == 3
        record_ids = [m.context.record_id for m in result]
        # REMOVEs first
        assert record_ids[0] in ("e1-remove", "e3-remove")
        assert record_ids[1] in ("e1-remove", "e3-remove")
        # INSERT last
        assert record_ids[2] == "e2-insert"

    def test_modify_also_dropped_when_entity_deleted(self):
        """Test that MODIFY is also dropped when entity is deleted."""
        remove_msg = StreamMessage(
            entity_type="RECEIPT_PLACE",
            entity_data={"image_id": "img-1", "receipt_id": 1},
            changes={},
            event_name="REMOVE",
            collections=(ChromaDBCollection.LINES,),
            context=StreamRecordContext(
                timestamp="2025-01-01T00:00:00Z",
                record_id="msg-remove",
                aws_region="us-east-1",
            ),
        )
        modify_msg = StreamMessage(
            entity_type="RECEIPT_PLACE",
            entity_data={"image_id": "img-1", "receipt_id": 1},
            changes={"merchant_name": {"old": "Old", "new": "New"}},
            event_name="MODIFY",
            collections=(ChromaDBCollection.LINES,),
            context=StreamRecordContext(
                timestamp="2025-01-01T00:00:01Z",
                record_id="msg-modify",
                aws_region="us-east-1",
            ),
        )

        result = sort_and_deduplicate_messages([modify_msg, remove_msg])

        # Only REMOVE should remain
        assert len(result) == 1
        assert result[0].event_name == "REMOVE"

    def test_preserves_order_within_same_priority(self):
        """Test that timestamp order is preserved within same event type."""
        msg1 = StreamMessage(
            entity_type="RECEIPT_PLACE",
            entity_data={"image_id": "img-1", "receipt_id": 1},
            changes={},
            event_name="REMOVE",
            collections=(ChromaDBCollection.LINES,),
            context=StreamRecordContext(
                timestamp="2025-01-01T00:00:02Z",
                record_id="remove-2",
                aws_region="us-east-1",
            ),
        )
        msg2 = StreamMessage(
            entity_type="RECEIPT_PLACE",
            entity_data={"image_id": "img-2", "receipt_id": 2},
            changes={},
            event_name="REMOVE",
            collections=(ChromaDBCollection.LINES,),
            context=StreamRecordContext(
                timestamp="2025-01-01T00:00:01Z",
                record_id="remove-1",
                aws_region="us-east-1",
            ),
        )

        # Pass in wrong timestamp order
        result = sort_and_deduplicate_messages([msg1, msg2])

        # Should be sorted by timestamp
        assert len(result) == 2
        assert result[0].context.record_id == "remove-1"
        assert result[1].context.record_id == "remove-2"
