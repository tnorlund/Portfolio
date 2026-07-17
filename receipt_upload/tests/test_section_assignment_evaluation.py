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
    assert score["per_type_predicted"] == {"ITEMS": 1}
    assert score["confusion"] == {
        ("ITEMS", "ITEMS"): 1,
        ("TOTAL_LINE", "__UNASSIGNED__"): 1,
    }


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


class _Feature:
    row = SimpleNamespace(line_ids=[1])
    tokens = ("apple", "__amount__")

    @staticmethod
    def numeric() -> dict[str, float]:
        return {
            "position": 0.5,
            "x_span": 0.75,
            "alpha_ratio": 0.8,
            "amount_density": 1.0,
        }

    @staticmethod
    def binary() -> dict[str, float]:
        return {"has_amount": 1.0, "has_quantity": 0.0}


class _Client:
    def __init__(self, merchant_name: str = "Example Store") -> None:
        self.merchant_name = merchant_name

    @staticmethod
    def list_receipt_lines_from_receipt(
        _image_id: str, _receipt_id: int
    ) -> list[SimpleNamespace]:
        return [SimpleNamespace(line_id=1)]

    @staticmethod
    def list_receipt_words_from_receipt(
        _image_id: str, _receipt_id: int
    ) -> list[SimpleNamespace]:
        return []

    @staticmethod
    def get_receipt_rows_from_receipt(
        _image_id: str, _receipt_id: int
    ) -> list[SimpleNamespace]:
        return [SimpleNamespace(line_ids=[1])]

    def get_receipt_place(
        self, _image_id: str, _receipt_id: int
    ) -> SimpleNamespace:
        return SimpleNamespace(merchant_name=self.merchant_name)


def test_builder_is_deterministic_and_hashes_complete_training_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    section = SimpleNamespace(
        image_id="00000000-0000-4000-8000-000000000001",
        receipt_id=1,
        section_type="ITEMS",
        line_ids=[1],
        validation_status=ValidationStatus.VALID.value,
    )
    monkeypatch.setattr(
        "scripts.build_section_order_priors.extract_row_features",
        lambda _rows, _lines: [_Feature()],
    )
    monkeypatch.setattr(
        "scripts.build_section_order_priors.build_receipt_rows",
        lambda _lines, _words: [SimpleNamespace(line_ids=[1])],
    )

    first = build_model(_Client(), [section])
    second = build_model(_Client(), [section])
    changed_merchant = build_model(_Client("Different Store"), [section])

    assert first == second
    assert "generated_at" not in first
    assert first["source"]["training_corpus_sha256"]
    assert (
        first["source"]["training_corpus_sha256"]
        != changed_merchant["source"]["training_corpus_sha256"]
    )
