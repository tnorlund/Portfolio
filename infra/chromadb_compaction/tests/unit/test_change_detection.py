"""
Unit tests for ChromaDB-relevant change detection.

Tests the logic that determines which field changes require ChromaDB updates.
"""

from datetime import datetime

import pytest

from receipt_dynamo.constants import ValidationMethod, ValidationStatus
from receipt_dynamo.entities.receipt_metadata import ReceiptMetadata
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel

from ...lambdas.stream_processor import (
    FieldChange,
    get_chromadb_relevant_changes,
)


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


class TestMetadataChangeDetection:
    """Test metadata field change detection."""

    def test_metadata_changes_detected(self):
        """Test metadata field changes are detected."""
        old_entity = create_test_receipt_metadata(
            merchant_name="Old Merchant",
            canonical_merchant_name="Old Merchant",
            place_id="old_place_123",
            address="Old Address",
        )

        new_entity = create_test_receipt_metadata(
            merchant_name="New Merchant",
            canonical_merchant_name="New Merchant",
            place_id="new_place_123",
            address="Old Address",  # Same address - no change expected
        )

        changes = get_chromadb_relevant_changes(
            "RECEIPT_METADATA", old_entity, new_entity
        )

        assert "canonical_merchant_name" in changes
        assert isinstance(changes["canonical_merchant_name"], FieldChange)
        assert (
            changes["canonical_merchant_name"].old
            == old_entity.canonical_merchant_name
        )
        assert (
            changes["canonical_merchant_name"].new
            == new_entity.canonical_merchant_name
        )
        assert "place_id" in changes
        assert changes["place_id"].old == old_entity.place_id
        assert changes["place_id"].new == new_entity.place_id
        assert "merchant_name" in changes
        assert changes["merchant_name"].old == old_entity.merchant_name
        assert changes["merchant_name"].new == new_entity.merchant_name
        assert (
            "address" not in changes
        )  # No change (both entities have same address)

    def test_no_changes_detected(self):
        """Test when no relevant changes are detected."""
        entity = create_test_receipt_metadata(
            merchant_name="Same Merchant",
            canonical_merchant_name="Same Merchant",
            place_id="same_place_123",
            address="Same Address",
        )

        changes = get_chromadb_relevant_changes(
            "RECEIPT_METADATA", entity, entity  # Same entity
        )
        assert not changes


class TestWordLabelChangeDetection:
    """Test word label change detection."""

    def test_label_changes_detected(self):
        """Test label field changes are detected."""
        old_entity = create_test_receipt_word_label(
            label="OLD_LABEL",
            validation_status=ValidationStatus.PENDING,
            reasoning="Old reasoning",
        )

        new_entity = create_test_receipt_word_label(
            label="NEW_LABEL",
            validation_status=ValidationStatus.VALID,
            reasoning="Old reasoning",  # Same reasoning - no change expected
        )

        changes = get_chromadb_relevant_changes(
            "RECEIPT_WORD_LABEL", old_entity, new_entity
        )

        assert "label" in changes
        assert isinstance(changes["label"], FieldChange)
        assert changes["label"].old == old_entity.label
        assert changes["label"].new == new_entity.label
        assert "validation_status" in changes
        assert changes["validation_status"].old == old_entity.validation_status
        assert changes["validation_status"].new == new_entity.validation_status
        assert (
            "reasoning" not in changes
        )  # No change (both entities have same reasoning)

    def test_no_label_changes(self):
        """Test when no label changes are detected."""
        entity = create_test_receipt_word_label(
            label="TOTAL",
            validation_status=ValidationStatus.VALID,
            reasoning="Same reasoning",
        )

        changes = get_chromadb_relevant_changes(
            "RECEIPT_WORD_LABEL", entity, entity
        )
        assert not changes


class TestUnknownEntityType:
    """Test handling of unknown entity types."""

    def test_unknown_entity_type(self):
        """Test handling of unknown entity types."""
        entity = create_test_receipt_metadata(merchant_name="Test Merchant")

        changes = get_chromadb_relevant_changes("UNKNOWN_TYPE", entity, entity)
        assert not changes


if __name__ == "__main__":
    pytest.main([__file__])

