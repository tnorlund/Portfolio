"""Unit tests for word metadata creation."""

from unittest.mock import Mock

import pytest
from receipt_chroma.embedding.metadata.word_metadata import (
    create_word_metadata,
    enrich_word_metadata_with_anchors,
    enrich_word_metadata_with_labels,
)

from receipt_dynamo.constants import ValidationStatus


class MockReceiptWord:
    """Mock ReceiptWord for testing."""

    def __init__(
        self,
        image_id: str,
        receipt_id: str,
        line_id: str,
        word_id: str,
        text: str,
        x: float = 0.5,
        y: float = 0.5,
        width: float = 0.1,
        height: float = 0.05,
        confidence: float = 0.9,
    ):
        self.image_id = image_id
        self.receipt_id = receipt_id
        self.line_id = line_id
        self.word_id = word_id
        self.text = text
        self.bounding_box = {
            "x": x,
            "y": y,
            "width": width,
            "height": height,
        }
        self.confidence = confidence

    def calculate_centroid(self) -> tuple[float, float]:
        """Calculate centroid coordinates."""
        x_center = self.bounding_box["x"] + self.bounding_box["width"] / 2
        y_center = self.bounding_box["y"] + self.bounding_box["height"] / 2
        return (x_center, y_center)


class MockReceiptWordLabel:
    """Mock ReceiptWordLabel for testing."""

    def __init__(
        self,
        validation_status: str = ValidationStatus.NONE.value,
        label: str = "merchant",
        label_type: str = "merchant",
        label_value: str = "Test Merchant",
        timestamp_added: str = "2024-01-01T00:00:00Z",
        label_proposed_by: str | None = None,
        confidence: float | None = None,
    ):
        self.validation_status = validation_status
        self.label = label
        self.label_type = label_type
        self.label_value = label_value
        self.timestamp_added = timestamp_added
        self.label_proposed_by = label_proposed_by
        self.confidence = confidence


@pytest.mark.unit
class TestCreateWordMetadata:
    """Test word metadata creation."""

    def test_basic_metadata(self):
        """Test creating basic word metadata."""
        word = MockReceiptWord("img1", "rec1", "line1", "word1", "hello")
        metadata = create_word_metadata(word, "left", "right")
        assert metadata["image_id"] == "img1"
        assert metadata["receipt_id"] == "rec1"
        assert metadata["line_id"] == "line1"
        assert metadata["word_id"] == "word1"
        assert metadata["text"] == "hello"
        assert metadata["left"] == "left"
        assert metadata["right"] == "right"
        assert metadata["source"] == "openai_embedding_batch"

    def test_metadata_with_merchant(self):
        """Test metadata with merchant name."""
        word = MockReceiptWord("img1", "rec1", "line1", "word1", "hello")
        metadata = create_word_metadata(
            word, "left", "right", merchant_name="Test Merchant"
        )
        assert metadata["merchant_name"] == "Test Merchant"

    def test_metadata_merchant_formatting(self):
        """Test merchant name formatting."""
        word = MockReceiptWord("img1", "rec1", "line1", "word1", "hello")
        metadata = create_word_metadata(
            word, "left", "right", merchant_name="  test merchant  "
        )
        assert metadata["merchant_name"] == "Test Merchant"

    def test_metadata_with_label_status(self):
        """Test metadata with label status."""
        word = MockReceiptWord("img1", "rec1", "line1", "word1", "hello")
        metadata = create_word_metadata(word, "left", "right", label_status="validated")
        assert metadata["label_status"] == "validated"

    def test_metadata_coordinates(self):
        """Test metadata includes coordinates."""
        word = MockReceiptWord("img1", "rec1", "line1", "word1", "hello", x=0.1, y=0.2)
        metadata = create_word_metadata(word, "left", "right")
        assert abs(metadata["x"] - 0.15) < 0.001  # x + width/2 (allow float precision)
        assert (
            abs(metadata["y"] - 0.225) < 0.001
        )  # y + height/2 (allow float precision)
        assert metadata["width"] == 0.1
        assert metadata["height"] == 0.05

    def test_metadata_confidence(self):
        """Test metadata includes confidence."""
        word = MockReceiptWord(
            "img1", "rec1", "line1", "word1", "hello", confidence=0.95
        )
        metadata = create_word_metadata(word, "left", "right")
        assert metadata["confidence"] == 0.95


@pytest.mark.unit
class TestEnrichWordMetadataWithLabels:
    """Test enriching word metadata with labels."""

    def test_enrich_with_validated_label(self):
        """Test enriching with validated label."""
        metadata = {"text": "hello"}
        labels = [MockReceiptWordLabel(ValidationStatus.VALID.value)]
        enriched = enrich_word_metadata_with_labels(metadata, labels)
        assert enriched["label_status"] == "validated"

    def test_enrich_with_pending_label(self):
        """Test enriching with pending label."""
        metadata = {"text": "hello"}
        labels = [MockReceiptWordLabel(ValidationStatus.PENDING.value)]
        enriched = enrich_word_metadata_with_labels(metadata, labels)
        assert enriched["label_status"] == "auto_suggested"

    def test_enrich_with_unvalidated_label(self):
        """Test enriching with unvalidated label."""
        metadata = {"text": "hello"}
        labels = [MockReceiptWordLabel(ValidationStatus.NONE.value)]
        enriched = enrich_word_metadata_with_labels(metadata, labels)
        assert enriched["label_status"] == "unvalidated"

    def test_enrich_with_multiple_labels(self):
        """Test enriching with multiple labels."""
        metadata = {"text": "hello"}
        labels = [
            MockReceiptWordLabel(ValidationStatus.NONE.value),
            MockReceiptWordLabel(ValidationStatus.VALID.value),
        ]
        enriched = enrich_word_metadata_with_labels(metadata, labels)
        # Should prioritize validated
        assert enriched["label_status"] == "validated"

    def test_enrich_empty_labels(self):
        """Test enriching with empty labels."""
        metadata = {"text": "hello", "label_status": "existing"}
        enriched = enrich_word_metadata_with_labels(metadata, [])
        assert enriched["label_status"] == "unvalidated"


@pytest.mark.unit
class TestEnrichWordMetadataWithAnchors:
    """Test enriching word metadata with anchors."""

    def test_enrich_with_phone_anchor(self):
        """Test enriching with phone anchor."""
        word = MockReceiptWord("img1", "rec1", "line1", "word1", "1234567890")
        word.extracted_data = {"type": "phone", "value": "123-456-7890"}
        metadata = {"text": "hello"}
        enriched = enrich_word_metadata_with_anchors(metadata, word)
        assert "normalized_phone_10" in enriched
        assert enriched["normalized_phone_10"] == "1234567890"

    def test_enrich_with_address_anchor(self):
        """Test enriching with address anchor."""
        word = MockReceiptWord("img1", "rec1", "line1", "word1", "123 Main St")
        word.extracted_data = {"type": "address", "value": "123 Main St"}
        metadata = {"text": "hello"}
        enriched = enrich_word_metadata_with_anchors(metadata, word)
        assert "normalized_full_address" in enriched

    def test_enrich_with_url_anchor(self):
        """Test enriching with URL anchor."""
        word = MockReceiptWord("img1", "rec1", "line1", "word1", "example.com")
        word.extracted_data = {"type": "url", "value": "https://example.com"}
        metadata = {"text": "hello"}
        enriched = enrich_word_metadata_with_anchors(metadata, word)
        assert "normalized_url" in enriched
        assert enriched["normalized_url"] == "example.com/"

    def test_enrich_with_phone_anchor_single(self):
        """Test enriching with phone anchor (single word)."""
        word = MockReceiptWord("img1", "rec1", "line1", "word1", "123-456-7890")
        word.extracted_data = {"type": "phone", "value": "123-456-7890"}
        metadata = {"text": "hello"}
        enriched = enrich_word_metadata_with_anchors(metadata, word)
        assert "normalized_phone_10" in enriched
        assert enriched["normalized_phone_10"] == "1234567890"

    def test_enrich_no_anchors(self):
        """Test enriching with no anchor words."""
        word = MockReceiptWord("img1", "rec1", "line1", "word1", "hello")
        word.extracted_data = {}
        metadata = {"text": "hello"}
        enriched = enrich_word_metadata_with_anchors(metadata, word)
        assert "normalized_phone_10" not in enriched
        assert "normalized_full_address" not in enriched
        assert "normalized_url" not in enriched

    def test_enrich_with_exception(self):
        """Test that exceptions are handled gracefully."""
        word = Mock()
        word.extracted_data = None
        metadata = {"text": "hello"}
        # Should not raise exception
        enriched = enrich_word_metadata_with_anchors(metadata, word)
        assert enriched == metadata
