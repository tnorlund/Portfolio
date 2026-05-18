from types import SimpleNamespace

import pytest

from receipt_agent.agents.label_evaluator.financial_structured import (
    _subtotal_mismatch_gap,
)


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
