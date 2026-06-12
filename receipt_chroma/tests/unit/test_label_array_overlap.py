"""Unit tests for label array overlap prevention in operations.py.

Verifies that _set_label_array_fields always writes both keys so that
dict.update() callers overwrite stale values from existing ChromaDB metadata.

Also verifies that VALID→INVALID transitions are handled correctly in the
incremental update path.
"""

import pytest

from receipt_chroma.data.operations import (
    _labels_from_array_metadata,
    _normalize_labels,
    _set_label_array_fields,
)


class TestSetLabelArrayFields:
    """Test _set_label_array_fields always writes both keys."""

    def test_both_populated(self):
        metadata = {}
        _set_label_array_fields(
            metadata,
            valid_labels={"TOTAL"},
            invalid_labels={"TAX"},
        )
        assert metadata["valid_labels_array"] == ["TOTAL"]
        assert metadata["invalid_labels_array"] == ["TAX"]

    def test_only_valid(self):
        metadata = {}
        _set_label_array_fields(
            metadata,
            valid_labels={"TOTAL"},
            invalid_labels=set(),
        )
        assert metadata["valid_labels_array"] == ["TOTAL"]
        assert metadata["invalid_labels_array"] is None

    def test_only_invalid(self):
        metadata = {}
        _set_label_array_fields(
            metadata,
            valid_labels=set(),
            invalid_labels={"TAX"},
        )
        assert metadata["valid_labels_array"] is None
        assert metadata["invalid_labels_array"] == ["TAX"]

    def test_both_empty(self):
        metadata = {}
        _set_label_array_fields(
            metadata,
            valid_labels=set(),
            invalid_labels=set(),
        )
        assert metadata["valid_labels_array"] is None
        assert metadata["invalid_labels_array"] is None

    def test_always_writes_keys_on_fresh_dict(self):
        """Regression: _set_label_array_fields must write both keys even on a
        fresh dict so that dict.update() will overwrite stale ChromaDB values.
        """
        fresh = {"label_status": "validated"}
        _set_label_array_fields(
            fresh,
            valid_labels=set(),
            invalid_labels={"TAX"},
        )
        # Both keys must be present so dict.update() overwrites stale values
        assert "valid_labels_array" in fresh
        assert "invalid_labels_array" in fresh
        assert fresh["valid_labels_array"] is None
        assert fresh["invalid_labels_array"] == ["TAX"]

    def test_stale_valid_array_overwritten_by_update(self):
        """Regression: simulates the exact bug where a stale valid_labels_array
        persisted through dict.update() because the key was missing from the
        reconstructed metadata."""
        # Existing ChromaDB metadata with a VALID label
        existing = {
            "text": "Total",
            "label_status": "validated",
            "valid_labels_array": ["TOTAL"],
            "invalid_labels_array": None,
        }
        updated = existing.copy()

        # Reconstructed metadata (label moved from VALID to INVALID in DynamoDB)
        reconstructed = {"label_status": "invalidated"}
        _set_label_array_fields(
            reconstructed,
            valid_labels=set(),
            invalid_labels={"TOTAL"},
        )

        # Simulate line 678: updated_metadata.update(reconstructed_metadata)
        updated.update(reconstructed)

        # The stale valid_labels_array=["TOTAL"] MUST be overwritten
        assert updated["valid_labels_array"] is None
        assert updated["invalid_labels_array"] == ["TOTAL"]

    def test_stale_invalid_array_overwritten_by_update(self):
        """Mirror case: stale invalid_labels_array must be overwritten."""
        existing = {
            "text": "$5.99",
            "label_status": "invalidated",
            "valid_labels_array": None,
            "invalid_labels_array": ["LINE_TOTAL"],
        }
        updated = existing.copy()

        # Label moved from INVALID to VALID in DynamoDB
        reconstructed = {"label_status": "validated"}
        _set_label_array_fields(
            reconstructed,
            valid_labels={"LINE_TOTAL"},
            invalid_labels=set(),
        )

        updated.update(reconstructed)

        assert updated["valid_labels_array"] == ["LINE_TOTAL"]
        assert updated["invalid_labels_array"] is None


class TestIncrementalLabelTransitions:
    """Test that incremental label updates handle all transitions correctly."""

    def _apply_incremental_update(
        self, existing_valid, existing_invalid, label, status
    ):
        """Simulate the incremental update path from operations.py:654-675.

        TODO: Replace with a call to the real ``update_word_labels``
        once a lightweight test harness for ChromaDB collections exists.
        """
        metadata = {
            "valid_labels_array": (
                list(existing_valid) if existing_valid else None
            ),
            "invalid_labels_array": (
                list(existing_invalid) if existing_invalid else None
            ),
        }
        valid_set = _labels_from_array_metadata(
            metadata, array_field="valid_labels_array"
        )
        invalid_set = _labels_from_array_metadata(
            metadata, array_field="invalid_labels_array"
        )

        normalized = label.strip().upper()
        if status == "VALID":
            valid_set.add(normalized)
            invalid_set.discard(normalized)
        elif status == "INVALID":
            invalid_set.add(normalized)
            valid_set.discard(normalized)
        elif status == "PENDING":
            valid_set.discard(normalized)
            invalid_set.discard(normalized)

        _set_label_array_fields(metadata, valid_set, invalid_set)
        return metadata

    def test_pending_to_valid(self):
        result = self._apply_incremental_update(
            existing_valid=set(),
            existing_invalid=set(),
            label="TOTAL",
            status="VALID",
        )
        assert result["valid_labels_array"] == ["TOTAL"]
        assert result["invalid_labels_array"] is None

    def test_pending_to_invalid(self):
        result = self._apply_incremental_update(
            existing_valid=set(),
            existing_invalid=set(),
            label="TOTAL",
            status="INVALID",
        )
        assert result["valid_labels_array"] is None
        assert result["invalid_labels_array"] == ["TOTAL"]

    def test_valid_to_invalid(self):
        """Regression: VALID→INVALID transitions must move the label."""
        result = self._apply_incremental_update(
            existing_valid={"TOTAL"},
            existing_invalid=set(),
            label="TOTAL",
            status="INVALID",
        )
        assert result["valid_labels_array"] is None
        assert result["invalid_labels_array"] == ["TOTAL"]

    def test_invalid_to_valid(self):
        result = self._apply_incremental_update(
            existing_valid=set(),
            existing_invalid={"TOTAL"},
            label="TOTAL",
            status="VALID",
        )
        assert result["valid_labels_array"] == ["TOTAL"]
        assert result["invalid_labels_array"] is None

    def test_valid_to_pending(self):
        result = self._apply_incremental_update(
            existing_valid={"TOTAL"},
            existing_invalid=set(),
            label="TOTAL",
            status="PENDING",
        )
        assert result["valid_labels_array"] is None
        assert result["invalid_labels_array"] is None

    def test_no_overlap_after_any_transition(self):
        """Exhaustive: no transition should produce overlapping arrays.

        Only tests starting states where valid and invalid don't already
        overlap, since the reconstruction path fix prevents those states.
        """
        labels = ["TOTAL", "TAX"]
        statuses = ["VALID", "INVALID", "PENDING"]

        # Only test non-overlapping starting states (valid starting conditions)
        starting_states = [
            (set(), set()),
            ({"TOTAL"}, set()),
            ({"TAX"}, set()),
            ({"TOTAL", "TAX"}, set()),
            (set(), {"TOTAL"}),
            (set(), {"TAX"}),
            ({"TOTAL"}, {"TAX"}),
            ({"TAX"}, {"TOTAL"}),
        ]

        for existing_v, existing_i in starting_states:
            for label in labels:
                for status in statuses:
                    result = self._apply_incremental_update(
                        existing_v, existing_i, label, status
                    )
                    valid = set(result.get("valid_labels_array") or [])
                    invalid = set(result.get("invalid_labels_array") or [])
                    overlap = valid & invalid
                    assert not overlap, (
                        f"Overlap {overlap} after applying "
                        f"{label}→{status} to "
                        f"valid={existing_v}, invalid={existing_i}"
                    )
