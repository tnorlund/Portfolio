"""Unit tests for the geometric line-item extractor's band logic.

Covers the failure modes found in the 2026-07-29 adversarial review:
skew chaining, echo false-dedupe, cross-boundary name pairing, negative
amounts, 1x quantities, fuel bands, and bare "2 3.99" qty bands.

Relocated from scripts/tests/, where the repository-tests CI job never
collected them (find scripts -maxdepth 1); here they run on every PR.
"""

from receipt_upload.line_items.geometry import (
    band_words,
    extract_items,
    parse_band,
)


def W(line_id, word_id, text, x, y, h=0.02):
    return {
        "line_id": line_id,
        "word_id": word_id,
        "text": text,
        "x": x,
        "y_mid": y,
        "h": h,
    }


def row(line_id, y, *tokens):
    """One visual row: tokens laid out left to right."""
    return [
        W(line_id, i + 1, t, 0.1 + 0.2 * i, y) for i, t in enumerate(tokens)
    ]


def items_for(words):
    line_ids = {w["line_id"] for w in words}
    items, collapsed = extract_items(words, line_ids)
    return items, collapsed


def test_skew_drift_does_not_chain_rows():
    # 6 rows at 0.030 pitch; single-linkage with med_h*0.6 = 0.012 would
    # never chain here, so tighten the pitch to just under the threshold:
    # each consecutive gap 0.011 < 0.012 chains under single-linkage, but
    # anchor-linkage caps the band at the first word.
    words = []
    for i in range(6):
        words += row(i + 1, 0.1 + i * 0.011, f"ITEM{i}", f"{i + 1}.99")
    bands = band_words(words)
    assert (
        len(bands) >= 3
    ), f"skew chained {6 - len(bands) + 1} rows into one band"


def test_echo_dedup_requires_sku_signature():
    # Two adjacent same-priced bands; the second's name is garbled OCR
    # (no 4+ digit run, no qty) -> must NOT be dropped as an echo.
    words = row(1, 0.10, "ORGANIC", "SOUR", "CREAM", "2.99") + row(
        2, 0.15, "q%", "2.99"
    )
    items, _ = items_for(words)
    assert len(items) == 2, [i["raw_text"] for i in items]
    assert items[1].get("name_quality") == "low"


def test_echo_dedup_still_drops_sku_metadata():
    # SKU echo (long digit run) with the same price IS dropped.
    words = row(1, 0.10, "DUCT", "TAPE", "WHT", "$5.69") + row(
        2, 0.15, "452700", "1", "EA", "$5.69"
    )
    items, _ = items_for(words)
    assert len(items) == 1
    assert items[0]["name"].startswith("DUCT")


def test_name_band_not_stolen_across_boundary():
    # [MILK 4.00][9.99][BREAD][3.50]: BREAD sits right above 3.50 and far
    # from 9.99 -> BREAD pairs with 3.50; 9.99 surfaces as low-name item.
    words = (
        row(1, 0.10, "MILK", "4.00")
        + row(2, 0.20, "9.99")
        + row(3, 0.40, "BREAD")
        + row(4, 0.43, "3.50")
    )
    items, _ = items_for(words)
    by_price = {i["price"]: i for i in items}
    assert by_price[3.5]["name"] == "BREAD"
    assert by_price[3.5].get("stacked") is True
    assert by_price[9.99].get("name_quality") == "low"


def test_leading_minus_refund_is_negative_discount():
    band = row(1, 0.1, "BOTTLE", "REFUND", "-12.99")
    parsed = parse_band(band)
    assert parsed["price"] == -12.99
    assert parsed["is_discount"] is True


def test_1x_multiplier_is_quantity():
    band = row(1, 0.1, "1x", "Pad", "Thai", "13.50")
    parsed = parse_band(band)
    assert parsed["quantity"] == 1.0
    assert parsed["name"] == "Pad Thai"
    assert parsed["price"] == 13.50


def test_fuel_band_three_decimal_unit_price():
    band = row(1, 0.1, "18.871", "@", "$5.299/Gal", "99.99")
    parsed = parse_band(band)
    assert parsed["quantity"] == 18.871
    assert parsed["unit_price"] == 5.299
    assert parsed["price"] == 99.99


def test_bare_qty_band_attaches_to_neighbor():
    words = row(1, 0.10, "2", "3.99") + row(
        2, 0.15, "RUSSET", "POTATO", "7.98"
    )
    items, _ = items_for(words)
    assert len(items) == 1
    assert items[0]["quantity"] == 2.0
    assert items[0]["unit_price"] == 3.99
    assert items[0]["price"] == 7.98


def test_weight_paren_gram_is_not_a_price():
    # "(7.00g)" is a pack weight: it must never be chosen as the price
    # (the old PRICE_RE matched it). Staying in the name text is fine.
    band = row(1, 0.1, "GUMMY", "MIX", "(7.00g)", "4.49")
    parsed = parse_band(band)
    assert parsed["price"] == 4.49


def test_one_decimal_and_bare_thousands_are_not_prices():
    # "$4,333.6" (loyalty spend tracker) and "4,444" (SKU) parse as
    # amounts but are not line prices; "11.62" is.
    band = row(1, 0.1, "PRO", "XTRA", "SPEND", "$4,333.6")
    assert parse_band(band) is None or parse_band(band)["price"] != 4333.6
    band2 = row(2, 0.2, "WIDGET", "4,444", "11.62")
    parsed = parse_band(band2)
    assert parsed["price"] == 11.62


def test_close_paren_orphan_is_not_a_price():
    # "(2 @0.00)" OCRs as "(2" + "80.00)": the close-paren orphan must not
    # become the price; the band pairs with the price-column META instead.
    words = row(1, 0.10, "Water", "(2", "80.00)") + row(2, 0.13, "0.00")
    items, _ = items_for(words)
    assert len(items) == 1
    assert items[0]["price"] == 0.0 or items[0]["price"] == 0
    assert "Water" in items[0]["name"]


def test_word_ids_retained_for_label_projection():
    band = row(1, 0.1, "COLD", "BREW", "6.49")
    parsed = parse_band(band)
    assert parsed["price_word_id"] == {"line_id": 1, "word_id": 3}
    assert [w["word_id"] for w in parsed["name_word_ids"]] == [1, 2]
