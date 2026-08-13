"""Gated join of OCR-shattered column prices.

``merge_price_fragments`` is trialled in ``extract_items`` only when a
printed summary is present and the merged decode then exact-matches.
Ungated merge is forbidden: Costco ``5,``+``90`` becomes 5.90 (truth 5.99).
"""

from receipt_upload.line_items.blocks import merge_price_fragments
from receipt_upload.line_items.geometry import (
    evaluate_items_zone,
    extract_items,
)


def W(line_id, word_id, text, x, y, h=0.04):
    return {
        "line_id": line_id,
        "word_id": word_id,
        "text": text,
        "x": x,
        "y_mid": y,
        "h": h,
    }


def _decode(words, summary):
    line_ids = {w["line_id"] for w in words}
    items, _ = extract_items(words, line_ids, summary=summary)
    zone = evaluate_items_zone(words, summary, line_ids)
    return items, zone


def test_merge_joins_trailing_separator_and_bare_dot_cents():
    sep = [
        W(1, 1, "EGGS", 0.05, 0.50),
        W(2, 1, "5.", 0.81, 0.50),
        W(2, 2, "79", 0.86, 0.50),
    ]
    merged = merge_price_fragments(sep)
    assert [w["text"] for w in merged] == ["EGGS", "5.79"]

    dot = [
        W(1, 1, "TOMATOES", 0.05, 0.50),
        W(2, 1, "5", 0.814, 0.50),
        W(2, 2, ".99", 0.839, 0.50),
    ]
    merged = merge_price_fragments(dot)
    assert [w["text"] for w in merged] == ["TOMATOES", "5.99"]


def test_close_paren_is_not_a_decimal():
    # 687da832: a quantity carcass, not 5.99.
    words = [
        W(1, 1, "FOO", 0.05, 0.50),
        W(2, 1, "5)", 0.81, 0.50),
        W(2, 2, "99", 0.86, 0.50),
    ]
    assert [w["text"] for w in merge_price_fragments(words)] == [
        "FOO",
        "5)",
        "99",
    ]
    milk = [
        W(3, 1, "RAW", 0.05, 0.40),
        W(3, 2, "MILK", 0.22, 0.40),
        W(4, 1, "10.00", 0.80, 0.40),
    ]
    items, zone = _decode(words + milk, {"subtotal": 15.99})
    assert 5.99 not in [i["price"] for i in items]
    assert zone["status"] != "match"


def test_eggs_5_dot_plus_cents_matches_when_gated():
    # 06e54f95: CAGE FREE BROWN EGGS shattered as 5. + 79.
    words = [
        W(1, 1, "CAGE", 0.05, 0.55),
        W(1, 2, "FREE", 0.18, 0.55),
        W(1, 3, "BROWN", 0.32, 0.55),
        W(1, 4, "EGGS", 0.48, 0.55),
        W(2, 1, "5.", 0.81, 0.55),
        W(2, 2, "79", 0.86, 0.55),
        W(3, 1, "RAW", 0.05, 0.40),
        W(3, 2, "WHOLE", 0.18, 0.40),
        W(3, 3, "MILK", 0.34, 0.40),
        W(4, 1, "17.99", 0.80, 0.40),
    ]
    items, zone = _decode(words, {"subtotal": 23.78})
    by_price = {i["price"]: i["name"] for i in items}
    assert by_price[17.99] == "RAW WHOLE MILK"
    assert by_price[5.79] == "CAGE FREE BROWN EGGS"
    assert zone["status"] == "match"
    assert zone["items_sum"] == 23.78


def test_tomatoes_bare_dollars_plus_dot_cents_matches():
    # 2f945f8d: ORG CHERRY TOMATOES shattered as 5 + .99.
    words = [
        W(1, 1, "ORG", 0.05, 0.62),
        W(1, 2, "CHERRY", 0.16, 0.62),
        W(1, 3, "TOMATOES", 0.34, 0.62),
        W(2, 1, "5", 0.814, 0.62),
        W(2, 2, ".99", 0.839, 0.62),
        W(3, 1, "BABY", 0.05, 0.50),
        W(3, 2, "SPINACH", 0.20, 0.50),
        W(4, 1, "19.99", 0.80, 0.50),
        W(5, 1, "CHICKEN", 0.05, 0.38),
        W(6, 1, "17.81", 0.80, 0.38),
    ]
    items, zone = _decode(words, {"subtotal": 43.79})
    by_price = {i["price"]: i["name"] for i in items}
    assert by_price[5.99].startswith("ORG")
    assert "TOMATOES" in by_price[5.99]
    assert sorted(i["price"] for i in items) == [5.99, 17.81, 19.99]
    assert zone["status"] == "match"
    assert zone["items_sum"] == 43.79


def test_costco_comma_fragment_near_miss_keeps_unmerged():
    # Ungated 5,+90 → 5.90 would land near (~0.09), not match. The gate
    # must keep the unmerged decode (mismatch by ~5.99).
    words = [
        W(1, 1, "KIRKLAND", 0.05, 0.55),
        W(1, 2, "WATER", 0.28, 0.55),
        W(2, 1, "5,", 0.81, 0.55),
        W(2, 2, "90", 0.86, 0.55),
        W(3, 1, "CHICKEN", 0.05, 0.40),
        W(4, 1, "1.00", 0.80, 0.40),
    ]
    items, zone = _decode(words, {"subtotal": 6.99})
    prices = [i["price"] for i in items]
    assert 5.90 not in prices
    assert zone["status"] == "mismatch"
    assert zone["delta"] == -5.99


def test_three_token_5_1_dot_99_does_not_ship_as_1_99():
    # 57cb7f2c: do not specially join 5 + 1. + 99. If 1.+99 merges to
    # 1.99, the receipt-level gate rejects it (not an exact match).
    words = [
        W(1, 1, "WIDGET", 0.05, 0.55),
        W(2, 1, "5", 0.75, 0.55),
        W(2, 2, "1.", 0.81, 0.55),
        W(2, 3, "99", 0.86, 0.55),
        W(3, 1, "MILK", 0.05, 0.40),
        W(4, 1, "10.00", 0.80, 0.40),
    ]
    items, zone = _decode(words, {"subtotal": 15.99})
    assert 1.99 not in [i["price"] for i in items]
    assert 5.99 not in [i["price"] for i in items]
    assert zone["status"] != "match"


def test_summary_none_does_not_merge():
    words = [
        W(1, 1, "CAGE", 0.05, 0.55),
        W(1, 2, "FREE", 0.18, 0.55),
        W(1, 3, "BROWN", 0.32, 0.55),
        W(1, 4, "EGGS", 0.48, 0.55),
        W(2, 1, "5.", 0.81, 0.55),
        W(2, 2, "79", 0.86, 0.55),
        W(3, 1, "RAW", 0.05, 0.40),
        W(3, 2, "WHOLE", 0.18, 0.40),
        W(3, 3, "MILK", 0.34, 0.40),
        W(4, 1, "17.99", 0.80, 0.40),
    ]
    items, _ = extract_items(
        words, {w["line_id"] for w in words}, summary=None
    )
    assert 5.79 not in [i["price"] for i in items]
    milk = [i for i in items if i["price"] == 17.99]
    assert milk
