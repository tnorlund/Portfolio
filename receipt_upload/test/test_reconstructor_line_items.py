"""Regression tests for geometry line-item reconstruction.

Built from the Trader Joe's IMG_2826 case: two line items whose totals
coincidentally sum to the grand total, plus a "6 @ $0.23" quantity/unit-price
row. The first-pass model mislabels the line totals as SUBTOTAL/TAX and never
emits QUANTITY/UNIT_PRICE; geometry must recover them.
"""

from types import SimpleNamespace

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.entities import ReceiptWordLabel

from receipt_upload.line_items import propose_line_item_labels

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


def _anchor(line_id, word_id, label):
    return ReceiptWordLabel(
        image_id=IMAGE_ID,
        receipt_id=1,
        line_id=line_id,
        word_id=word_id,
        label=label,
        reasoning="test anchor",
        timestamp_added="2026-01-01T00:00:00.000+00:00",
        validation_status=ValidationStatus.VALID.value,
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


def _proposals():
    words = _trader_joes_words()
    anchors = [
        _anchor(2, 1, "ADDRESS_LINE"),
        _anchor(2, 2, "ADDRESS_LINE"),
        _anchor(1, 1, "MERCHANT_NAME"),
        _anchor(13, 1, "GRAND_TOTAL"),
    ]
    return {(p.line_id, p.word_id): p for p in propose_line_item_labels(words, anchors)}


def test_line_totals_recovered():
    p = _proposals()
    assert p[(11, 1)].label == "LINE_TOTAL"  # $4.29 (milk)
    assert p[(12, 1)].label == "LINE_TOTAL"  # $1.38 (banana)


def test_product_names_recovered():
    p = _proposals()
    assert p[(8, 1)].label == "PRODUCT_NAME"  # MILK
    assert p[(9, 1)].label == "PRODUCT_NAME"  # BANANA


def test_quantity_and_unit_price_from_at_row():
    p = _proposals()
    # "6 @ $0.23": qty is the integer before "@", unit price is the price after.
    assert p[(10, 1)].label == "QUANTITY"
    assert p[(10, 3)].label == "UNIT_PRICE"
    # The single price on an "N @ $X" row is NOT a line total.
    assert p[(10, 3)].label != "LINE_TOTAL"


def test_arithmetic_gate_auto_validates():
    p = _proposals()
    # Σ(LINE_TOTAL) = 4.29 + 1.38 = 5.67 = GRAND_TOTAL -> auto-VALID.
    line_totals = [v for v in p.values() if v.label == "LINE_TOTAL"]
    assert line_totals
    assert all(
        v.validation_status == ValidationStatus.VALID.value for v in line_totals
    )
