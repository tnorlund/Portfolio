"""Regression tests for geometry line-item reconstruction.

Built from the Trader Joe's IMG_2826 case: two line items whose totals
coincidentally sum to the grand total, plus a "6 @ $0.23" quantity/unit-price
row. The first-pass model mislabels the line totals as SUBTOTAL/TAX and never
emits QUANTITY/UNIT_PRICE; the upload pipeline must (1) reclassify the
mislabeled SUBTOTAL/TAX to LINE_TOTAL when arithmetic proves it, then (2)
recover PRODUCT_NAME/QUANTITY/UNIT_PRICE by geometry.

These tests exercise the *production* label set — the model's PENDING
SUBTOTAL/TAX labels are present, exactly as a real upload would see them — not
curated anchors.
"""

from types import SimpleNamespace

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.entities import ReceiptWordLabel

from receipt_upload.line_items import (
    propose_line_item_labels,
    reclassify_mislabeled_totals,
)

IMAGE_ID = "00000000-0000-4000-8000-000000000abc"


def _w(line_id, word_id, text, x, y, w=0.08, h=0.02):
    return SimpleNamespace(
        image_id=IMAGE_ID,
        receipt_id=1,
        line_id=line_id,
        word_id=word_id,
        text=text,
        bounding_box={"x": x, "y": y, "width": w, "height": h},
    )


def _label(line_id, word_id, label, status=ValidationStatus.VALID.value):
    return ReceiptWordLabel(
        image_id=IMAGE_ID,
        receipt_id=1,
        line_id=line_id,
        word_id=word_id,
        label=label,
        reasoning="test label",
        timestamp_added="2026-01-01T00:00:00.000+00:00",
        validation_status=status,
    )


def _trader_joes_words():
    # y is bottom-origin (header high, totals low). Prices on the right (x~0.7).
    return [
        _w(1, 1, "TRADER", 0.30, 0.95),
        _w(1, 2, "JOE'S", 0.50, 0.95),
        _w(2, 1, "2716", 0.20, 0.90),
        _w(2, 2, "Parkway", 0.40, 0.90),
        # line items
        _w(8, 1, "MILK", 0.10, 0.70),
        _w(8, 2, "ORGANIC", 0.22, 0.70),
        _w(8, 3, "HALF", 0.34, 0.70),
        _w(8, 4, "GALLON", 0.44, 0.70),
        _w(11, 1, "$4.29", 0.72, 0.70),
        _w(9, 1, "BANANA", 0.10, 0.66),
        _w(9, 2, "EACH", 0.24, 0.66),
        _w(12, 1, "$1.38", 0.72, 0.66),
        _w(10, 1, "6", 0.10, 0.63),
        _w(10, 2, "@", 0.18, 0.63),
        _w(10, 3, "$0.23", 0.28, 0.63),
        # grand total
        _w(13, 1, "$5.67", 0.72, 0.50),
    ]


def _model_labels():
    """The first-pass model's PENDING output for IMG_2826 — the production set.

    The line totals are MISLABELED as SUBTOTAL/TAX (no Subtotal/Tax keyword, and
    4.29 + 1.38 == 5.67 fools the model).
    """
    return [
        _label(2, 1, "ADDRESS_LINE"),
        _label(2, 2, "ADDRESS_LINE"),
        _label(1, 1, "MERCHANT_NAME"),
        _label(11, 1, "SUBTOTAL", ValidationStatus.PENDING.value),  # $4.29 (milk)
        _label(12, 1, "TAX", ValidationStatus.PENDING.value),       # $1.38 (banana)
        _label(13, 1, "GRAND_TOTAL", ValidationStatus.PENDING.value),
    ]


def _run_pipeline(labels=None):
    """Mirror the upload pipeline: reclassify mislabeled totals, then geometry."""
    words = _trader_joes_words()
    labels = list(_model_labels() if labels is None else labels)

    # Stage 1: arithmetic-gated reclassification of mislabeled SUBTOTAL/TAX,
    # plus locking the existing line totals the arithmetic confirms.
    reclassifications, locked = reclassify_mislabeled_totals(words, labels)
    for old, new in reclassifications:
        old.validation_status = ValidationStatus.INVALID.value
        labels.append(new)
    for lt in locked:
        lt.validation_status = ValidationStatus.VALID.value

    # Stage 2: geometry line-item recovery over the corrected label set. The
    # active label for a word is its non-INVALID one (a word may carry an
    # INVALID SUBTOTAL alongside a VALID LINE_TOTAL).
    by_key = {}
    for l in labels:
        if l.validation_status != ValidationStatus.INVALID.value:
            by_key[(l.line_id, l.word_id)] = l
    for p in propose_line_item_labels(words, labels):
        by_key[(p.line_id, p.word_id)] = p
    return by_key


def test_mislabeled_totals_reclassified_to_line_totals():
    p = _run_pipeline()
    assert p[(11, 1)].label == "LINE_TOTAL"  # $4.29 was SUBTOTAL
    assert p[(12, 1)].label == "LINE_TOTAL"  # $1.38 was TAX
    # Arithmetic proved it -> committed VALID, not left PENDING.
    assert p[(11, 1)].validation_status == ValidationStatus.VALID.value
    assert p[(12, 1)].validation_status == ValidationStatus.VALID.value


def test_product_names_recovered():
    p = _run_pipeline()
    assert p[(8, 1)].label == "PRODUCT_NAME"  # MILK
    assert p[(9, 1)].label == "PRODUCT_NAME"  # BANANA


def test_quantity_and_unit_price_from_at_row():
    p = _run_pipeline()
    # "6 @ $0.23": qty is the integer before "@", unit price is the price after.
    assert p[(10, 1)].label == "QUANTITY"
    assert p[(10, 3)].label == "UNIT_PRICE"
    # The single price on an "N @ $X" row is NOT a line total.
    assert p[(10, 3)].label != "LINE_TOTAL"


def test_locks_existing_line_total_against_llm_correction():
    """Production case: the model labels one item LINE_TOTAL and the other SUBTOTAL.

    Reclassify the SUBTOTAL ($4.29) and *lock* the existing LINE_TOTAL ($1.38)
    so the LLM validator can't "correct" it to TAX (the bug observed on the live
    IMG_2826 upload).
    """
    P = ValidationStatus.PENDING.value
    labels = [
        _label(2, 1, "ADDRESS_LINE"),
        _label(1, 1, "MERCHANT_NAME"),
        _label(11, 1, "SUBTOTAL", P),     # $4.29 mislabeled
        _label(12, 1, "LINE_TOTAL", P),   # $1.38 model got this one right
        _label(13, 1, "GRAND_TOTAL", P),
    ]
    words = _trader_joes_words()
    reclassifications, locked = reclassify_mislabeled_totals(words, labels)
    # $4.29 SUBTOTAL -> LINE_TOTAL
    assert [(o.label, n.label) for o, n in reclassifications] == [
        ("SUBTOTAL", "LINE_TOTAL")
    ]
    # $1.38's existing LINE_TOTAL is returned for locking (so it survives the LLM).
    assert [(l.line_id, l.word_id) for l in locked] == [(12, 1)]


def test_reclassification_is_arithmetic_gated_on_grand_total():
    """No GRAND_TOTAL value -> nothing to reconcile against -> no override."""
    words = _trader_joes_words()
    labels = [l for l in _model_labels() if l.label != "GRAND_TOTAL"]
    assert reclassify_mislabeled_totals(words, labels) == ([], [])


def test_normal_receipt_subtotal_tax_not_reclassified():
    """A correctly-labeled receipt must keep its SUBTOTAL/TAX.

    Two real line items ($2.00, $3.00) sum to SUBTOTAL ($5.00); SUBTOTAL + TAX
    ($0.50) == GRAND_TOTAL ($5.50). That SUBTOTAL+TAX==GRAND identity is the
    definition of a receipt, not a mislabel — the override must NOT fire.
    """
    words = [
        _w(1, 1, "SHOP", 0.30, 0.95),
        _w(5, 1, "APPLE", 0.10, 0.80),
        _w(5, 2, "$2.00", 0.72, 0.80),
        _w(6, 1, "BREAD", 0.10, 0.76),
        _w(6, 2, "$3.00", 0.72, 0.76),
        _w(7, 1, "Subtotal", 0.10, 0.60),
        _w(7, 2, "$5.00", 0.72, 0.60),
        _w(8, 1, "Tax", 0.10, 0.57),
        _w(8, 2, "$0.50", 0.72, 0.57),
        _w(9, 1, "Total", 0.10, 0.54),
        _w(9, 2, "$5.50", 0.72, 0.54),
    ]
    labels = [
        _label(1, 1, "MERCHANT_NAME"),
        _label(5, 2, "LINE_TOTAL", ValidationStatus.PENDING.value),
        _label(6, 2, "LINE_TOTAL", ValidationStatus.PENDING.value),
        _label(7, 2, "SUBTOTAL", ValidationStatus.PENDING.value),
        _label(8, 2, "TAX", ValidationStatus.PENDING.value),
        _label(9, 2, "GRAND_TOTAL", ValidationStatus.PENDING.value),
    ]
    assert reclassify_mislabeled_totals(words, labels) == ([], [])


def test_human_validated_totals_never_overridden():
    """VALID (human-confirmed) SUBTOTAL/TAX are deliberate and must be left alone."""
    words = _trader_joes_words()
    labels = _model_labels()
    for l in labels:
        if l.label in ("SUBTOTAL", "TAX"):
            l.validation_status = ValidationStatus.VALID.value
    assert reclassify_mislabeled_totals(words, labels) == ([], [])
