"""Unit tests for the dry-run section-seed projection + report logic.

Pure functions over synthetic words — no Dynamo, no pixels.
"""

from glyphstudio.section_seeds import (
    SeedReport,
    SeedWord,
    line_section,
    merchant_slug,
    seed_receipt,
    word_label_section,
)


def test_merchant_slug():
    assert merchant_slug("Sprouts Farmers Market") == "sprouts"
    assert merchant_slug("  COSTCO Wholesale ") == "costco"
    assert merchant_slug("Trader Joe's") == "traderjoes"
    assert merchant_slug("Unknown Bodega") is None
    assert merchant_slug("") is None


def test_word_label_section_high_only_by_default():
    # GRAND_TOTAL is high -> seeds
    assert word_label_section(["GRAND_TOTAL"]) == "total_line"
    # DISCOUNT is medium -> not seeded by default
    assert word_label_section(["DISCOUNT"]) is None
    assert word_label_section(["DISCOUNT"], include_medium=True) == "summary"
    # unmapped labels -> None
    assert word_label_section(["DATE"]) is None
    assert word_label_section([]) is None


def test_word_label_section_prefers_high_over_medium():
    # a word carrying both a high and a medium label resolves to the high one
    assert (
        word_label_section(["CHANGE", "GRAND_TOTAL"], include_medium=True)
        == "total_line"
    )


def test_line_section_uses_stylescan_rules():
    line = [SeedWord(5, 0, "SUBTOTAL"), SeedWord(5, 1, "12.99")]
    assert line_section(line, "sprouts") == "summary"
    # Costco warehouse header folds to storefront
    hdr = [SeedWord(0, 0, "Costco"), SeedWord(0, 1, "#1234")]
    assert line_section(hdr, "costco") == "storefront"
    # a priced unruled line -> items
    item = [SeedWord(9, 0, "BANANAS"), SeedWord(9, 1, "1.29")]
    assert line_section(item, "sprouts") == "items"


def test_seed_receipt_label_wins_over_stylescan():
    # word carries a high label (GRAND_TOTAL->total_line); its line text also
    # matches a stylescan rule. Source A (label) must win section_final.
    words = [
        SeedWord(1, 0, "TOTAL"),
        SeedWord(1, 1, "45.00", labels=("GRAND_TOTAL",)),
    ]
    seeds = seed_receipt(words, "sprouts")
    by_word = {(s.line_id, s.word_id): s for s in seeds}
    labeled = by_word[(1, 1)]
    assert labeled.section_label == "total_line"
    assert labeled.section_final == "total_line"
    assert labeled.source == "label"
    # the unlabeled word on the same line inherits the line's stylescan section
    plain = by_word[(1, 0)]
    assert plain.section_label is None
    assert plain.source == "stylescan"
    assert plain.section_final == plain.section_style


def test_report_coverage_and_agreement():
    words = [
        # line 1: SUBTOTAL row. word "SUBTOTAL" has a VALID SUBTOTAL label
        # (->summary) AND stylescan classifies the line summary -> agree.
        SeedWord(1, 0, "SUBTOTAL", labels=("SUBTOTAL",)),
        SeedWord(1, 1, "10.00"),
        # line 2: item line, no labels -> covered by stylescan (items).
        SeedWord(2, 0, "APPLE"),
        SeedWord(2, 1, "2.50"),
        # line 3: an unruled, unlabeled word -> no section (uncovered).
        SeedWord(3, 0, "xyzzy"),
    ]
    seeds = seed_receipt(words, "sprouts")
    rep = SeedReport(merchant="sprouts")
    rep.add_receipt(seeds, image_id="img", receipt_id=1)

    assert rep.total_words == 5
    assert rep.covered_words == 4  # all but "xyzzy"
    assert abs(rep.coverage - 0.8) < 1e-9
    # SUBTOTAL word: label(summary) and style(summary) both present + agree
    assert rep.both_present == 1
    assert rep.both_agree == 1
    assert abs(rep.agreement - 1.0) < 1e-9
    d = rep.to_dict()
    assert d["per_section"]["summary"] >= 1
    assert d["per_section"]["items"] == 2


def test_report_records_disagreement():
    # a word mislabeled PAYMENT_METHOD (->payment) on a line stylescan reads as
    # summary should be recorded as a disagreement for QA.
    words = [
        SeedWord(1, 0, "SUBTOTAL", labels=("PAYMENT_METHOD",)),
        SeedWord(1, 1, "10.00"),
    ]
    seeds = seed_receipt(words, "sprouts")
    rep = SeedReport(merchant="sprouts")
    rep.add_receipt(seeds, image_id="img", receipt_id=1)
    assert rep.both_present == 1
    assert rep.both_agree == 0
    assert rep.disagreements and rep.disagreements[0]["label_section"] == "payment"
    assert rep.disagreements[0]["style_section"] == "summary"
