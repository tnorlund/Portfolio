"""Unit tests for line metadata creation."""

from unittest.mock import Mock

import pytest

from receipt_chroma.embedding.metadata.line_metadata import (
    create_line_metadata,
    enrich_line_metadata_with_anchors,
)


class MockReceiptLine:
    """Mock ReceiptLine for testing."""

    def __init__(
        self,
        image_id: str,
        receipt_id: str,
        line_id: str,
        text: str,
        x: float = 0.0,
        y: float = 0.5,
        width: float = 1.0,
        height: float = 0.1,
        confidence: float = 0.9,
    ):
        self.image_id = image_id
        self.receipt_id = receipt_id
        self.line_id = line_id
        self.text = text
        self.bounding_box = {
            "x": x,
            "y": y,
            "width": width,
            "height": height,
        }
        self.confidence = confidence


class MockReceiptWord:
    """Mock ReceiptWord for testing."""

    def __init__(
        self,
        text: str,
        extracted_data: dict | None = None,
    ):
        self.text = text
        self.extracted_data = extracted_data or {}


@pytest.mark.unit
class TestCreateLineMetadata:
    """Test line metadata creation."""

    def test_basic_metadata(self):
        """Test creating basic line metadata."""
        line = MockReceiptLine("img1", "rec1", "line1", "hello world")
        metadata = create_line_metadata(line, "prev", "next")
        assert metadata["image_id"] == "img1"
        assert metadata["receipt_id"] == "rec1"
        assert metadata["line_id"] == "line1"
        assert metadata["text"] == "hello world"
        assert metadata["prev_line"] == "prev"
        assert metadata["next_line"] == "next"
        assert metadata["source"] == "openai_embedding_batch"

    def test_metadata_with_merchant(self):
        """Test metadata with merchant name."""
        line = MockReceiptLine("img1", "rec1", "line1", "hello world")
        metadata = create_line_metadata(
            line, "prev", "next", merchant_name="Test Merchant"
        )
        assert metadata["merchant_name"] == "Test Merchant"

    def test_metadata_merchant_formatting(self):
        """Test merchant name formatting."""
        line = MockReceiptLine("img1", "rec1", "line1", "hello world")
        metadata = create_line_metadata(
            line, "prev", "next", merchant_name="  test merchant  "
        )
        assert metadata["merchant_name"] == "Test Merchant"

    def test_metadata_with_avg_confidence(self):
        """Test metadata with average word confidence."""
        line = MockReceiptLine(
            "img1", "rec1", "line1", "hello world", confidence=0.8
        )
        metadata = create_line_metadata(
            line, "prev", "next", avg_word_confidence=0.95
        )
        assert metadata["avg_word_confidence"] == 0.95
        assert metadata["confidence"] == 0.8  # Line confidence unchanged

    def test_metadata_without_avg_confidence(self):
        """Test metadata without average word confidence uses line confidence."""
        line = MockReceiptLine(
            "img1", "rec1", "line1", "hello world", confidence=0.85
        )
        metadata = create_line_metadata(line, "prev", "next")
        assert metadata["avg_word_confidence"] == 0.85
        assert metadata["confidence"] == 0.85

    def test_metadata_with_section_label(self):
        """Test metadata with section label."""
        line = MockReceiptLine("img1", "rec1", "line1", "hello world")
        metadata = create_line_metadata(
            line, "prev", "next", section_label="header"
        )
        assert metadata["section_label"] == "header"

    def test_metadata_coordinates(self):
        """Test metadata includes coordinates."""
        line = MockReceiptLine("img1", "rec1", "line1", "hello", x=0.1, y=0.2)
        metadata = create_line_metadata(line, "prev", "next")
        assert metadata["x"] == 0.1
        assert metadata["y"] == 0.2
        assert metadata["width"] == 1.0
        assert metadata["height"] == 0.1


@pytest.mark.unit
class TestEnrichLineMetadataWithAnchors:
    """Test enriching line metadata with anchors."""

    def test_enrich_with_phone_anchor(self):
        """Test enriching with phone anchor."""
        word = MockReceiptWord(
            "123-456-7890", {"type": "phone", "value": "123-456-7890"}
        )
        metadata = {"text": "hello"}
        enriched = enrich_line_metadata_with_anchors(metadata, [word])
        assert "normalized_phone_10" in enriched
        assert enriched["normalized_phone_10"] == "1234567890"

    def test_enrich_with_address_anchor(self):
        """Test enriching with address anchor."""
        word = MockReceiptWord(
            "123 Main St", {"type": "address", "value": "123 Main St"}
        )
        metadata = {"text": "hello"}
        enriched = enrich_line_metadata_with_anchors(metadata, [word])
        assert "normalized_full_address" in enriched

    def test_enrich_with_url_anchor(self):
        """Test enriching with URL anchor."""
        word = MockReceiptWord(
            "example.com", {"type": "url", "value": "https://example.com"}
        )
        metadata = {"text": "hello"}
        enriched = enrich_line_metadata_with_anchors(metadata, [word])
        assert "normalized_url" in enriched
        assert enriched["normalized_url"] == "example.com/"

    def test_enrich_with_all_anchors(self):
        """Test enriching with all anchor types."""
        words = [
            MockReceiptWord(
                "123-456-7890", {"type": "phone", "value": "123-456-7890"}
            ),
            MockReceiptWord(
                "123 Main St", {"type": "address", "value": "123 Main St"}
            ),
            MockReceiptWord(
                "example.com", {"type": "url", "value": "https://example.com"}
            ),
        ]
        metadata = {"text": "hello"}
        enriched = enrich_line_metadata_with_anchors(metadata, words)
        assert "normalized_phone_10" in enriched
        assert "normalized_full_address" in enriched
        assert "normalized_url" in enriched

    def test_enrich_no_anchors(self):
        """Test enriching with no anchor words."""
        word = MockReceiptWord("hello", {})
        metadata = {"text": "hello"}
        enriched = enrich_line_metadata_with_anchors(metadata, [word])
        assert "normalized_phone_10" not in enriched
        assert "normalized_full_address" not in enriched
        assert "normalized_url" not in enriched

    def test_enrich_stops_after_all_found(self):
        """Test that enrichment stops after all anchors are found."""
        words = [
            MockReceiptWord(
                "123-456-7890", {"type": "phone", "value": "123-456-7890"}
            ),
            MockReceiptWord(
                "123 Main St", {"type": "address", "value": "123 Main St"}
            ),
            MockReceiptWord(
                "example.com", {"type": "url", "value": "https://example.com"}
            ),
            MockReceiptWord(
                "extra", {"type": "phone", "value": "999-999-9999"}
            ),
        ]
        metadata = {"text": "hello"}
        enriched = enrich_line_metadata_with_anchors(metadata, words)
        # Should only have first phone number
        assert enriched["normalized_phone_10"] == "1234567890"

    def test_enrich_with_exception(self):
        """Test that exceptions are handled gracefully."""
        word = Mock()
        word.extracted_data = None
        metadata = {"text": "hello"}
        # Should not raise exception
        enriched = enrich_line_metadata_with_anchors(metadata, [word])
        assert enriched == metadata
