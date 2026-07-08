"""Unit tests for the dry-run section-seed projection + report logic.

Pure functions over synthetic words — no Dynamo, no pixels.
"""

import pytest

from glyphstudio.section_seeds import (
    SeedReport,
    SeedWord,
    known_stylescan_slugs,
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


def test_line_section_rejects_unknown_slug():
    line = [SeedWord(0, 0, "anything")]
    with pytest.raises(ValueError):
        line_section(line, "not_a_merchant")
    with pytest.raises(ValueError):
        seed_receipt([SeedWord(0, 0, "x")], "typo_slug")
    # sanity: the nine calibrated merchants are all known
    assert {"sprouts", "costco", "vons", "traderjoes", "cvs", "innout",
            "target", "wildfork", "homedepot"} <= known_stylescan_slugs()


def test_stylescan_overlap_fixes():
    # Costco thank-you footer no longer folds to storefront via self_checkout
    assert line_section([SeedWord(0, 0, "THANK YOU")], "costco") == "footer"
    assert (
        line_section([SeedWord(0, 0, "Please"), SeedWord(0, 1, "Come"),
                      SeedWord(0, 2, "Again")], "costco")
        == "footer"
    )
    # ...but the real lane header still classifies as storefront
    assert (
        line_section([SeedWord(0, 0, "SELF-CHECKOUT")], "costco")
        == "storefront"
    )
    # Wild Fork standalone total no longer folds to items via item_header
    assert (
        line_section([SeedWord(0, 0, "Total"), SeedWord(0, 1, "45.00")],
                     "wildfork")
        == "total_line"
    )
    assert (
        line_section([SeedWord(0, 0, "Total"), SeedWord(0, 1, "Tax"),
                      SeedWord(0, 2, "3.15")], "wildfork")
        == "summary"
    )
    # ...but the column header row still classifies to items
    assert (
        line_section([SeedWord(0, 0, "Item"), SeedWord(0, 1, "Qty"),
                      SeedWord(0, 2, "Price")], "wildfork")
        == "items"
    )


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


# --- aggregation to ReceiptSection specs ----------------------------------

from glyphstudio.section_seeds import (  # noqa: E402
    WordSeed,
    aggregate_line_sections,
    receipt_section_specs,
)


def _w(line_id, word_id, label=None, style=None):
    final = label if label is not None else style
    source = "label" if label is not None else ("stylescan" if style else None)
    return WordSeed(
        line_id=line_id,
        word_id=word_id,
        text="x",
        section_label=label,
        section_style=style,
        section_final=final,
        source=source,
    )


def test_aggregate_line_stylescan_only_base_confidence():
    seeds = [_w(1, 0, style="items"), _w(1, 1, style="items")]
    lines = aggregate_line_sections(seeds)
    assert len(lines) == 1
    assert lines[0].section == "items"
    assert lines[0].confidence == 0.60  # rule-only, no label corroboration


def test_aggregate_line_label_corroboration_lifts_confidence():
    # stylescan says summary; both labeled words agree -> 0.60 + 0.35*1.0
    seeds = [
        _w(2, 0, label="summary", style="summary"),
        _w(2, 1, label="summary", style="summary"),
    ]
    lines = aggregate_line_sections(seeds)
    assert lines[0].section == "summary"
    assert lines[0].confidence == 1.0  # label + stylescan agree


def test_aggregate_line_label_only_when_no_stylescan():
    seeds = [_w(3, 0, label="total_line"), _w(3, 1, label="total_line")]
    lines = aggregate_line_sections(seeds)
    assert lines[0].section == "total_line"
    # label-chosen, no stylescan corroboration: 0.70 + 0.15
    assert lines[0].confidence == 0.85


def test_aggregate_line_dropped_when_no_section():
    seeds = [_w(4, 0)]
    assert aggregate_line_sections(seeds) == []


def test_receipt_section_specs_groups_and_uppercases():
    seeds = [
        _w(1, 0, style="items"),
        _w(2, 0, style="items"),
        _w(3, 0, label="total_line", style="total_line"),
    ]
    specs = {s.section_type: s for s in receipt_section_specs(seeds)}
    assert set(specs) == {"ITEMS", "TOTAL_LINE"}
    assert specs["ITEMS"].line_ids == (1, 2)
    assert specs["TOTAL_LINE"].line_ids == (3,)
    # confidence is the mean of member-line confidences
    assert 0.0 < specs["ITEMS"].confidence <= 1.0


def test_label_only_withholds_stylescan_only_lines():
    seeds = [
        _w(1, 0, style="items"),                 # stylescan-only -> withheld
        _w(2, 0, label="total_line", style="total_line"),  # corroborated -> kept
        _w(3, 0, label="summary"),               # label-only -> kept
    ]
    full = {s.section_type for s in receipt_section_specs(seeds)}
    lo = {s.section_type for s in receipt_section_specs(seeds, label_only=True)}
    assert full == {"ITEMS", "TOTAL_LINE", "SUMMARY"}
    assert lo == {"TOTAL_LINE", "SUMMARY"}  # ITEMS (stylescan-only) withheld


def test_labels_win_the_line_on_disagreement():
    """A labeled word overrides the stylescan section for its whole line
    (consistent with per-word label-wins). This is the fix for line sections
    contradicting hand labels."""
    # word labeled total_line sits on a line stylescan reads as items
    seeds = [_w(1, 0, label="total_line", style="items")]
    lines = aggregate_line_sections(seeds)
    assert lines[0].section == "total_line"  # label wins, not "items"
    # label-chosen, stylescan disagrees -> 0.70 + 0.15*1.0 (no style bonus)
    assert lines[0].confidence == 0.85


def test_label_only_has_no_rule_only_rows():
    """label_only lines are all label-chosen -> confidence >= 0.70, never the
    0.60 rule-only floor (Fable's invariant)."""
    seeds = [
        _w(1, 0, style="items"),                       # rule-only -> withheld
        _w(2, 0, label="summary", style="items"),      # disagreement, label wins
        _w(3, 0, label="footer"),                      # label-only
    ]
    lines = aggregate_line_sections(seeds, label_only=True)
    assert all(ls.confidence >= 0.70 for ls in lines), lines
    assert {ls.section for ls in lines} == {"summary", "footer"}
