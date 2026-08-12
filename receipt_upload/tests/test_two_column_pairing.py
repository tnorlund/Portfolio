"""Split over-merged visual bands that glue two product rows together.

``band_words`` joins name-left with price-right; on tight row pitch that
also merges two stacked name+price rows into one band, and ``parse_band``
then emits a single item. The split uses the right-most price column and
y-within-band — no merchant vocabulary, and no invented prices.
"""

from receipt_upload.line_items.geometry import (
    band_words,
    extract_items,
    parse_band,
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


def items_for(words):
    line_ids = {w["line_id"] for w in words}
    items, _ = extract_items(words, line_ids)
    return items


def test_two_stacked_name_price_rows_split():
    # Pitch 0.012 is under med_h*0.6=0.024, so clustering merges the rows.
    # Two column prices at x=0.80, y-separated by 0.012 >= 0.25*0.03.
    words = [
        W(1, 1, "CLOVER", 0.05, 0.625),
        W(1, 2, "SPROUTS", 0.22, 0.625),
        W(2, 1, "3.99", 0.80, 0.625, 0.03),
        W(3, 1, "CLUSTER", 0.05, 0.613),
        W(3, 2, "TOMATOES", 0.22, 0.613),
        W(4, 1, "3.92", 0.80, 0.613, 0.03),
    ]
    bands = band_words(words)
    assert len(bands) == 2, [[w["text"] for w in b] for b in bands]
    items = items_for(words)
    by_price = {i["price"]: i["name"] for i in items}
    assert by_price[3.99] == "CLOVER SPROUTS"
    assert by_price[3.92] == "CLUSTER TOMATOES"


def test_two_names_one_price_does_not_mint_a_price():
    # Two left-side name clusters, only one column amount. Splitting
    # cannot invent the missing price; the band stays one item.
    words = [
        W(1, 1, "MILK", 0.05, 0.625),
        W(2, 1, "YOGURT", 0.05, 0.613),
        W(3, 1, "5.99", 0.80, 0.620, 0.03),
    ]
    items = items_for(words)
    assert len(items) == 1
    assert items[0]["price"] == 5.99
    assert set(items[0]["name"].split()) == {"MILK", "YOGURT"}


def test_qty_unit_and_line_total_on_one_row_stay_one_item():
    # Weight + unit price sit left of the price column; only 2.74 is in
    # column. Must not split the annotation off the product.
    words = [
        W(1, 1, "ONIONS", 0.05, 0.50, 0.02),
        W(1, 2, "1.62", 0.30, 0.50, 0.02),
        W(1, 3, "lb", 0.40, 0.50, 0.02),
        W(1, 4, "@", 0.48, 0.50, 0.02),
        W(1, 5, "$1.69", 0.55, 0.50, 0.02),
        W(1, 6, "2.74", 0.80, 0.50, 0.02),
    ]
    items = items_for(words)
    assert len(items) == 1
    assert items[0]["price"] == 2.74
    assert "ONIONS" in items[0]["name"]


def test_same_row_price_and_you_pay_stay_one_band():
    # Vons-style Price / You Pay: two column amounts, y-gap ~0.0005, well
    # under 0.25 * height. parse_band keeps the last amount.
    words = [
        W(1, 1, "GLAD", 0.05, 0.75, 0.012),
        W(1, 2, "WRAP", 0.20, 0.75, 0.012),
        W(1, 3, "5.99", 0.70, 0.7515, 0.012),
        W(1, 4, "6.99", 0.85, 0.7520, 0.012),
    ]
    bands = band_words(words)
    assert len(bands) == 1
    parsed = parse_band(bands[0])
    assert parsed["price"] == 6.99
    assert parsed["name"] == "GLAD WRAP"


def test_identical_stacked_prices_still_split():
    # Two ice-cream rows at the same $6.49: still two column prices at
    # distinct y, so two items — not one glued name with one price.
    words = [
        W(1, 1, "LEMON", 0.05, 0.938, 0.025),
        W(1, 2, "COOKIE", 0.18, 0.938, 0.025),
        W(2, 1, "6.49", 0.77, 0.937, 0.02),
        W(3, 1, "MINT", 0.05, 0.924, 0.025),
        W(3, 2, "CHIP", 0.16, 0.924, 0.025),
        W(4, 1, "6.49", 0.77, 0.923, 0.02),
    ]
    items = items_for(words)
    names = sorted(i["name"] for i in items)
    assert [i["price"] for i in items] == [6.49, 6.49]
    assert names == ["LEMON COOKIE", "MINT CHIP"]
