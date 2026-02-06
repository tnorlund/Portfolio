"""Unit tests for row-level label aggregation in compaction updates."""

from unittest.mock import Mock

import pytest

from receipt_chroma.compaction.labels import _update_row_labels


class MockLabelEntity:
    """Minimal label entity for row-label aggregation tests."""

    def __init__(self, line_id: int, label: str, validation_status: str):
        self.line_id = line_id
        self.label = label
        self.validation_status = validation_status


@pytest.mark.unit
def test_update_row_labels_applies_valid_and_invalid_flags():
    """Row update should persist both positive and negative label booleans."""
    collection = Mock()
    logger = Mock()
    metadata = {
        "row_line_ids": "[1, 2]",
        "label_OLD": True,
        "merchant_name": "Test Merchant",
    }
    receipt_labels = [
        MockLabelEntity(1, "TOTAL", "VALID"),
        MockLabelEntity(2, "TAX", "INVALID"),
        MockLabelEntity(9, "TOTAL", "VALID"),  # Different row, ignored
    ]

    updated_count = _update_row_labels(
        collection=collection,
        chromadb_id="IMAGE#img#RECEIPT#00001#LINE#00001",
        metadata=metadata,
        receipt_labels=receipt_labels,
        logger=logger,
    )

    assert updated_count == 1
    collection.update.assert_called_once()
    update_kwargs = collection.update.call_args.kwargs
    new_metadata = update_kwargs["metadatas"][0]
    assert new_metadata["label_TOTAL"] is True
    assert new_metadata["label_TAX"] is False
    assert new_metadata["label_status"] == "validated"
    assert "label_OLD" not in new_metadata
    assert new_metadata["merchant_name"] == "Test Merchant"


@pytest.mark.unit
def test_update_row_labels_sets_auto_suggested_for_pending_only():
    """Rows with only pending labels should keep no label booleans."""
    collection = Mock()
    logger = Mock()
    metadata = {"row_line_ids": "[1]"}
    receipt_labels = [MockLabelEntity(1, "TOTAL", "PENDING")]

    updated_count = _update_row_labels(
        collection=collection,
        chromadb_id="IMAGE#img#RECEIPT#00001#LINE#00001",
        metadata=metadata,
        receipt_labels=receipt_labels,
        logger=logger,
    )

    assert updated_count == 1
    collection.update.assert_called_once()
    update_kwargs = collection.update.call_args.kwargs
    new_metadata = update_kwargs["metadatas"][0]
    assert new_metadata["label_status"] == "auto_suggested"
    assert "label_TOTAL" not in new_metadata


@pytest.mark.unit
def test_update_row_labels_sets_unvalidated_when_no_labels_match():
    """Rows with no matching labels should be marked unvalidated."""
    collection = Mock()
    logger = Mock()
    metadata = {"row_line_ids": "[1]"}
    # Label for a different line — won't match row
    receipt_labels = [MockLabelEntity(99, "TOTAL", "VALID")]

    updated_count = _update_row_labels(
        collection=collection,
        chromadb_id="IMAGE#img#RECEIPT#00001#LINE#00001",
        metadata=metadata,
        receipt_labels=receipt_labels,
        logger=logger,
    )

    assert updated_count == 1
    collection.update.assert_called_once()
    update_kwargs = collection.update.call_args.kwargs
    new_metadata = update_kwargs["metadatas"][0]
    assert new_metadata["label_status"] == "unvalidated"
    assert not any(
        k.startswith("label_") and k != "label_status" for k in new_metadata
    )
