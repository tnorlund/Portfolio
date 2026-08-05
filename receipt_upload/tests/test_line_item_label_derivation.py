"""Unit tests for deterministic word-label derivation from the decode.

The gate is arithmetic: labels exist only when the decoded items sum to
the receipt's printed baseline exactly. These tests pin that gate, the
word-level placement of every label type, and the fail-closed rules
(ambiguous summary figures, name words with no letters, discount rows).
"""

import pytest

from receipt_upload.line_items.labels import (
    DECODER_PROPOSED_BY,
    GATE_NO_ITEMS_SECTION,
    GATE_NOT_MATCHED,
    GATE_NOT_PROVEN,
    GATE_OK,
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


def test_summary_figure_printed_twice_is_ambiguous():
    # "Balance to pay 37.51" and the terminal's "TOTAL PURCHASE 37.51"
    # are both grand-total anchors: picking one would be a guess.
    words = (
        row(1, 0.10, "ORGANIC", "BANANAS", "37.51")
        + row(2, 0.20, "Balance", "to", "pay", "37.51")
        + row(3, 0.30, "TOTAL", "PURCHASE", "37.51")
    )
    result = derive_labels(words, {1}, {"grand_total": 37.51})

    assert result.gate == GATE_OK
    assert "GRAND_TOTAL" not in by_label(result)


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


def test_proposed_by_marker_is_distinct_from_llm_proposers():
    assert DECODER_PROPOSED_BY == "decoder_reconciled"
    assert "llm" not in DECODER_PROPOSED_BY


def test_every_proposal_carries_reasoning():
    words = row(1, 0.10, "ORGANIC", "BANANAS", "2.99")
    result = derive_labels(words, {1}, {"subtotal": 2.99})
    assert result.labels
    for proposal in result.labels:
        assert proposal.reasoning.strip()
