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


# --- non-product band filter (summary-figure / promo-echo / tender) ------


def items_with_summary(words, summary):
    line_ids = {w["line_id"] for w in words}
    items, _ = extract_items(words, line_ids, summary=summary)
    return items


def test_summary_figure_unnamed_band_dropped():
    # Mob Museum: a bare "57.90" band == printed grand total emitted as an
    # unnamed item alongside the two real tickets. With the summary
    # available, the figure band is dropped and the receipt reconciles.
    words = (
        row(1, 0.30, "1", "Adult-Local-Any", "19.95")
        + row(2, 0.25, "1", "Adult-Anytime", "37.95")
        + row(3, 0.20, "57.90")
    )
    items = items_with_summary(words, {"grand_total": "57.90", "tax": "0.0"})
    assert sorted(i["price"] for i in items) == [19.95, 37.95]


def test_summary_figure_named_order_line_dropped():
    # In-N-Out prints the order type + total INSIDE the items block
    # ("DRIVE-Thru Eat Out 14.50"); the band carries a real-looking name,
    # so it may only drop when the remaining items then reconcile.
    words = (
        row(1, 0.30, "5", "Meat", "Patty", "9.75")
        + row(2, 0.25, "1", "Animal", "Fry", "4.75")
        + row(3, 0.20, "DRIVE-Tat", ".", "Out", "14.50")
    )
    items = items_with_summary(
        words,
        {"subtotal": "14.5", "grand_total": "15.91", "tax": "1.41"},
    )
    assert sorted(i["price"] for i in items) == [4.75, 9.75]


def test_summary_figure_guard_needs_two_survivors():
    # n_items >= 2 guard (LOAD-BEARING): when dropping the figure band
    # would leave fewer than 2 items, nothing is dropped -- single-item
    # receipts legitimately have item price == total.
    words = row(1, 0.30, "2", "Animal", "Fry", "9.20") + row(
        2, 0.25, "DRIVE", "Eat", "In", "9.20"
    )
    items = items_with_summary(
        words, {"subtotal": "9.2", "grand_total": "9.97"}
    )
    assert sorted(i["price"] for i in items) == [9.2, 9.2]


def test_summary_figure_single_item_receipt_untouched():
    # A one-item receipt whose only item equals the printed total already
    # reconciles; the filter must never touch it (Barnes & Noble / CVS /
    # Target / LA County in the failure-mode report).
    words = row(1, 0.30, "HARDCOVER", "BOOK", "24.99")
    items = items_with_summary(
        words, {"subtotal": "24.99", "grand_total": "27.05", "tax": "2.06"}
    )
    assert len(items) == 1
    assert items[0]["price"] == 24.99


def test_named_item_at_tax_amount_survives():
    # A real product coincidentally priced at the tax figure must survive
    # even on a receipt that fails to reconcile: named bands are never
    # matched against the tax figure.
    words = (
        row(1, 0.30, "COFFEE", "2.33")
        + row(2, 0.25, "BAGEL", "5.00")
        + row(3, 0.20, "MUFFIN", "3.00")
    )
    items = items_with_summary(
        words, {"subtotal": "8.00", "grand_total": "10.33", "tax": "2.33"}
    )
    assert sorted(i["price"] for i in items) == [2.33, 3.0, 5.0]


def test_sprouts_promo_echo_absorbs_not_emits():
    # Sprouts prints "1@2 FOR 14.00" under each half of a 2-for deal; the
    # promo band explains the neighboring 7.00 item (1 * 7.00) and must
    # absorb into it instead of emitting a second 14.00 item.
    words = row(1, 0.30, "BEER", "BRATWURST", "7.00", "F") + row(
        2, 0.25, "1@2", "FOR", "14.00"
    )
    items, _ = items_for(words)
    assert len(items) == 1
    assert items[0]["price"] == 7.0
    assert items[0]["quantity"] == 1.0
    assert items[0]["unit_price"] == 7.0


def test_target_regular_price_echo_dropped():
    # Target restates the pre-discount price as "Regular Price $22.99"
    # under the discounted item; it is an annotation, never an item.
    words = row(1, 0.30, "070050839", "FOOD", "CHOPPER", "T", "$19.54") + row(
        2, 0.25, "Regular", "Price", "$22.99"
    )
    items, _ = items_for(words)
    assert [i["price"] for i in items] == [19.54]


def test_tip_suggestion_and_count_notes_are_not_items():
    # Restaurant tip-suggestion footers and transaction-count notes carry
    # amounts but are never items; product names with a bare percent
    # ("6% FAT MLK") must survive.
    for tokens in (
        ["22%", "Tip", "=", "4.40"],
        ["15%", "=", "10.73"],
        ["18%:", "(Tip", "Total", "9.27"],
        ["Items", "in", "Transaction:", "5", "5.49"],
        ["Comparable", "Value", "59.95"],
    ):
        words = row(1, 0.30, "REAL", "WIDGET", "3.00") + row(2, 0.25, *tokens)
        items, _ = items_for(words)
        assert [i["price"] for i in items] == [
            3.0
        ], f"note band leaked: {tokens}"
    words = row(1, 0.30, "JRG", "A2/A2", "6%", "FAT", "MLK", "7.99")
    items, _ = items_for(words)
    assert [i["price"] for i in items] == [7.99]


def test_settlement_scrambled_and_prefixed_forms():
    # OCR word-order scramble ("17.98 DUE BALANCE"), item-count prefix
    # ("[1 item] Sub Total 16.00"), and AUTH DEBIT are settlement bands.
    for tokens in (
        ["17.98", "DUE", "BALANCE"],
        ["[1", "item]", "Sub", "Total", "16.00"],
        ["2014.98", "AUTH", "DEBIT", "$20.47"],
    ):
        words = row(1, 0.30, "REAL", "WIDGET", "3.00") + row(2, 0.25, *tokens)
        items, _ = items_for(words)
        assert [i["price"] for i in items] == [
            3.0
        ], f"settlement band leaked: {tokens}"
