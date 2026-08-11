"""Trailing-minus (accounting negative) totals in ReceiptSummary.

Target return receipts print refunds with a trailing minus -- dev
receipt d30ba860-4bd6-4c9e-a6d7-c2eaed0c2149 (receipt 1) prints
"$16.25-" / "$14.99-" / "$1.26-" with correct VALID labels -- yet the
old extract_amount dropped the sign (parsing +16.25) and the printed
fallback treated any <= 0 label total as "missing", clobbering it with
whatever positive figure sat in the total row's y-band. These tests pin
the fixed behavior: signs carry through, the refund reconciles
(subtotal + tax == grand_total), and the fallback leaves negative
label-derived totals alone.
"""

from types import SimpleNamespace

import pytest

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.entities.receipt_summary import (
    MonetaryTotals,
    ReceiptSummary,
    _apply_printed_total_fallback,
)
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel

pytestmark = pytest.mark.unit

IMAGE_ID = "d30ba860-4bd6-4c9e-a6d7-c2eaed0c2149"
TIMESTAMP = "2026-08-10T00:00:00.000+00:00"

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


def _label(line_id: int, word_id: int, label: str) -> ReceiptWordLabel:
    return ReceiptWordLabel(
        image_id=IMAGE_ID,
        receipt_id=1,
        line_id=line_id,
        word_id=word_id,
        label=label,
        reasoning="test",
        timestamp_added=TIMESTAMP,
        validation_status=ValidationStatus.VALID.value,
    )


def _target_return_words() -> list[SimpleNamespace]:
    """Summary zone of a Target return: all three figures print 'N.NN-'."""
    return [
        _word(10, 1, "SUBTOTAL", 0.30, x=0.1),
        _word(10, 2, "$14.99-", 0.30, x=0.8),
        _word(11, 1, "TAX", 0.32, x=0.1),
        _word(11, 2, "$1.26-", 0.32, x=0.8),
        _word(12, 1, "TOTAL", 0.34, x=0.1),
        _word(12, 2, "$16.25-", 0.34, x=0.8),
    ]


def _target_return_labels() -> list[ReceiptWordLabel]:
    return [
        _label(10, 2, "SUBTOTAL"),
        _label(11, 2, "TAX"),
        _label(12, 2, "GRAND_TOTAL"),
    ]


def test_trailing_minus_totals_parse_negative_and_reconcile():
    summary = ReceiptSummary.from_word_labels_and_words(
        image_id=IMAGE_ID,
        receipt_id=1,
        merchant_name="Target",
        word_labels=_target_return_labels(),
        words=_target_return_words(),
    )

    assert summary.grand_total == pytest.approx(-16.25)
    assert summary.subtotal == pytest.approx(-14.99)
    assert summary.tax == pytest.approx(-1.26)
    # The refund reconciles with its sign intact.
    assert summary.subtotal + summary.tax == pytest.approx(summary.grand_total)


def test_printed_fallback_leaves_negative_label_totals_alone():
    """A stray positive figure in the total row's band must not clobber
    a label-derived refund total (the old <= 0 guard let it)."""
    words = _target_return_words() + [
        # Loyalty figure sharing the TOTAL row's y-band; positive, so
        # the printed fallback would anchor on it if it ran.
        _word(13, 1, "$5.00", 0.34, x=0.5),
    ]
    totals = MonetaryTotals(grand_total=-16.25, subtotal=-14.99, tax=-1.26)

    _apply_printed_total_fallback(totals, words)

    assert totals.grand_total == pytest.approx(-16.25)
    assert totals.subtotal == pytest.approx(-14.99)


def test_printed_fallback_still_fills_missing_totals():
    """None/zero totals keep falling back to printed positive rows."""
    words = [
        _word(12, 1, "TOTAL", 0.34, x=0.1),
        _word(12, 2, "$16.25", 0.34, x=0.8),
    ]
    totals = MonetaryTotals(grand_total=None, subtotal=None, tax=None)

    _apply_printed_total_fallback(totals, words)

    assert totals.grand_total == pytest.approx(16.25)
