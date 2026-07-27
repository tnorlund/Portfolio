"""Tests for semantic dash rules recovered from structural OCR gaps."""

from receipt_agent.agents.label_evaluator.rendering.receipt_grid import (
    GridWord,
)
from receipt_agent.agents.label_evaluator.rendering.receipt_renderer import (
    _structural_gap_separator_centers,
)


def _row(top: float, bottom: float, *texts: str) -> list[GridWord]:
    return [
        GridWord(
            left=index * 80.0,
            top=top,
            right=index * 80.0 + 70.0,
            bottom=bottom,
            text=text,
            ink=(0, 0, 0),
        )
        for index, text in enumerate(texts)
    ]


def test_structural_gap_rules_cover_generic_pos_transitions() -> None:
    rows = [
        _row(10, 20, "00313505101552502031820"),
        _row(50, 60, "YOUR CASHIER TODAY WAS SELF"),
        _row(70, 80, "**** BALANCE", "61.13"),
        _row(110, 120, "Credit Purchase", "02/03/25"),
        _row(130, 140, "PAYMENT AMOUNT", "61.13"),
        _row(170, 180, "AL US DEBIT"),
        _row(190, 200, "Visa Debit", "$63.66"),
        _row(230, 240, "PAYMENT CARD PURCHASE TRANSACTION"),
    ]

    assert _structural_gap_separator_centers(rows, min_gap=20) == [
        35.0,
        95.0,
        155.0,
        215.0,
    ]


def test_structural_gap_rules_require_both_semantics_and_whitespace() -> None:
    rows = [
        _row(10, 20, "**** BALANCE", "61.13"),
        _row(25, 35, "Credit Purchase"),
        _row(70, 80, "LOYALTY BALANCE", "5.00"),
        _row(110, 120, "POINTS SUMMARY"),
    ]

    assert _structural_gap_separator_centers(rows, min_gap=20) == []
