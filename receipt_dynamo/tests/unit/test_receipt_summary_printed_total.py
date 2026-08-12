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
    MonetaryTotals,
    ReceiptSummary,
    _apply_printed_total_fallback,
    find_printed_grand_total,
    find_printed_subtotal,
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


def _label(line_id: int, word_id: int, label: str, status: str) -> ReceiptWordLabel:
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


# ---------------------------------------------------------------------------
# Moody Market a8d7ab9f:4 — tender rows must never anchor the total.
# Real line texts and y-geometry from dev: the receipt prints
# "Subtotal 20.00 / Tax 1.45 / Total 21.45 / Tips 3.22 /
# Total Tender 24.67 / Change 0.00" with labels and amounts as
# separate per-column OCR lines. The old anchor logic accepted
# "Total Tender" as a grand-total row and max() picked 24.67
# (total + tips) over the plain "Total 21.45".
# ---------------------------------------------------------------------------


def _moody_a8d7ab9f_words() -> list[SimpleNamespace]:
    return [
        # Item row: "Big Waygu Beef Bowl" / "20.00".
        _word(19, 1, "Big", 0.5029, x=0.00),
        _word(19, 2, "Waygu", 0.5029, x=0.08),
        _word(19, 3, "Beef", 0.5029, x=0.20),
        _word(19, 4, "Bowl", 0.5029, x=0.30),
        _word(2, 1, "20.00", 0.4999, x=0.89),
        # Summary rows: keyword column and amount column are separate
        # OCR lines paired only by the y-band.
        _word(20, 1, "Subtotal", 0.4163, x=0.00),
        _word(4, 1, "20.00", 0.4171, x=0.89),
        _word(21, 1, "Tax", 0.3888, x=0.00),
        _word(6, 1, "1.45", 0.3905, x=0.91),
        _word(22, 1, "Total", 0.3626, x=0.00),
        _word(8, 1, "21.45", 0.3631, x=0.89),
        _word(23, 1, "Visa", 0.2797, x=0.00),
        _word(23, 2, "...3931", 0.2797, x=0.11),
        _word(10, 1, "21.45", 0.2797, x=0.89),
        _word(24, 1, "Tips", 0.2253, x=0.00),
        _word(12, 1, "3.22", 0.2261, x=0.91),
        _word(25, 1, "Total", 0.1720, x=0.00),
        _word(25, 2, "Tender", 0.1720, x=0.12),
        _word(14, 1, "24.67", 0.1720, x=0.89),
        _word(26, 1, "Change", 0.1439, x=0.00),
        _word(16, 1, "0.00", 0.1439, x=0.91),
    ]


def test_moody_plain_total_outranks_total_tender():
    assert find_printed_grand_total(_moody_a8d7ab9f_words()) == 21.45


def test_moody_tender_rows_alone_anchor_nothing():
    # Only the settlement block: no total row at all -> no anchor.
    words = [
        _word(24, 1, "Tips", 0.2253, x=0.00),
        _word(12, 1, "3.22", 0.2261, x=0.91),
        _word(25, 1, "Total", 0.1720, x=0.00),
        _word(25, 2, "Tender", 0.1720, x=0.12),
        _word(14, 1, "24.67", 0.1720, x=0.89),
        _word(26, 1, "Change", 0.1439, x=0.00),
        _word(16, 1, "0.00", 0.1439, x=0.91),
    ]
    assert find_printed_grand_total(words) is None


def test_amount_tendered_and_cash_total_are_not_anchors():
    words = [
        _word(1, 1, "AMOUNT", 0.5),
        _word(1, 2, "TENDERED", 0.5),
        _word(2, 1, "50.00", 0.5005),
        _word(3, 1, "CASH", 0.45),
        _word(3, 2, "TOTAL", 0.45),
        _word(4, 1, "50.00", 0.4505),
    ]
    assert find_printed_grand_total(words) is None


def test_moody_printed_subtotal_is_anchored():
    # The subtotal anchor pairs with the 20.00 in ITS row band, not the
    # identical 20.00 on the item row (y 0.4999) and not the total.
    assert find_printed_subtotal(_moody_a8d7ab9f_words()) == 20.00


def test_subtotal_anchor_ignores_savings_subtotal():
    words = [
        _word(1, 1, "SUBTOTAL", 0.5),
        _word(1, 2, "SAVINGS", 0.5),
        _word(2, 1, "5.00", 0.5005),
    ]
    assert find_printed_subtotal(words) is None


def test_moody_fallback_fills_grand_total_and_subtotal():
    totals = MonetaryTotals(grand_total=None, subtotal=None, tax=None)
    _apply_printed_total_fallback(totals, _moody_a8d7ab9f_words())
    # 21.45 (not the 24.67 tender row); subtotal anchored at 20.00, so
    # the $20 bowl reconciles against baseline 20.00.
    assert totals.grand_total == 21.45
    assert totals.subtotal == 20.00


# ---------------------------------------------------------------------------
# OCR-mangled currency tokens + TOTAL_LINE extra anchors.
# 383a90d8 r2 prints "Total:" / "USD$S 7.43" (Vision doubled the $ into
# a trailing S). 37c9f558 r1 has a VALID GRAND_TOTAL on the tax figure
# 0.65 while TOTAL_LINE carries 25.85. Bottle-return voids print a
# negative BALANCE DUE and must not invent a positive total.
# ---------------------------------------------------------------------------


def _sprouts_383a90d8_words() -> list[SimpleNamespace]:
    """Card-slip + TOTAL_LINE of dev receipt 383a90d8:2 (grand total 7.43)."""
    return [
        _word(29, 1, "Total:", 0.7950, x=0.020),
        _word(30, 1, "USD$S", 0.7923, x=0.763),
        _word(30, 2, "7.43", 0.7923, x=0.881),
        _word(47, 1, "BALANCE", 0.5075, x=0.125),
        _word(47, 2, "DUE", 0.5078, x=0.314),
        _word(57, 1, "7.43", 0.5065, x=0.787),
        _word(58, 1, "$7.43", 0.4940, x=0.770),
        _word(51, 1, "CHANGE", 0.4580, x=0.125),
        _word(60, 1, "0.00", 0.4564, x=0.796),
    ]


def test_split_usds_prefix_pairs_with_amount_on_same_line():
    assert find_printed_grand_total(_sprouts_383a90d8_words()) == 7.43


def test_glued_usds_token_parses_as_printed_total():
    words = [
        _word(1, 1, "Total:", 0.7950, x=0.020),
        _word(2, 1, "USD$S 7.43", 0.7923, x=0.763),
    ]
    assert find_printed_grand_total(words) == 7.43


def test_total_line_ids_anchor_without_total_keyword():
    # Keyword OCR dropped "Total:"; the section finder still tagged the
    # amount row as TOTAL_LINE. Extra anchors recover the figure.
    words = [
        _word(30, 1, "USD$S", 0.7923, x=0.763),
        _word(30, 2, "7.43", 0.7923, x=0.881),
        _word(43, 1, "CANADIAN", 0.5689, x=0.100),
        _word(44, 1, "$17.99/", 0.5671, x=0.363),
    ]
    assert find_printed_grand_total(words) is None
    assert find_printed_grand_total(words, total_line_ids=[30]) == 7.43


def test_total_line_overrides_grand_total_label_off_section():
    # 37c9f558: VALID GRAND_TOTAL on tax 0.65; TOTAL_LINE is 25.85.
    words = [
        _word(47, 1, "TAX", 0.5237, x=0.062),
        _word(54, 1, "0.65", 0.5228, x=0.800),
        _word(49, 1, "BALANCE", 0.5032, x=0.154),
        _word(49, 2, "DUE", 0.5032, x=0.334),
        _word(55, 1, "25.85", 0.5028, x=0.775),
        _word(56, 1, "$25.85", 0.4921, x=0.754),
    ]
    labels = [_label(54, 1, "GRAND_TOTAL", ValidationStatus.VALID.value)]

    without_section = ReceiptSummary.from_word_labels_and_words(
        image_id=IMAGE_ID,
        receipt_id=1,
        merchant_name=None,
        word_labels=labels,
        words=words,
    )
    assert without_section.grand_total == 0.65

    with_section = ReceiptSummary.from_word_labels_and_words(
        image_id=IMAGE_ID,
        receipt_id=1,
        merchant_name=None,
        word_labels=labels,
        words=words,
        total_line_ids=[49, 55],
    )
    assert with_section.grand_total == 25.85


def test_void_balance_due_does_not_invent_a_positive_total():
    # 8e6faa9a / bd770f76: bottle-return BALANCE DUE is negative; CHANGE
    # restates the cash given back. Do not mint a positive grand total.
    words = [
        _word(12, 1, "BALANCE", 0.5407, x=0.112),
        _word(12, 2, "DUE", 0.5407, x=0.281),
        _word(14, 1, "-6.00", 0.5327, x=0.731),
        _word(13, 1, "CHANGE", 0.5131, x=0.112),
        _word(15, 1, "6.00", 0.5029, x=0.760),
    ]
    assert find_printed_grand_total(words, total_line_ids=[12, 14]) is None
