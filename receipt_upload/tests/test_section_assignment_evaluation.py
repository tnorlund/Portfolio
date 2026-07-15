"""Integrity tests for the measured D2 build and evaluation scripts."""

from types import SimpleNamespace

import pytest

from receipt_dynamo.constants import ValidationStatus
from scripts.build_section_order_priors import build_model
from scripts.evaluate_section_assignment import _score_predictions


def test_missing_predictions_are_scored_as_unassigned_mismatches() -> None:
    score = _score_predictions(
        {1: "ITEMS", 2: "TOTAL_LINE"},
        {1: "ITEMS"},
    )

    assert score["matched"] == 1
    assert score["scored"] == 2
    assert score["unassigned"] == 1
    assert score["per_type_total"] == {
        "ITEMS": 1,
        "TOTAL_LINE": 1,
    }
    assert score["per_type_matched"] == {"ITEMS": 1}


def test_builder_rejects_exclusion_without_valid_section_evidence() -> None:
    section = SimpleNamespace(
        image_id="00000000-0000-4000-8000-000000000001",
        receipt_id=1,
        validation_status=ValidationStatus.VALID.value,
    )

    with pytest.raises(ValueError, match="without QA-VALID section evidence"):
        build_model(
            SimpleNamespace(),
            [section],
            excluded_receipts={("00000000-0000-4000-8000-000000000002", 1)},
        )
