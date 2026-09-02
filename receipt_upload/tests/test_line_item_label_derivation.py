"""Unit tests for deterministic word-label derivation from the decode.

The gate is arithmetic: labels exist only when the decoded items sum to
the receipt's printed baseline exactly. These tests pin that gate, the
word-level placement of every label type, and the fail-closed rules
(ambiguous summary figures, name words with no letters, discount rows).
"""

import pytest

from receipt_upload.label_validation.label_normalization import is_core_label
from receipt_upload.line_items.labels import (
    DECODER_PROPOSED_BY,
    GATE_NO_ITEMS_SECTION,
    GATE_NOT_MATCHED,
    GATE_NOT_PROVEN,
    GATE_OK,
    _amount_of,
    _ocr_damaged_amount_of,
    _quantity_labels,
    derive_labels,
)


class FakeWord:
    """The ReceiptWord surface the derivation actually reads."""

    def __init__(self, line_id, word_id, text, x, y, h=0.02):
        self.line_id = line_id
        self.word_id = word_id
        self.text = text
        self.bounding_box = {"x": x, "y": y - h / 2, "width": 0.1, "height": h}


def row(line_id, y, *tokens, x0=0.1, dx=0.2):
    return [
        FakeWord(line_id, i + 1, t, x0 + dx * i, y)
        for i, t in enumerate(tokens)
    ]


def placed(result):
    """{(line_id, word_id): label} for every derived proposal."""
    return {(p.line_id, p.word_id): p.label for p in result.labels}


def by_label(result):
    """{label: {word texts}} for every derived proposal."""
    out = {}
    for proposal in result.labels:
        out.setdefault(proposal.label, set()).add(proposal.text)
    return out


def test_names_and_prices_land_on_the_right_words():
    words = row(1, 0.10, "ORGANIC", "BANANAS", "2.99") + row(
        2, 0.15, "WHOLE", "MILK", "4.49"
    )
    result = derive_labels(words, {1, 2}, {"subtotal": 7.48})

    assert result.gate == GATE_OK
    assert result.reconciliation_status == "match"
    assert by_label(result) == {
        "PRODUCT_NAME": {"ORGANIC", "BANANAS", "WHOLE", "MILK"},
        "LINE_TOTAL": {"2.99", "4.49"},
    }
    assert placed(result)[(1, 3)] == "LINE_TOTAL"
    assert placed(result)[(2, 3)] == "LINE_TOTAL"


def test_mismatch_mints_nothing():
    words = row(1, 0.10, "ORGANIC", "BANANAS", "2.99")
    result = derive_labels(words, {1}, {"subtotal": 99.00})

    assert result.gate == GATE_NOT_MATCHED
    assert result.reconciliation_status == "mismatch"
    assert result.labels == []


def test_no_baseline_mints_nothing():
    words = row(1, 0.10, "ORGANIC", "BANANAS", "2.99")
    result = derive_labels(words, {1}, None)

    assert result.gate == GATE_NOT_MATCHED
    assert result.reconciliation_status == "no-baseline"
    assert result.labels == []


def test_near_never_qualifies():
    # 2.99 against a 3.05 baseline is "near", not "match".
    words = row(1, 0.10, "ORGANIC", "BANANAS", "2.99")
    result = derive_labels(words, {1}, {"subtotal": 3.05})

    assert result.reconciliation_status == "near"
    assert result.gate == GATE_NOT_MATCHED
    assert result.labels == []


def test_empty_items_section_mints_nothing():
    words = row(1, 0.10, "ORGANIC", "BANANAS", "2.99")
    result = derive_labels(words, set(), {"subtotal": 2.99})

    assert result.gate == GATE_NO_ITEMS_SECTION
    assert result.labels == []


def test_name_words_without_letters_are_skipped():
    # The trailing "1" is a quantity the decoder folded into the name; a
    # bare numeric token is never descriptive text.
    words = row(1, 0.10, "Large", "Popcorn", "1", "12.99")
    result = derive_labels(words, {1}, {"subtotal": 12.99})

    assert result.gate == GATE_OK
    assert by_label(result)["PRODUCT_NAME"] == {"Large", "Popcorn"}
    assert (1, 3) not in placed(result)


def test_discount_row_takes_discount_not_line_total():
    words = (
        row(1, 0.10, "ORGANIC", "BANANAS", "5.00")
        + row(2, 0.15, "MEMBER", "SAVINGS", "-1.00")
        + row(3, 0.20, "WHOLE", "MILK", "4.00")
    )
    result = derive_labels(words, {1, 2, 3}, {"subtotal": 9.00})

    assert result.gate == GATE_OK
    labels = by_label(result)
    assert labels["DISCOUNT"] == {"-1.00"}
    assert labels["LINE_TOTAL"] == {"5.00", "4.00"}
    # A discount row's words are not a product name.
    assert "MEMBER" not in labels["PRODUCT_NAME"]


def test_quantity_and_unit_price_split_by_value():
    words = row(1, 0.10, "SODA", "2", "@", "1.50", "3.00")
    result = derive_labels(words, {1}, {"subtotal": 3.00})

    assert result.gate == GATE_OK
    labels = by_label(result)
    assert labels["QUANTITY"] == {"2"}
    assert labels["UNIT_PRICE"] == {"1.50"}
    assert labels["LINE_TOTAL"] == {"3.00"}
    assert labels["PRODUCT_NAME"] == {"SODA"}


def test_leading_quantity_word_is_labelled():
    words = row(1, 0.10, "3", "ORGANIC", "BANANAS", "6.00")
    result = derive_labels(words, {1}, {"subtotal": 6.00})

    assert result.gate == GATE_OK
    assert placed(result)[(1, 1)] == "QUANTITY"
    assert by_label(result)["PRODUCT_NAME"] == {"ORGANIC", "BANANAS"}


def test_printed_summary_figures_are_labelled():
    words = (
        row(1, 0.10, "ORGANIC", "BANANAS", "2.99")
        + row(2, 0.20, "SUBTOTAL", "2.99")
        + row(3, 0.25, "TAX", "0.21")
        + row(4, 0.30, "TOTAL", "3.20")
    )
    result = derive_labels(
        words, {1}, {"subtotal": 2.99, "tax": 0.21, "grand_total": 3.20}
    )

    assert result.gate == GATE_OK
    assert placed(result)[(2, 2)] == "SUBTOTAL"
    assert placed(result)[(3, 2)] == "TAX"
    assert placed(result)[(4, 2)] == "GRAND_TOTAL"


def trader_joes_restated_total():
    """The Trader Joe's two-column layout, in receipt coordinates.

    The amount column is printed as its own OCR lines, paired with the
    descriptive text by vertical position: "Balance to pay" on the store
    copy and "TOTAL PURCHASE" on the card slip both anchor the same
    37.51. y decreases down the receipt (Vision's bottom-left origin).
    """
    return (
        row(1, 0.6745, "PORK", "AL", "PASTOR", "DICED")
        + row(20, 0.6842, "$37.51")
        + row(15, 0.5202, "Items", "in", "Transaction:", "10")
        + row(25, 0.5113, "$37.51")
        + row(16, 0.4976, "Balance", "to", "pay")
        + row(26, 0.4895, "$37.51")
        + row(30, 0.3029, "TOTAL", "PURCHASE")
        + row(37, 0.3142, "$37.51")
    )


def test_restated_grand_total_elects_one_canonical_copy():
    # Three words print 37.51. "Items in Transaction" is not a
    # grand-total anchor at all; "Balance to pay" and "TOTAL PURCHASE"
    # both are. A receipt has exactly one grand total, so exactly one
    # word is labelled -- the copy dedupe_grand_total keeps.
    result = derive_labels(
        trader_joes_restated_total(), {1, 20}, {"grand_total": 37.51}
    )

    assert result.gate == GATE_OK
    assert by_label(result)["GRAND_TOTAL"] == {"$37.51"}
    grand = [p for p in result.labels if p.label == "GRAND_TOTAL"]
    assert len(grand) == 1
    assert grand[0].word_key == (37, 1)


def test_amount_beside_a_non_total_row_is_never_the_grand_total():
    # The 37.51 paired with "Items in Transaction: 10" is a column
    # neighbour, not a total: no anchor, no label, election or not.
    result = derive_labels(
        trader_joes_restated_total(), {1, 20}, {"grand_total": 37.51}
    )
    assert (25, 1) not in placed(result)


def test_two_total_copies_on_one_row_mint_nothing():
    # Both copies sit on the same visual row, so no row-ordering rule
    # can tell them apart. Election declines rather than guessing.
    words = row(1, 0.60, "ORGANIC", "BANANAS", "37.51") + row(
        2, 0.30, "TOTAL", "37.51", "37.51"
    )
    result = derive_labels(words, {1}, {"grand_total": 37.51})

    assert result.gate == GATE_OK
    assert "GRAND_TOTAL" not in by_label(result)


def test_restated_subtotal_stays_ambiguous():
    # Only GRAND_TOTAL has a canonical-copy election rule. A subtotal
    # printed twice still mints nothing.
    words = (
        row(1, 0.60, "ORGANIC", "BANANAS", "2.99")
        + row(2, 0.40, "SUBTOTAL", "2.99")
        + row(3, 0.30, "Sub", "Total", "2.99")
    )
    result = derive_labels(words, {1}, {"subtotal": 2.99})

    assert result.gate == GATE_OK
    assert "SUBTOTAL" not in by_label(result)


def test_summary_word_disagreeing_with_the_summary_is_not_labelled():
    words = row(1, 0.10, "ORGANIC", "BANANAS", "2.99") + row(
        2, 0.20, "TOTAL", "9.99"
    )
    result = derive_labels(words, {1}, {"subtotal": 2.99, "grand_total": 3.20})

    assert result.gate == GATE_OK
    assert "GRAND_TOTAL" not in by_label(result)


def test_one_word_never_gets_two_labels():
    words = row(1, 0.10, "ORGANIC", "BANANAS", "2.99") + row(
        2, 0.20, "SUBTOTAL", "2.99"
    )
    result = derive_labels(words, {1}, {"subtotal": 2.99})

    counted = {}
    for proposal in result.labels:
        counted[proposal.word_key] = counted.get(proposal.word_key, 0) + 1
    assert set(counted.values()) == {1}


@pytest.mark.parametrize(
    "bank_amount,expected_gate",
    [(3.20, GATE_OK), (3.21, GATE_NOT_PROVEN), (None, GATE_NOT_PROVEN)],
)
def test_require_proven_needs_the_bank_hop(bank_amount, expected_gate):
    words = row(1, 0.10, "ORGANIC", "BANANAS", "2.99")
    result = derive_labels(
        words,
        {1},
        {"subtotal": 2.99, "grand_total": 3.20},
        require_proven=True,
        bank_amount=bank_amount,
    )
    assert result.gate == expected_gate


TRADER_JOES_PLACE = {
    "formatted_address": "2716 N Green Valley Pkwy, Henderson, NV 89014, USA"
}


def header_receipt(*extra_rows):
    """A reconciling one-item receipt plus whatever header rows a test
    wants to put in front of it."""
    return row(1, 0.60, "ORGANIC", "BANANAS", "2.99") + [
        word for extra in extra_rows for word in extra
    ]


def test_address_lines_match_the_place_row_through_ocr_damage():
    # Places says "2716 N Green Valley Pkwy"; the receipt prints
    # "North" and OCR breaks "Parkway" into "Par" + "way". The line is
    # still the address, and the whole line is labelled.
    words = header_receipt(
        row(3, 0.85, "2716", "North", "Green", "Valley", "Par", "way"),
        row(4, 0.83, "Henderson,", "NV"),
        row(5, 0.82, "89014"),
    )
    result = derive_labels(
        words, {1}, {"subtotal": 2.99}, place=TRADER_JOES_PLACE
    )

    assert by_label(result)["ADDRESS_LINE"] == {
        "2716",
        "North",
        "Green",
        "Valley",
        "Par",
        "way",
        "Henderson,",
        "NV",
        "89014",
    }


def test_no_place_row_means_no_address_label():
    words = header_receipt(row(3, 0.85, "2716", "North", "Green", "Valley"))
    result = derive_labels(words, {1}, {"subtotal": 2.99})

    assert "ADDRESS_LINE" not in by_label(result)


def test_address_of_a_different_merchant_is_declined():
    words = header_receipt(row(3, 0.85, "4001", "South", "Rainbow", "Blvd"))
    result = derive_labels(
        words, {1}, {"subtotal": 2.99}, place=TRADER_JOES_PLACE
    )

    assert "ADDRESS_LINE" not in by_label(result)


def test_address_line_carrying_a_foreign_token_is_declined():
    # A phone number sharing the address row would be swallowed by a
    # whole-line label, so the whole line declines instead.
    words = header_receipt(
        row(3, 0.85, "2716", "Green", "Valley", "702-433-6773")
    )
    result = derive_labels(
        words, {1}, {"subtotal": 2.99}, place=TRADER_JOES_PLACE
    )

    assert "ADDRESS_LINE" not in by_label(result)


def test_address_is_found_wherever_it_is_printed():
    # The header is not always the geometric top line -- some receipts
    # read bottom-origin, and the address can print in the footer.
    words = header_receipt(row(48, 0.04, "Henderson,", "NV", "89014"))
    result = derive_labels(
        words, {1}, {"subtotal": 2.99}, place=TRADER_JOES_PLACE
    )

    assert by_label(result)["ADDRESS_LINE"] == {"Henderson,", "NV", "89014"}


def test_phone_number_spans_the_words_it_is_split_across():
    words = header_receipt(
        row(6, 0.81, "Store", "#0097", "-", "702", "433-6773")
    )
    result = derive_labels(
        words, {1}, {"subtotal": 2.99}, place=TRADER_JOES_PLACE
    )

    assert by_label(result)["PHONE_NUMBER"] == {"702", "433-6773"}
    assert "Store" not in by_label(result).get("PHONE_NUMBER", set())


def test_phone_number_missing_a_digit_is_declined():
    # OCR dropped a digit: "702 433-67 3" is nine digits, not a phone
    # number, and repairing it would be a guess.
    words = header_receipt(
        row(6, 0.81, "Store", "#0097", "-", "702", "433-67", "3")
    )
    result = derive_labels(
        words, {1}, {"subtotal": 2.99}, place=TRADER_JOES_PLACE
    )

    assert "PHONE_NUMBER" not in by_label(result)


def test_two_unverified_phone_numbers_mint_neither():
    words = header_receipt(
        row(6, 0.81, "702-433-6773"), row(7, 0.79, "702-555-0100")
    )
    result = derive_labels(
        words, {1}, {"subtotal": 2.99}, place=TRADER_JOES_PLACE
    )

    assert "PHONE_NUMBER" not in by_label(result)


def test_place_phone_picks_the_merchant_number_out_of_several():
    place = dict(TRADER_JOES_PLACE, phone_number="(702) 433-6773")
    words = header_receipt(
        row(6, 0.81, "702-433-6773"), row(7, 0.79, "702-555-0100")
    )
    result = derive_labels(words, {1}, {"subtotal": 2.99}, place=place)

    assert by_label(result)["PHONE_NUMBER"] == {"702-433-6773"}


def test_masked_pan_matches_the_summary_last_four():
    # The merchant id (five trailing digits) and the terminal id (wrong
    # four) are the two near-misses every card slip prints.
    words = header_receipt(
        row(29, 0.32, "MID:", "*******04690"),
        row(32, 0.38, "******|*****1454"),
        row(36, 0.34, "****0159"),
    )
    result = derive_labels(
        words, {1}, {"subtotal": 2.99, "card_last4": "1454"}
    )

    assert by_label(result)["PAYMENT_METHOD"] == {"******|*****1454"}


def test_masked_pan_is_never_labelled_with_a_non_core_alias():
    # CARD_NUMBER reads like the more precise label, but it is not in
    # CORE_LABELS -- the corpus normalizes it to PAYMENT_METHOD.
    words = header_receipt(row(32, 0.38, "******|*****1454"))
    result = derive_labels(
        words, {1}, {"subtotal": 2.99, "card_last4": "1454"}
    )

    assert "CARD_NUMBER" not in by_label(result)
    assert is_core_label("PAYMENT_METHOD")
    assert not is_core_label("CARD_NUMBER")


def test_masked_pan_needs_the_summary_to_say_which_card():
    words = header_receipt(row(32, 0.38, "******|*****1454"))
    result = derive_labels(words, {1}, {"subtotal": 2.99})

    assert "PAYMENT_METHOD" not in by_label(result)


def test_unmasked_trailing_digits_are_not_a_card():
    words = header_receipt(row(32, 0.38, "Ref", "1454"))
    result = derive_labels(
        words, {1}, {"subtotal": 2.99, "card_last4": "1454"}
    )

    assert "PAYMENT_METHOD" not in by_label(result)


def test_every_derived_label_is_a_core_label():
    words = (
        header_receipt(
            row(3, 0.85, "2716", "North", "Green", "Valley", "Par", "way"),
            row(6, 0.81, "Store", "#0097", "-", "702", "433-6773"),
            row(32, 0.38, "******|*****1454"),
        )
        + row(2, 0.40, "TOTAL", "2.99")[:]
    )
    result = derive_labels(
        words,
        {1},
        {"subtotal": 2.99, "grand_total": 2.99, "card_last4": "1454"},
        place=TRADER_JOES_PLACE,
    )

    assert result.labels
    for proposal in result.labels:
        assert is_core_label(proposal.label), proposal.label


@pytest.mark.parametrize(
    "item,expected",
    [
        (
            {
                "name": "SODA",
                "quantity": 2,
                "unit_price": 1.5,
                "qty_word_ids": [
                    {"line_id": 1, "word_id": 2},
                    {"line_id": 1, "word_id": 4},
                ],
            },
            {"QUANTITY": {"2"}, "UNIT_PRICE": {"1.50"}},
        ),
        # An item the decoder emitted without a quantity span at all --
        # the shape every decode has today, and the shape a decode that
        # simply could not read the quantity keeps.
        ({"name": "SODA"}, {}),
        # A quantity with no unit price still names the quantity word.
        (
            {
                "name": "SODA",
                "quantity": 2,
                "qty_word_ids": [{"line_id": 1, "word_id": 2}],
            },
            {"QUANTITY": {"2"}},
        ),
    ],
)
def test_quantity_span_contract_tolerates_a_decode_without_quantities(
    item, expected
):
    # Pinned against the decoder's item dict directly: whichever of
    # these fields a decode carries, the derivation reads what is there
    # and mints nothing for what is not.
    texts = {(1, 1): "SODA", (1, 2): "2", (1, 3): "@", (1, 4): "1.50"}
    labels = _quantity_labels(item, texts)

    by_type = {}
    for proposal in labels:
        by_type.setdefault(proposal.label, set()).add(proposal.text)
    assert by_type == expected


def qty_span_item(**overrides):
    """The decoder's item dict for a "6 @ 0.49" span."""
    return {
        "name": "LEMON EACH",
        "quantity": 6.0,
        "unit_price": 0.49,
        "qty_word_ids": [
            {"line_id": 14, "word_id": 1},
            {"line_id": 14, "word_id": 2},
            {"line_id": 14, "word_id": 3},
        ],
        **overrides,
    }


def qty_types(labels):
    out = {}
    for proposal in labels:
        out.setdefault(proposal.label, set()).add(proposal.text)
    return out


def test_unit_price_survives_an_ocr_mangled_currency_glyph():
    # IMG_3404 line 14 reads "6 8 S0.49": Vision rendered "$" as "S"
    # and "@" as "8". The decoder proved 6 x 0.49 = 2.94 against the
    # printed price before emitting unit_price, so refusing the word
    # for its glyphs would discard a settled fact.
    texts = {(14, 1): "6", (14, 2): "8", (14, 3): "S0.49"}
    labels = qty_types(_quantity_labels(qty_span_item(), texts))

    assert labels["UNIT_PRICE"] == {"S0.49"}
    assert labels["QUANTITY"] == {"6"}


def test_tax_flagged_unit_price_is_read_too():
    texts = {(14, 1): "6", (14, 2): "@", (14, 3): "0.49T"}
    labels = qty_types(_quantity_labels(qty_span_item(), texts))

    assert labels["UNIT_PRICE"] == {"0.49T"}


def test_well_formed_words_still_win_over_the_damaged_reading():
    # The tolerant reading is a fallback, never a widening: when a
    # well-formed word carries the value, the span is resolved by it
    # alone and a second damaged spelling cannot make it ambiguous.
    texts = {(14, 1): "6", (14, 2): "0.49", (14, 3): "S0.49"}
    labels = qty_types(_quantity_labels(qty_span_item(), texts))

    assert labels["UNIT_PRICE"] == {"0.49"}
    assert labels["QUANTITY"] == {"6"}


def test_bare_integers_are_never_read_as_a_unit_price():
    # A quantity equal to its own unit price ("2 @ 2.00") must not let
    # the bare "2" be claimed as the price word and strip QUANTITY.
    item = qty_span_item(quantity=2.0, unit_price=2.0, name="SODA")
    texts = {(14, 1): "2", (14, 2): "@", (14, 3): "2.00"}
    labels = qty_types(_quantity_labels(item, texts))

    assert labels["UNIT_PRICE"] == {"2.00"}
    assert labels["QUANTITY"] == {"2"}


def test_a_damaged_word_worth_a_different_amount_is_not_the_unit_price():
    # Acceptance never widens: the value must still equal the decoder's
    # proven unit price to the cent.
    texts = {(14, 1): "6", (14, 2): "8", (14, 3): "S0.59"}
    labels = qty_types(_quantity_labels(qty_span_item(), texts))

    assert "UNIT_PRICE" not in labels
    assert labels["QUANTITY"] == {"6"}


def test_two_damaged_words_worth_the_unit_price_mint_neither():
    texts = {(14, 1): "6", (14, 2): "S0.49", (14, 3): "s0.49"}
    labels = qty_types(_quantity_labels(qty_span_item(), texts))

    assert "UNIT_PRICE" not in labels


def test_negative_amounts_are_left_to_the_strict_reading():
    # The damaged reading must never drop a sign and turn a credit into
    # a charge; "-0.49" is well formed and parses with its sign.
    texts = {(14, 1): "6", (14, 2): "8", (14, 3): "-0.49"}
    labels = qty_types(_quantity_labels(qty_span_item(), texts))

    assert "UNIT_PRICE" not in labels


def test_ocr_damaged_reading_is_scoped_to_the_decoder_span():
    # The strict helper is untouched, so nothing outside the
    # arithmetically-backed span starts accepting "S0.49" as money.
    assert _amount_of("S0.49") is None
    assert _amount_of("$0.49") == 0.49
    assert _ocr_damaged_amount_of("S0.49") == 0.49
    assert _ocr_damaged_amount_of("6") is None
    assert _ocr_damaged_amount_of("-0.49") is None


def test_quantity_labels_skip_cleanly_when_the_decode_has_no_quantity():
    # QUANTITY / UNIT_PRICE come from the decoder's quantity span. A
    # decode that never parsed one mints neither, and mints everything
    # else as usual.
    words = row(1, 0.60, "ORGANIC", "BANANAS", "2.99")
    result = derive_labels(words, {1}, {"subtotal": 2.99})

    labels = by_label(result)
    assert "QUANTITY" not in labels
    assert "UNIT_PRICE" not in labels
    assert labels["LINE_TOTAL"] == {"2.99"}


def test_proposed_by_marker_is_distinct_from_llm_proposers():
    assert DECODER_PROPOSED_BY == "decoder_reconciled"
    assert "llm" not in DECODER_PROPOSED_BY


def test_every_proposal_carries_reasoning():
    words = row(1, 0.10, "ORGANIC", "BANANAS", "2.99")
    result = derive_labels(words, {1}, {"subtotal": 2.99})
    assert result.labels
    for proposal in result.labels:
        assert proposal.reasoning.strip()


# --- Column-header / boilerplate item names --------------------------------
#
# The band-block decoder names an item after a column-header row
# ("Unit Price 25.00") or footer legalese often enough to matter: 22 of
# 4730 decoded items across dev and prod, 18 of them on receipts that
# reconcile to their printed baseline as a full match, so the arithmetic
# gate passes them straight into the training corpus.
#
# They are NOT dropped, here or in the decoder. The corpus sweep measured
# 20 of those 22 load-bearing: their receipts balance WITH the item
# included, and removing one flips the receipt out of `match`, costing the
# whole receipt every label it would otherwise contribute. The price is
# real; only the name is wrong. So the derivation abstains on the name.


def test_column_header_name_mints_no_product_name():
    # "Unit Price 25.00" is one printed row: header words beside a real
    # price. Rise Henderson prints five of them on one receipt.
    words = row(1, 0.10, "Unit", "Price", "25.00")
    result = derive_labels(words, {1}, {"subtotal": 25.00})

    assert result.gate == GATE_OK
    assert result.reconciliation_status == "match"
    assert by_label(result) == {}


def test_header_row_price_word_is_not_a_line_total_either():
    # The receipt's own word for this amount is "ORIGINAL PRICE" -- it is
    # the pre-markdown price, printed beside a differently-priced item.
    # Minting LINE_TOTAL on it would teach the model that a header row's
    # amount is an extended total, on the strength of a sum that lands.
    #
    # Shape taken from CVS 5a7b884a, which prints exactly this: a $49.89
    # item with its $49.99 pre-coupon price on the next row. The
    # annotation is now recognized by SALE_PRICE_RE, so it never becomes
    # an item at all -- a stronger form of the same guarantee, and the
    # real item beside it keeps its own labels.
    words = row(1, 0.10, "PLAN", "B", "ONE", "STEP", "49.89") + row(
        2, 0.15, "ORIGINAL", "PRICE", "49.99"
    )
    result = derive_labels(words, {1, 2}, {"subtotal": 49.89})

    assert result.gate == GATE_OK
    assert by_label(result).get("LINE_TOTAL") == {"49.89"}
    assert "49.99" not in {p.text for p in result.labels}


def test_header_name_borrowed_from_another_row_keeps_its_line_total():
    # Here the header is its own row and the price row carries a real
    # name, so the decoder stacks them and the header contaminates the
    # NAME only. The price word was printed beside "EXTRASTRTHSCT" and is
    # a genuine extended total, so it keeps LINE_TOTAL.
    words = row(1, 0.30, "Description", "Qty", "Amount") + row(
        2, 0.10, "T", "EXTRASTRTHSCT", "3.59"
    )
    result = derive_labels(words, {1, 2}, {"subtotal": 3.59})

    assert result.gate == GATE_OK
    labels = by_label(result)
    assert labels.get("LINE_TOTAL") == {"3.59"}
    named = labels.get("PRODUCT_NAME", set())
    assert "Description" not in named
    assert "Amount" not in named


def test_real_items_keep_their_names_beside_a_header_item():
    # Abstention is per item, not per receipt: a header-named item must
    # not cost its neighbours their labels.
    words = (
        row(1, 0.30, "Item", "Qty", "Price", "6.00")
        + row(2, 0.20, "ORGANIC", "BANANAS", "2.99")
        + row(3, 0.10, "WHOLE", "MILK", "4.49")
    )
    result = derive_labels(words, {1, 2, 3}, {"subtotal": 13.48})

    assert result.gate == GATE_OK
    labels = by_label(result)
    assert labels["PRODUCT_NAME"] == {
        "ORGANIC",
        "BANANAS",
        "WHOLE",
        "MILK",
    }
    assert labels["LINE_TOTAL"] == {"2.99", "4.49"}


def test_a_header_named_item_still_counts_toward_reconciliation():
    # The whole point: the item stays in the decode and keeps the receipt
    # balancing. If it were dropped the sum would miss and the gate would
    # close on every other item too.
    words = row(1, 0.30, "Unit", "Price", "25.00") + row(
        2, 0.10, "ORGANIC", "BANANAS", "2.99"
    )
    result = derive_labels(words, {1, 2}, {"subtotal": 27.99})

    assert result.gate == GATE_OK
    assert result.item_count == 2
    assert result.item_sum == pytest.approx(27.99)
    assert by_label(result)["PRODUCT_NAME"] == {"ORGANIC", "BANANAS"}
