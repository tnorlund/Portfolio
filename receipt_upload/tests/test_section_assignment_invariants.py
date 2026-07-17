"""Synthetic invariants for the semi-Markov section decoder."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from receipt_dynamo.entities import ReceiptRow

import receipt_upload.section_assignment as assignment
from receipt_upload.section_assignment import (
    RowAssignment,
    RowFeatures,
    sections_from_assignments,
)

_IMAGE_ID = "00000000-0000-4000-8000-000000000001"


def _feature(
    row_id: int,
    *,
    position: float,
    tokens: tuple[str, ...],
    has_amount: float = 0.0,
) -> RowFeatures:
    row = ReceiptRow(
        image_id=_IMAGE_ID,
        receipt_id=1,
        row_id=row_id,
        line_ids=[row_id],
        grouping_version="visual-rows-v1",
        y_min=0.0,
        y_max=0.01,
        x_min=0.0,
        x_max=0.8,
        created_at=datetime(2026, 7, 17, tzinfo=timezone.utc),
    )
    return RowFeatures(
        row=row,
        position=position,
        x_span=0.8,
        alpha_ratio=0.8,
        has_amount=has_amount,
        amount_density=has_amount,
        has_quantity=0.0,
        tokens=tokens,
    )


def _model(sections: list[str]) -> dict[str, Any]:
    return {
        "global": {
            "sections": sections,
            "section_models": {
                section: {
                    "features": {
                        "position": {"mean": float(index), "std": 1.0}
                    }
                }
                for index, section in enumerate(sections)
            },
            "transitions": {},
        },
        "merchants": {},
    }


def _patch_scores(
    monkeypatch: pytest.MonkeyPatch,
    scores: dict[int, dict[str, float]],
) -> None:
    monkeypatch.setattr(
        assignment,
        "_emission",
        lambda features, section, _model, _fallback: scores[
            features.row.row_id
        ][section],
    )
    monkeypatch.setattr(
        assignment,
        "_duration_score",
        lambda _prior, _global, _section, _duration: 0.0,
    )
    monkeypatch.setattr(
        assignment,
        "_transition",
        lambda _prior, _global, source, destination: (
            1.0 if source == "<START>" or destination == "<END>" else 1e-6
        ),
    )


def test_semantic_constraint_participates_in_neighboring_path_choice(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A constrained row must be part of the path optimized by the DP."""

    features = [
        _feature(1, position=0.0, tokens=("apple",)),
        _feature(2, position=0.2, tokens=("subtotal",)),
        _feature(3, position=0.4, tokens=("banana",)),
    ]
    _patch_scores(
        monkeypatch,
        {
            1: {"ITEMS": 5.0, "SUMMARY": 4.0},
            2: {"ITEMS": 10.0, "SUMMARY": 0.0},
            3: {"ITEMS": 5.0, "SUMMARY": 4.0},
        },
    )

    result = assignment.assign_feature_sections(
        features, _model(["ITEMS", "SUMMARY"])
    )

    assert [item.section_type for item in result] == [
        "SUMMARY",
        "SUMMARY",
        "SUMMARY",
    ]
    assert result[1].confidence < 0.001


def test_local_emission_does_not_mutate_the_decoded_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A local winner must not bypass transition and duration evidence."""

    features = [
        _feature(1, position=0.0, tokens=("first",), has_amount=1.0),
        _feature(2, position=0.2, tokens=("apple",), has_amount=1.0),
        _feature(3, position=0.4, tokens=("last",), has_amount=1.0),
    ]
    _patch_scores(
        monkeypatch,
        {
            1: {"ITEMS": 0.0, "PAYMENT": 10.0},
            2: {"ITEMS": 7.0, "PAYMENT": 5.0},
            3: {"ITEMS": 0.0, "PAYMENT": 10.0},
        },
    )

    result = assignment.assign_feature_sections(
        features, _model(["ITEMS", "PAYMENT"])
    )

    assert [item.section_type for item in result] == [
        "PAYMENT",
        "PAYMENT",
        "PAYMENT",
    ]
    assert result[1].confidence < 0.2


def test_section_confidence_is_not_a_path_coherence_measure() -> None:
    """Aggregation can combine disjoint runs and only averages row scores."""

    first = _feature(1, position=0.0, tokens=("first",))
    middle = _feature(2, position=0.2, tokens=("middle",))
    last = _feature(3, position=0.4, tokens=("last",))
    sections = sections_from_assignments(
        [
            RowAssignment(first.row, "SUMMARY", 0.99),
            RowAssignment(middle.row, "ITEMS", 0.8),
            RowAssignment(last.row, "SUMMARY", 0.01),
        ]
    )

    summary = next(
        section for section in sections if section.section_type == "SUMMARY"
    )
    assert summary.row_ids == [1, 3]
    assert summary.confidence == pytest.approx(0.5)


@pytest.mark.parametrize(
    ("tokens", "has_amount", "expected"),
    [
        (("subtotal", "__amount__"), 1.0, "SUMMARY"),
        (("fuel", "points", "earned"), 0.0, "FOOTER"),
        (("visa", "__amount__"), 1.0, "PAYMENT"),
        (("fresh", "value"), 0.0, None),
    ],
)
def test_semantic_constraints_are_generic_and_merchant_independent(
    tokens: tuple[str, ...], has_amount: float, expected: str | None
) -> None:
    """Only generic receipt semantics may create compatibility constraints."""

    feature = _feature(1, position=0.1, tokens=tokens, has_amount=has_amount)

    # pylint: disable-next=protected-access
    assert assignment._semantic_constraint(feature) == expected
