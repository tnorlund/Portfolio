"""Printed grand-total fallback for ReceiptSummary.

The word/line fixtures reproduce real dev receipts (Sprouts Farmers
Market) whose GRAND_TOTAL labels were attached to garbage words and
rejected by the evaluator, leaving summaries with grand_total None/0.0
even though the total is printed. Sprouts prints summary rows as two
OCR lines in the same visual row: "Total:" / "BALANCE DUE" in one
column and "USD$ 42.54" / "42.54" in the other.
"""

from types import SimpleNamespace

import pytest

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.entities.receipt_summary import (
    ReceiptSummary,
    find_printed_grand_total,
)
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel

pytestmark = pytest.mark.unit

IMAGE_ID = "a0301717-d765-4f34-a15d-48c362ebf9fd"
TIMESTAMP = "2026-01-01T00:00:00.000+00:00"

_LINE_HEIGHT = 0.012


def _word(
    line_id: int,
    word_id: int,
    text: str,
    y_center: float,
    x: float = 0.1,
) -> SimpleNamespace:
    return SimpleNamespace(
        line_id=line_id,
        word_id=word_id,
        text=text,
        bounding_box={
            "x": x,
            "y": y_center - _LINE_HEIGHT / 2,
            "width": 0.1,
            "height": _LINE_HEIGHT,
        },
    )


def _label(
    line_id: int, word_id: int, label: str, status: str
) -> ReceiptWordLabel:
    return ReceiptWordLabel(
        image_id=IMAGE_ID,
        receipt_id=1,
        line_id=line_id,
        word_id=word_id,
        label=label,
        reasoning="test",
        timestamp_added=TIMESTAMP,
        validation_status=status,
    )


def _sprouts_a0301717_words() -> list[SimpleNamespace]:
    """Summary zone of dev receipt a0301717:1 (grand total 42.54)."""
    return [
        # Card-slip row: "USD$ 42.54" and "Total:" are separate lines.
        _word(26, 1, "USD$", 0.2169, x=0.166),
        _word(26, 2, "42.54", 0.2169, x=0.220),
        _word(13, 1, "Total:", 0.2188, x=0.837),
        # Item rows (must never be picked up).
        _word(43, 1, "LARGE", 0.4865, x=0.833),
        _word(43, 2, "GRADE", 0.4865, x=0.870),
        _word(43, 3, "A", 0.4865, x=0.900),
        _word(43, 4, "EGGS", 0.4865, x=0.920),
        _word(49, 1, "6.99", 0.4876, x=0.092),
        # Summary rows, split across per-column OCR lines.
        _word(54, 1, "BALANCE", 0.5659, x=0.688),
        _word(54, 2, "DUE", 0.5659, x=0.740),
        _word(55, 1, "42.54", 0.5665, x=0.092),
        _word(56, 1, "CREDIT", 0.5794, x=0.716),
        _word(57, 1, "$42.54", 0.5800, x=0.091),
        _word(61, 1, "CHANGE", 0.6194, x=0.717),
        _word(62, 1, "0.00", 0.6194, x=0.092),
    ]


def test_finds_balance_due_row_split_across_ocr_lines():
    assert find_printed_grand_total(_sprouts_a0301717_words()) == 42.54


def test_total_keyword_pairs_with_usd_amount_line():
    # Sprouts 0fd6e62e:1 card slip: only "Total:" + "USD$ 47.44" remain.
    words = [
        _word(14, 1, "Total:", 0.2188, x=0.837),
        _word(27, 1, "USD$", 0.2169, x=0.166),
        _word(27, 2, "47.44", 0.2169, x=0.220),
    ]
    assert find_printed_grand_total(words) == 47.44


def test_same_line_amount_wins_without_geometry_pairing():
    words = [
        _word(1, 1, "Total", 0.5),
        _word(1, 2, "$9.54", 0.5),
    ]
    assert find_printed_grand_total(words) == 9.54


def test_item_count_total_row_is_not_an_anchor():
    words = [
        _word(1, 1, "TOTAL", 0.5),
        _word(1, 2, "NUMBER", 0.5),
        _word(1, 3, "OF", 0.5),
        _word(1, 4, "ITEMS", 0.5),
        _word(1, 5, "SOLD", 0.5),
        _word(2, 1, "12.00", 0.5005),
    ]
    assert find_printed_grand_total(words) is None


def test_savings_total_row_is_not_an_anchor():
    words = [
        _word(1, 1, "TOTAL", 0.5),
        _word(1, 2, "SAVINGS", 0.5),
        _word(2, 1, "5.00", 0.5005),
    ]
    assert find_printed_grand_total(words) is None


def test_subtotal_row_is_not_an_anchor():
    words = [
        _word(1, 1, "Sub-Total", 0.5),
        _word(2, 1, "39.90", 0.5005),
    ]
    assert find_printed_grand_total(words) is None


def test_change_row_zero_amount_is_never_a_total():
    words = [
        _word(61, 1, "CHANGE", 0.6194),
        _word(62, 1, "0.00", 0.6194),
    ]
    assert find_printed_grand_total(words) is None


def test_bare_integers_never_qualify_as_amounts():
    # "Store: 220" style rows must not pair with a keyword anchor.
    words = [
        _word(1, 1, "BALANCE", 0.5),
        _word(1, 2, "DUE", 0.5),
        _word(2, 1, "220", 0.5005),
    ]
    assert find_printed_grand_total(words) is None


def test_amount_outside_row_band_is_ignored():
    words = [
        _word(1, 1, "BALANCE", 0.5),
        _word(1, 2, "DUE", 0.5),
        _word(2, 1, "42.54", 0.55),
    ]
    assert find_printed_grand_total(words) is None


def test_summary_fallback_recovers_total_from_rejected_labels():
    # Real failure mode: every GRAND_TOTAL label sits on a garbage word
    # ("Cashier:", "@") and is INVALID, so the label pass yields None.
    words = _sprouts_a0301717_words() + [
        _word(71, 1, "Cashier:", 0.8182, x=0.803),
        _word(37, 1, "@", 0.4071, x=0.808),
    ]
    labels = [
        _label(71, 1, "GRAND_TOTAL", ValidationStatus.INVALID.value),
        _label(37, 1, "GRAND_TOTAL", ValidationStatus.INVALID.value),
    ]

    summary = ReceiptSummary.from_word_labels_and_words(
        image_id=IMAGE_ID,
        receipt_id=1,
        merchant_name="Sprouts Farmers Market",
        word_labels=labels,
        words=words,
    )

    assert summary.grand_total == 42.54


def test_valid_grand_total_label_bypasses_fallback():
    words = [
        _word(22, 1, "$8.49", 0.3),
        _word(1, 1, "Total:", 0.5),
        _word(2, 1, "10.00", 0.5005),
    ]
    labels = [_label(22, 1, "GRAND_TOTAL", ValidationStatus.VALID.value)]

    summary = ReceiptSummary.from_word_labels_and_words(
        image_id=IMAGE_ID,
        receipt_id=1,
        merchant_name=None,
        word_labels=labels,
        words=words,
    )

    assert summary.grand_total == 8.49


def test_fallback_overrides_zero_label_total():
    # A VALID GRAND_TOTAL on a "0.00" word (e.g. the CHANGE row) must not
    # freeze the summary at 0.0 when a real total is printed.
    words = [
        _word(24, 1, "0.00", 0.32),
        _word(1, 1, "BALANCE", 0.5),
        _word(1, 2, "DUE", 0.5),
        _word(2, 1, "8.49", 0.5005),
    ]
    labels = [_label(24, 1, "GRAND_TOTAL", ValidationStatus.VALID.value)]

    summary = ReceiptSummary.from_word_labels_and_words(
        image_id=IMAGE_ID,
        receipt_id=1,
        merchant_name=None,
        word_labels=labels,
        words=words,
    )

    assert summary.grand_total == 8.49
