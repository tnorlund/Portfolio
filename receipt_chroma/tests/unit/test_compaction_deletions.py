"""Unit tests for receipt_chroma.compaction.deletions module."""

from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from receipt_chroma.compaction.deletions import (
    _build_chromadb_id,
    _delete_word_embedding,
    _delete_line_embedding,
    _delete_receipt_embeddings,
    apply_receipt_deletions,
)
from receipt_chroma.compaction.models import ReceiptDeletionResult
from receipt_dynamo.constants import ChromaDBCollection


class MockLogger:
    """Mock logger for testing."""

    def __init__(self):
        self.logs = []

    def info(self, msg, **kwargs):
        self.logs.append(("info", msg, kwargs))

    def warning(self, msg, **kwargs):
        self.logs.append(("warning", msg, kwargs))

    def error(self, msg, **kwargs):
        self.logs.append(("error", msg, kwargs))


class MockMetrics:
    """Mock metrics collector for testing."""

    def __init__(self):
        self.counts = []

    def count(self, name: str, value: int, dimensions: dict = None):
        self.counts.append((name, value, dimensions))


class MockChromaCollection:
    """Mock ChromaDB collection for testing."""

    def __init__(self, ids: list[str] = None):
        self.ids = ids or []
        self.deleted_ids = []

    def get(self, include=None):
        return {"ids": self.ids}

    def delete(self, ids: list[str]):
        self.deleted_ids.extend(ids)


class MockChromaClient:
    """Mock ChromaDB client for testing."""

    def __init__(self, collection: MockChromaCollection = None):
        self._collection = collection or MockChromaCollection()

    def get_collection(self, name: str):
        return self._collection


class MockDynamoClient:
    """Mock DynamoDB client for testing."""

    def __init__(self, words: list = None, lines: list = None):
        self._words = words or []
        self._lines = lines or []

    def list_receipt_words_from_receipt(self, image_id: str, receipt_id: int):
        return self._words

    def list_receipt_lines_from_receipt(self, image_id: str, receipt_id: int):
        return self._lines


class MockReceiptWord:
    """Mock ReceiptWord for testing."""

    def __init__(self, image_id: str, receipt_id: int, line_id: int, word_id: int):
        self.image_id = image_id
        self.receipt_id = receipt_id
        self.line_id = line_id
        self.word_id = word_id


class MockReceiptLine:
    """Mock ReceiptLine for testing."""

    def __init__(self, image_id: str, receipt_id: int, line_id: int):
        self.image_id = image_id
        self.receipt_id = receipt_id
        self.line_id = line_id


class MockStreamMessage:
    """Mock StreamMessage for testing."""

    def __init__(self, entity_type: str, entity_data: dict, event_name: str = "REMOVE"):
        self.entity_type = entity_type
        self.entity_data = entity_data
        self.event_name = event_name


# Tests for _build_chromadb_id


class TestBuildChromaDBId:
    def test_build_id_receipt_only(self):
        """Test building ID with just receipt components."""
        result = _build_chromadb_id("img-123", 1)
        assert result == "IMAGE#img-123#RECEIPT#00001"

    def test_build_id_with_line(self):
        """Test building ID with line component."""
        result = _build_chromadb_id("img-123", 1, line_id=2)
        assert result == "IMAGE#img-123#RECEIPT#00001#LINE#00002"

    def test_build_id_with_word(self):
        """Test building ID with word component."""
        result = _build_chromadb_id("img-123", 1, line_id=2, word_id=3)
        assert result == "IMAGE#img-123#RECEIPT#00001#LINE#00002#WORD#00003"

    def test_build_id_padding(self):
        """Test that IDs are properly zero-padded."""
        result = _build_chromadb_id("img-123", 99, line_id=42, word_id=7)
        assert result == "IMAGE#img-123#RECEIPT#00099#LINE#00042#WORD#00007"


# Tests for _delete_word_embedding


class TestDeleteWordEmbedding:
    def test_delete_word_success(self):
        """Test successful word deletion."""
        collection = MockChromaCollection()
        logger = MockLogger()

        entity_data = {
            "image_id": "img-123",
            "receipt_id": 1,
            "line_id": 2,
            "word_id": 3,
        }

        result = _delete_word_embedding(
            chroma_collection=collection,
            entity_data=entity_data,
            collection=ChromaDBCollection.WORDS,
            logger=logger,
        )

        assert result.deleted_count == 1
        assert result.image_id == "img-123"
        assert result.receipt_id == 1
        assert result.error is None
        assert "IMAGE#img-123#RECEIPT#00001#LINE#00002#WORD#00003" in collection.deleted_ids

    def test_delete_word_wrong_collection(self):
        """Test word deletion skips LINES collection."""
        collection = MockChromaCollection()
        logger = MockLogger()

        entity_data = {
            "image_id": "img-123",
            "receipt_id": 1,
            "line_id": 2,
            "word_id": 3,
        }

        result = _delete_word_embedding(
            chroma_collection=collection,
            entity_data=entity_data,
            collection=ChromaDBCollection.LINES,  # Wrong collection
            logger=logger,
        )

        assert result.deleted_count == 0
        assert collection.deleted_ids == []


# Tests for _delete_line_embedding


class TestDeleteLineEmbedding:
    def test_delete_line_success(self):
        """Test successful line deletion."""
        collection = MockChromaCollection()
        logger = MockLogger()

        entity_data = {
            "image_id": "img-123",
            "receipt_id": 1,
            "line_id": 2,
        }

        result = _delete_line_embedding(
            chroma_collection=collection,
            entity_data=entity_data,
            collection=ChromaDBCollection.LINES,
            logger=logger,
        )

        assert result.deleted_count == 1
        assert result.image_id == "img-123"
        assert result.receipt_id == 1
        assert result.error is None
        assert "IMAGE#img-123#RECEIPT#00001#LINE#00002" in collection.deleted_ids

    def test_delete_line_wrong_collection(self):
        """Test line deletion skips WORDS collection."""
        collection = MockChromaCollection()
        logger = MockLogger()

        entity_data = {
            "image_id": "img-123",
            "receipt_id": 1,
            "line_id": 2,
        }

        result = _delete_line_embedding(
            chroma_collection=collection,
            entity_data=entity_data,
            collection=ChromaDBCollection.WORDS,  # Wrong collection
            logger=logger,
        )

        assert result.deleted_count == 0
        assert collection.deleted_ids == []


# Tests for _delete_receipt_embeddings


class TestDeleteReceiptEmbeddings:
    def test_delete_receipt_words_via_dynamo(self):
        """Test deleting receipt embeddings using DynamoDB lookup."""
        collection = MockChromaCollection()
        logger = MockLogger()

        words = [
            MockReceiptWord("img-123", 1, 1, 1),
            MockReceiptWord("img-123", 1, 1, 2),
            MockReceiptWord("img-123", 1, 2, 1),
        ]
        dynamo_client = MockDynamoClient(words=words)

        result = _delete_receipt_embeddings(
            chroma_collection=collection,
            image_id="img-123",
            receipt_id=1,
            collection=ChromaDBCollection.WORDS,
            logger=logger,
            dynamo_client=dynamo_client,
        )

        assert result.deleted_count == 3
        assert result.error is None
        assert len(collection.deleted_ids) == 3

    def test_delete_receipt_lines_via_dynamo(self):
        """Test deleting receipt lines using DynamoDB lookup."""
        collection = MockChromaCollection()
        logger = MockLogger()

        lines = [
            MockReceiptLine("img-123", 1, 1),
            MockReceiptLine("img-123", 1, 2),
        ]
        dynamo_client = MockDynamoClient(lines=lines)

        result = _delete_receipt_embeddings(
            chroma_collection=collection,
            image_id="img-123",
            receipt_id=1,
            collection=ChromaDBCollection.LINES,
            logger=logger,
            dynamo_client=dynamo_client,
        )

        assert result.deleted_count == 2
        assert result.error is None
        assert len(collection.deleted_ids) == 2

    def test_delete_receipt_fallback_to_prefix_scan(self):
        """Test fallback to prefix scan when DynamoDB returns empty."""
        # Pre-populate ChromaDB with matching IDs
        matching_ids = [
            "IMAGE#img-123#RECEIPT#00001#LINE#00001#WORD#00001",
            "IMAGE#img-123#RECEIPT#00001#LINE#00001#WORD#00002",
        ]
        collection = MockChromaCollection(ids=matching_ids)
        logger = MockLogger()

        # Empty DynamoDB - entities already deleted
        dynamo_client = MockDynamoClient(words=[])

        result = _delete_receipt_embeddings(
            chroma_collection=collection,
            image_id="img-123",
            receipt_id=1,
            collection=ChromaDBCollection.WORDS,
            logger=logger,
            dynamo_client=dynamo_client,
        )

        assert result.deleted_count == 2
        assert result.error is None
        # Check warning was logged
        warnings = [log for log in logger.logs if log[0] == "warning"]
        assert any("prefix scan fallback" in log[1] for log in warnings)

    def test_delete_receipt_no_embeddings_found(self):
        """Test when no embeddings exist for the receipt."""
        collection = MockChromaCollection(ids=[])
        logger = MockLogger()
        dynamo_client = MockDynamoClient(words=[])

        result = _delete_receipt_embeddings(
            chroma_collection=collection,
            image_id="img-123",
            receipt_id=1,
            collection=ChromaDBCollection.WORDS,
            logger=logger,
            dynamo_client=dynamo_client,
        )

        assert result.deleted_count == 0
        assert result.error is None

    def test_delete_receipt_with_metrics(self):
        """Test that metrics are recorded."""
        matching_ids = [
            "IMAGE#img-123#RECEIPT#00001#LINE#00001",
            "IMAGE#img-123#RECEIPT#00001#LINE#00002",
        ]
        collection = MockChromaCollection(ids=matching_ids)
        logger = MockLogger()
        metrics = MockMetrics()
        dynamo_client = MockDynamoClient(lines=[])  # Force prefix scan

        result = _delete_receipt_embeddings(
            chroma_collection=collection,
            image_id="img-123",
            receipt_id=1,
            collection=ChromaDBCollection.LINES,
            logger=logger,
            metrics=metrics,
            dynamo_client=dynamo_client,
        )

        assert result.deleted_count == 2
        # Check prefix scan fallback metric was recorded
        metric_names = [m[0] for m in metrics.counts]
        assert "ReceiptDeletionPrefixScanFallback" in metric_names


# Tests for apply_receipt_deletions


class TestApplyReceiptDeletions:
    def test_empty_messages(self):
        """Test with no messages to process."""
        client = MockChromaClient()
        logger = MockLogger()

        results = apply_receipt_deletions(
            chroma_client=client,
            receipt_messages=[],
            collection=ChromaDBCollection.WORDS,
            logger=logger,
        )

        assert results == []

    def test_receipt_word_deletion(self):
        """Test processing RECEIPT_WORD deletion message."""
        collection = MockChromaCollection()
        client = MockChromaClient(collection)
        logger = MockLogger()

        msg = MockStreamMessage(
            entity_type="RECEIPT_WORD",
            entity_data={
                "entity_type": "RECEIPT_WORD",
                "image_id": "img-123",
                "receipt_id": 1,
                "line_id": 2,
                "word_id": 3,
            },
        )

        results = apply_receipt_deletions(
            chroma_client=client,
            receipt_messages=[msg],
            collection=ChromaDBCollection.WORDS,
            logger=logger,
        )

        assert len(results) == 1
        assert results[0].deleted_count == 1
        assert results[0].error is None

    def test_receipt_line_deletion(self):
        """Test processing RECEIPT_LINE deletion message."""
        collection = MockChromaCollection()
        client = MockChromaClient(collection)
        logger = MockLogger()

        msg = MockStreamMessage(
            entity_type="RECEIPT_LINE",
            entity_data={
                "entity_type": "RECEIPT_LINE",
                "image_id": "img-123",
                "receipt_id": 1,
                "line_id": 2,
            },
        )

        results = apply_receipt_deletions(
            chroma_client=client,
            receipt_messages=[msg],
            collection=ChromaDBCollection.LINES,
            logger=logger,
        )

        assert len(results) == 1
        assert results[0].deleted_count == 1
        assert results[0].error is None

    def test_receipt_deletion_with_dynamo_lookup(self):
        """Test processing RECEIPT deletion with DynamoDB lookup."""
        collection = MockChromaCollection()
        client = MockChromaClient(collection)
        logger = MockLogger()

        words = [
            MockReceiptWord("img-123", 1, 1, 1),
            MockReceiptWord("img-123", 1, 1, 2),
        ]
        dynamo_client = MockDynamoClient(words=words)

        msg = MockStreamMessage(
            entity_type="RECEIPT",
            entity_data={
                "entity_type": "RECEIPT",
                "image_id": "img-123",
                "receipt_id": 1,
            },
        )

        results = apply_receipt_deletions(
            chroma_client=client,
            receipt_messages=[msg],
            collection=ChromaDBCollection.WORDS,
            logger=logger,
            dynamo_client=dynamo_client,
        )

        assert len(results) == 1
        assert results[0].deleted_count == 2
        assert results[0].error is None

    def test_collection_not_found(self):
        """Test when ChromaDB collection is not found."""
        client = MagicMock()
        client.get_collection.return_value = None
        logger = MockLogger()

        msg = MockStreamMessage(
            entity_type="RECEIPT_WORD",
            entity_data={
                "entity_type": "RECEIPT_WORD",
                "image_id": "img-123",
                "receipt_id": 1,
                "line_id": 2,
                "word_id": 3,
            },
        )

        results = apply_receipt_deletions(
            chroma_client=client,
            receipt_messages=[msg],
            collection=ChromaDBCollection.WORDS,
            logger=logger,
        )

        assert len(results) == 1
        assert results[0].deleted_count == 0
        assert "Failed to get ChromaDB collection" in results[0].error

    def test_unknown_entity_type(self):
        """Test with unknown entity type."""
        collection = MockChromaCollection()
        client = MockChromaClient(collection)
        logger = MockLogger()

        msg = MockStreamMessage(
            entity_type="UNKNOWN_TYPE",
            entity_data={
                "entity_type": "UNKNOWN_TYPE",
                "image_id": "img-123",
                "receipt_id": 1,
            },
        )

        results = apply_receipt_deletions(
            chroma_client=client,
            receipt_messages=[msg],
            collection=ChromaDBCollection.WORDS,
            logger=logger,
        )

        # Unknown type should produce no result (not added to results)
        assert len(results) == 0
        # Check warning was logged
        warnings = [log for log in logger.logs if log[0] == "warning"]
        assert any("Unknown entity type" in log[1] for log in warnings)

    def test_multiple_messages(self):
        """Test processing multiple deletion messages."""
        collection = MockChromaCollection()
        client = MockChromaClient(collection)
        logger = MockLogger()

        messages = [
            MockStreamMessage(
                entity_type="RECEIPT_WORD",
                entity_data={
                    "entity_type": "RECEIPT_WORD",
                    "image_id": "img-123",
                    "receipt_id": 1,
                    "line_id": 1,
                    "word_id": 1,
                },
            ),
            MockStreamMessage(
                entity_type="RECEIPT_WORD",
                entity_data={
                    "entity_type": "RECEIPT_WORD",
                    "image_id": "img-123",
                    "receipt_id": 1,
                    "line_id": 1,
                    "word_id": 2,
                },
            ),
            MockStreamMessage(
                entity_type="RECEIPT_WORD",
                entity_data={
                    "entity_type": "RECEIPT_WORD",
                    "image_id": "img-123",
                    "receipt_id": 1,
                    "line_id": 2,
                    "word_id": 1,
                },
            ),
        ]

        results = apply_receipt_deletions(
            chroma_client=client,
            receipt_messages=messages,
            collection=ChromaDBCollection.WORDS,
            logger=logger,
        )

        assert len(results) == 3
        assert all(r.deleted_count == 1 for r in results)
        assert len(collection.deleted_ids) == 3
