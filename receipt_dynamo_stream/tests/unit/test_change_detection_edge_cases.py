"""Additional edge case tests for change_detection module."""

import dataclasses
from datetime import datetime

import pytest
from receipt_dynamo_stream.change_detection.detector import (
    CHROMADB_RELEVANT_FIELDS,
    get_chromadb_relevant_changes,
)
from receipt_dynamo_stream.models import FieldChange

from receipt_dynamo.entities.receipt_place import ReceiptPlace
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel


def _make_place(**kwargs: object) -> ReceiptPlace:
    """Helper to create ReceiptPlace with custom fields."""
    defaults = {
        "image_id": "550e8400-e29b-41d4-a716-446655440000",
        "receipt_id": 1,
        "place_id": "place123",
        "merchant_name": "Test Merchant",
        "formatted_address": "123 Main St",
        "phone_number": "555-123-4567",
        "matched_fields": ["name"],
        "validated_by": "INFERENCE",
        "timestamp": datetime.fromisoformat("2024-01-01T00:00:00"),
    }
    defaults.update(kwargs)
    return ReceiptPlace(**defaults)  # type: ignore


def _make_word_label(**kwargs: object) -> ReceiptWordLabel:
    """Helper to create ReceiptWordLabel with custom fields."""
    defaults = {
        "image_id": "550e8400-e29b-41d4-a716-446655440000",
        "receipt_id": 1,
        "line_id": 1,
        "word_id": 1,
        "label": "TOTAL",
        "reasoning": "initial",
        "timestamp_added": datetime.fromisoformat("2024-01-01T00:00:00"),
        "validation_status": "NONE",
    }
    defaults.update(kwargs)
    return ReceiptWordLabel(**defaults)  # type: ignore


# Test CHROMADB_RELEVANT_FIELDS constant


def test_chromadb_relevant_fields_contains_expected() -> None:
    """Test that CHROMADB_RELEVANT_FIELDS contains expected fields."""
    assert "RECEIPT_PLACE" in CHROMADB_RELEVANT_FIELDS
    assert "RECEIPT_WORD_LABEL" in CHROMADB_RELEVANT_FIELDS

    place_fields = CHROMADB_RELEVANT_FIELDS["RECEIPT_PLACE"]
    assert "merchant_name" in place_fields
    assert "merchant_category" in place_fields
    assert "formatted_address" in place_fields
    assert "phone_number" in place_fields
    assert "place_id" in place_fields

    word_label_fields = CHROMADB_RELEVANT_FIELDS["RECEIPT_WORD_LABEL"]
    assert "label" in word_label_fields
    assert "reasoning" in word_label_fields
    assert "validation_status" in word_label_fields
    assert "label_proposed_by" in word_label_fields
    assert "label_consolidated_from" in word_label_fields


# Test get_chromadb_relevant_changes edge cases


def test_get_chromadb_relevant_changes_both_none() -> None:
    """Test when both old and new entities are None."""
    changes = get_chromadb_relevant_changes("RECEIPT_PLACE", None, None)
    assert changes == {}


def test_get_chromadb_relevant_changes_old_none() -> None:
    """Test when old entity is None (INSERT case)."""
    new_entity = _make_place(merchant_name="New Merchant")
    changes = get_chromadb_relevant_changes(
        "RECEIPT_PLACE", None, new_entity
    )

    # All relevant fields should be detected as changes
    assert "merchant_name" in changes
    assert changes["merchant_name"].old is None
    assert changes["merchant_name"].new == "New Merchant"


def test_get_chromadb_relevant_changes_new_none() -> None:
    """Test when new entity is None (REMOVE case)."""
    old_entity = _make_place(merchant_name="Old Merchant")
    changes = get_chromadb_relevant_changes(
        "RECEIPT_PLACE", old_entity, None
    )

    # All relevant fields should be detected as changes
    assert "merchant_name" in changes
    assert changes["merchant_name"].old == "Old Merchant"
    assert changes["merchant_name"].new is None


def test_get_chromadb_relevant_changes_no_changes() -> None:
    """Test when entities are identical."""
    entity1 = _make_place()
    entity2 = _make_place()
    changes = get_chromadb_relevant_changes(
        "RECEIPT_PLACE", entity1, entity2
    )
    assert changes == {}


def test_get_chromadb_relevant_changes_multiple_fields() -> None:
    """Test detecting changes in multiple fields."""
    old_entity = _make_place(
        merchant_name="Old Merchant",
        merchant_category="RESTAURANT",
    )
    new_entity = _make_place(
        merchant_name="New Merchant",
        merchant_category="CAFE",
    )

    changes = get_chromadb_relevant_changes(
        "RECEIPT_PLACE", old_entity, new_entity
    )

    assert len(changes) == 2
    assert changes["merchant_name"].old == "Old Merchant"
    assert changes["merchant_name"].new == "New Merchant"
    assert changes["merchant_category"].old == "RESTAURANT"
    assert changes["merchant_category"].new == "CAFE"


def test_get_chromadb_relevant_changes_word_label_all_fields() -> None:
    """Test detecting changes in all word label relevant fields."""
    old_entity = _make_word_label(
        label="TOTAL",
        reasoning="initial reasoning",
        validation_status="NONE",
        label_proposed_by="agent1",
        label_consolidated_from=None,
    )
    new_entity = _make_word_label(
        label="MERCHANT",
        reasoning="updated reasoning",
        validation_status="VALID",
        label_proposed_by="agent2",
        label_consolidated_from="agent1",
    )

    changes = get_chromadb_relevant_changes(
        "RECEIPT_WORD_LABEL", old_entity, new_entity
    )

    assert len(changes) == 5
    assert changes["label"].old == "TOTAL"
    assert changes["label"].new == "MERCHANT"
    assert changes["reasoning"].old == "initial reasoning"
    assert changes["reasoning"].new == "updated reasoning"
    assert changes["validation_status"].old == "NONE"
    assert changes["validation_status"].new == "VALID"


def test_get_chromadb_relevant_changes_unknown_entity_type() -> None:
    """Test with unknown entity type."""
    entity1 = _make_place()
    entity2 = _make_place()
    changes = get_chromadb_relevant_changes("UNKNOWN_TYPE", entity1, entity2)
    assert changes == {}


def test_get_chromadb_relevant_changes_empty_to_value() -> None:
    """Test when a field changes from empty string to a value."""
    old_entity = _make_place(formatted_address="")
    new_entity = _make_place(formatted_address="456 New St")

    changes = get_chromadb_relevant_changes(
        "RECEIPT_PLACE", old_entity, new_entity
    )

    assert "formatted_address" in changes
    assert changes["formatted_address"].old == ""
    assert changes["formatted_address"].new == "456 New St"


def test_get_chromadb_relevant_changes_value_to_empty() -> None:
    """Test when a field changes from a value to empty string."""
    old_entity = _make_place(formatted_address="456 Old St")
    new_entity = _make_place(formatted_address="")

    changes = get_chromadb_relevant_changes(
        "RECEIPT_PLACE", old_entity, new_entity
    )

    assert "formatted_address" in changes
    assert changes["formatted_address"].old == "456 Old St"
    assert changes["formatted_address"].new == ""


def test_get_chromadb_relevant_changes_irrelevant_fields_ignored() -> None:
    """Test that non-relevant field changes are ignored."""
    # These entities differ in timestamp, which is not a relevant field
    old_entity = _make_place(
        timestamp=datetime.fromisoformat("2024-01-01T00:00:00")
    )
    new_entity = _make_place(
        timestamp=datetime.fromisoformat("2024-01-02T00:00:00")
    )

    changes = get_chromadb_relevant_changes(
        "RECEIPT_PLACE", old_entity, new_entity
    )

    # timestamp is not in CHROMADB_RELEVANT_FIELDS
    assert changes == {}


def test_get_chromadb_relevant_changes_field_change_immutability() -> None:
    """Test that FieldChange objects are immutable."""
    old_entity = _make_place(merchant_name="Old")
    new_entity = _make_place(merchant_name="New")

    changes = get_chromadb_relevant_changes(
        "RECEIPT_PLACE", old_entity, new_entity
    )

    field_change = changes["merchant_name"]
    with pytest.raises(dataclasses.FrozenInstanceError):
        field_change.old = "Modified"  # type: ignore
