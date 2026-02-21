from types import SimpleNamespace

import pytest

from receipt_agent.agents.label_evaluator.financial_structured import (
    _compute_reocr_region,
    _subtotal_mismatch_gap,
)


def _word(line_id: int, word_id: int, x: float):
    return SimpleNamespace(
        line_id=line_id,
        word_id=word_id,
        bounding_box={"x": x, "y": 0.0, "width": 0.05, "height": 0.02},
    )


def _label(
    line_id: int,
    word_id: int,
    *,
    label: str = "LINE_TOTAL",
    status="VALID",
):
    return SimpleNamespace(
        line_id=line_id,
        word_id=word_id,
        label=label,
        validation_status=status,
    )


def test_compute_reocr_region_defaults_when_missing_inputs():
    assert _compute_reocr_region([], []) == {
        "x": 0.70,
        "y": 0.0,
        "width": 0.30,
        "height": 1.0,
    }


def test_compute_reocr_region_uses_only_valid_line_totals():
    words = [
        _word(1, 1, 0.45),
        _word(1, 2, 0.55),
        _word(1, 3, 0.90),  # invalid label status -> ignored
        _word(1, 4, 0.20),  # non-LINE_TOTAL label -> ignored
    ]
    labels = [
        _label(1, 1, label="LINE_TOTAL", status="VALID"),
        _label(
            1,
            2,
            label="LINE_TOTAL",
            status=SimpleNamespace(value="VALID"),  # enum-like status
        ),
        _label(1, 3, label="LINE_TOTAL", status="INVALID"),
        _label(1, 4, label="SUBTOTAL", status="VALID"),
    ]

    region = _compute_reocr_region(words, labels)

    assert region == {
        "x": 0.45,
        "y": 0.0,
        "width": 0.30,
        "height": 1.0,
    }


def test_compute_reocr_region_clamps_to_right_boundary():
    words = [_word(1, 1, 0.95), _word(1, 2, 0.98)]
    labels = [
        _label(1, 1, label="LINE_TOTAL", status="VALID"),
        _label(1, 2, label="LINE_TOTAL", status="VALID"),
    ]

    region = _compute_reocr_region(words, labels)

    assert region["x"] == 0.70
    assert region["width"] == 0.30
    assert region["y"] == 0.0
    assert region["height"] == 1.0


def test_subtotal_mismatch_gap_uses_absolute_difference():
    issues = [
        SimpleNamespace(
            issue_type="SUBTOTAL_MISMATCH",
            expected_value=18.25,
            actual_value=21.10,
        )
    ]
    assert _subtotal_mismatch_gap(issues) == pytest.approx(2.85)
    assert _subtotal_mismatch_gap(list(reversed(issues))) == pytest.approx(2.85)


def test_subtotal_mismatch_gap_returns_zero_when_missing_issue():
    issues = [SimpleNamespace(issue_type="TAX_MISMATCH", expected_value=1, actual_value=2)]
    assert _subtotal_mismatch_gap(issues) == 0.0
