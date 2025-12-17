"""Tests for embedding record helpers."""

from typing import Any, Dict, List
from unittest.mock import Mock

import pytest
from receipt_chroma.embedding.records import (
    LineEmbeddingRecord,
    WordEmbeddingRecord,
    build_line_payload,
    build_word_payload,
)


def create_mock_line(
    image_id: str,
    receipt_id: int,
    line_id: int,
    text: str,
    confidence: float = 0.9,
    y: float = 0.5,
) -> Mock:
    """Create a mock ReceiptLine object."""
    line = Mock()
    line.image_id = image_id
    line.receipt_id = receipt_id
    line.line_id = line_id
    line.text = text
    line.confidence = confidence
    line.bounding_box = {
        "x": 0.0,
        "y": y,
        "width": 1.0,
        "height": 0.1,
    }
    line.calculate_centroid = Mock(return_value=(0.5, y + 0.05))
    return line


def create_mock_word(
    image_id: str,
    receipt_id: int,
    line_id: int,
    word_id: int,
    text: str,
    confidence: float = 0.9,
    extracted_data: Dict[str, Any] | None = None,
    x: float = 0.0,
    y: float = 0.5,
) -> Mock:
    """Create a mock ReceiptWord object."""
    word = Mock()
    word.image_id = image_id
    word.receipt_id = receipt_id
    word.line_id = line_id
    word.word_id = word_id
    word.text = text
    word.confidence = confidence
    word.extracted_data = extracted_data or {}
    word.bounding_box = {
        "x": x,
        "y": y,
        "width": 0.2,
        "height": 0.1,
    }
    word.calculate_centroid = Mock(return_value=(x + 0.1, y + 0.05))
    return word


class TestLineEmbeddingRecord:
    """Tests for LineEmbeddingRecord dataclass."""

    def test_chroma_id_format(self) -> None:
        """Test that chroma_id is correctly formatted."""
        line = create_mock_line("img123", 42, 7, "Test line")
        record = LineEmbeddingRecord(
            line=line, embedding=[0.1, 0.2, 0.3], batch_id="batch1"
        )

        assert record.chroma_id == "IMAGE#img123#RECEIPT#00042#LINE#00007"

    def test_document_property(self) -> None:
        """Test that document returns line text."""
        line = create_mock_line("img123", 42, 7, "Test line text")
        record = LineEmbeddingRecord(line=line, embedding=[0.1, 0.2, 0.3])

        assert record.document == "Test line text"

    def test_frozen_dataclass(self) -> None:
        """Test that record is immutable."""
        line = create_mock_line("img123", 42, 7, "Test line")
        record = LineEmbeddingRecord(line=line, embedding=[0.1, 0.2, 0.3])

        with pytest.raises(Exception):  # FrozenInstanceError
            record.line = line  # type: ignore[misc]


class TestWordEmbeddingRecord:
    """Tests for WordEmbeddingRecord dataclass."""

    def test_chroma_id_format(self) -> None:
        """Test that chroma_id is correctly formatted."""
        word = create_mock_word("img123", 42, 7, 3, "Test")
        record = WordEmbeddingRecord(
            word=word, embedding=[0.1, 0.2, 0.3], batch_id="batch1"
        )

        assert (
            record.chroma_id
            == "IMAGE#img123#RECEIPT#00042#LINE#00007#WORD#00003"
        )

    def test_document_property(self) -> None:
        """Test that document returns word text."""
        word = create_mock_word("img123", 42, 7, 3, "Hello")
        record = WordEmbeddingRecord(word=word, embedding=[0.1, 0.2, 0.3])

        assert record.document == "Hello"

    def test_frozen_dataclass(self) -> None:
        """Test that record is immutable."""
        word = create_mock_word("img123", 42, 7, 3, "Test")
        record = WordEmbeddingRecord(word=word, embedding=[0.1, 0.2, 0.3])

        with pytest.raises(Exception):  # FrozenInstanceError
            record.word = word  # type: ignore[misc]


class TestBuildLinePayload:
    """Tests for build_line_payload function."""

    def test_basic_payload_structure(self) -> None:
        """Test that payload has correct structure."""
        line1 = create_mock_line("img1", 1, 1, "Line 1", y=0.2)
        line2 = create_mock_line("img1", 1, 2, "Line 2", y=0.6)

        record1 = LineEmbeddingRecord(line=line1, embedding=[0.1, 0.2])
        record2 = LineEmbeddingRecord(line=line2, embedding=[0.3, 0.4])

        payload = build_line_payload(
            records=[record1, record2],
            all_lines=[line1, line2],
            all_words=[],
            merchant_name="Test Merchant",
        )

        assert set(payload.keys()) == {
            "ids",
            "embeddings",
            "documents",
            "metadatas",
        }
        assert len(payload["ids"]) == 2
        assert len(payload["embeddings"]) == 2
        assert len(payload["documents"]) == 2
        assert len(payload["metadatas"]) == 2

    def test_payload_ids_match_chroma_format(self) -> None:
        """Test that IDs are correctly formatted."""
        line = create_mock_line("img1", 5, 10, "Test")
        record = LineEmbeddingRecord(line=line, embedding=[0.1, 0.2])

        payload = build_line_payload(
            records=[record],
            all_lines=[line],
            all_words=[],
        )

        assert payload["ids"][0] == "IMAGE#img1#RECEIPT#00005#LINE#00010"

    def test_payload_with_words_calculates_avg_confidence(self) -> None:
        """Test average confidence calculation from words."""
        line = create_mock_line("img1", 1, 1, "Line 1", confidence=0.5)
        word1 = create_mock_word("img1", 1, 1, 1, "Word1", confidence=0.8)
        word2 = create_mock_word("img1", 1, 1, 2, "Word2", confidence=1.0)

        record = LineEmbeddingRecord(line=line, embedding=[0.1, 0.2])

        payload = build_line_payload(
            records=[record],
            all_lines=[line],
            all_words=[word1, word2],
        )

        # Average of 0.8 and 1.0 should be 0.9
        assert payload["metadatas"][0]["avg_word_confidence"] == 0.9

    def test_payload_without_words_uses_line_confidence(self) -> None:
        """Test that line confidence is used when no words exist."""
        line = create_mock_line("img1", 1, 1, "Line 1", confidence=0.75)
        record = LineEmbeddingRecord(line=line, embedding=[0.1, 0.2])

        payload = build_line_payload(
            records=[record],
            all_lines=[line],
            all_words=[],
        )

        assert payload["metadatas"][0]["avg_word_confidence"] == 0.75


class TestBuildWordPayload:
    """Tests for build_word_payload function."""

    def test_basic_payload_structure(self) -> None:
        """Test that payload has correct structure."""
        word1 = create_mock_word("img1", 1, 1, 1, "Word1")
        word2 = create_mock_word("img1", 1, 1, 2, "Word2")

        record1 = WordEmbeddingRecord(word=word1, embedding=[0.1, 0.2])
        record2 = WordEmbeddingRecord(word=word2, embedding=[0.3, 0.4])

        payload = build_word_payload(
            records=[record1, record2],
            all_words=[word1, word2],
            word_labels=[],
            merchant_name="Test Merchant",
        )

        assert set(payload.keys()) == {
            "ids",
            "embeddings",
            "documents",
            "metadatas",
        }
        assert len(payload["ids"]) == 2
        assert len(payload["embeddings"]) == 2
        assert len(payload["documents"]) == 2
        assert len(payload["metadatas"]) == 2

    def test_payload_ids_match_chroma_format(self) -> None:
        """Test that IDs are correctly formatted."""
        word = create_mock_word("img1", 5, 10, 3, "Test")
        record = WordEmbeddingRecord(word=word, embedding=[0.1, 0.2])

        payload = build_word_payload(
            records=[record],
            all_words=[word],
            word_labels=[],
        )

        assert (
            payload["ids"][0]
            == "IMAGE#img1#RECEIPT#00005#LINE#00010#WORD#00003"
        )

    def test_payload_with_labels(self) -> None:
        """Test that word labels are correctly included in metadata."""
        word = create_mock_word("img1", 1, 1, 1, "Amount")

        label = Mock()
        label.image_id = "img1"
        label.receipt_id = 1
        label.line_id = 1
        label.word_id = 1
        label.label = "total"
        label.validation_status = "PENDING"
        label.label_proposed_by = "model_v1"
        label.confidence = 0.85
        label.timestamp_added = "2024-01-01T00:00:00Z"

        record = WordEmbeddingRecord(word=word, embedding=[0.1, 0.2])

        payload = build_word_payload(
            records=[record],
            all_words=[word],
            word_labels=[label],
        )

        metadata = payload["metadatas"][0]
        assert metadata["label_status"] == "auto_suggested"
        assert metadata["label_proposed_by"] == "model_v1"

    def test_payload_with_custom_context_size(self) -> None:
        """Test that context size parameter works."""
        words: List[Mock] = []
        for i in range(1, 6):
            words.append(
                create_mock_word("img1", 1, 1, i, f"Word{i}", x=i * 0.1)
            )

        record = WordEmbeddingRecord(word=words[2], embedding=[0.1, 0.2])

        payload = build_word_payload(
            records=[record],
            all_words=words,  # type: ignore[arg-type]
            word_labels=[],
            context_size=1,  # Only 1 word on each side
        )

        assert len(payload["ids"]) == 1

    def test_edge_word_context(self) -> None:
        """Test that edge words get <EDGE> tokens."""
        word = create_mock_word("img1", 1, 1, 1, "First")

        record = WordEmbeddingRecord(word=word, embedding=[0.1, 0.2])

        payload = build_word_payload(
            records=[record],
            all_words=[word],
            word_labels=[],
        )

        metadata = payload["metadatas"][0]
        assert metadata["left"] == "<EDGE>"
        assert metadata["right"] == "<EDGE>"
