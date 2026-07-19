"""Unit tests for the Dollar Tree canonical composer (PR #1187 review)."""

from __future__ import annotations

import os
import sys

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "synthesis_loop")
)

from compose_dollartree import _norm_price, canonical_words  # noqa: E402


def test_norm_price_parses_shattered_tokens_with_flag():
    assert _norm_price("1251") == ("1.25", True)
    assert _norm_price("1.251") == ("1.25", True)
    assert _norm_price("1.25T") == ("1.25", True)
    assert _norm_price("1.25") == ("1.25", False)
    assert _norm_price(".52") == ("0.52", False)
    assert _norm_price("junk") is None


def _word(text, x0, y, w=40, line_id=1, labels=None):
    return {
        "text": text,
        "bbox": [x0, y, x0 + w, y + 20],
        "labels": labels or [],
        "line_id": line_id,
        "word_id": 1,
    }


def test_quantity_arithmetic_never_copies_totals():
    words = [
        _word("DESCRIPTION", 0, 800, line_id=1),
        _word("SCRUB BRUSH THING", 10, 700, line_id=2),
        _word("2", 620, 702, w=15, line_id=3),
        _word("1.25", 720, 701, line_id=4),
    ]
    out = canonical_words(words)
    texts = [w["text"] for w in out]
    assert "2.50" in texts  # total = 1.25 x 2, not a bare copy
    assert "2.50T" not in texts  # no fabricated tax flag


def test_observed_tax_flag_is_preserved():
    words = [
        _word("DESCRIPTION", 0, 800, line_id=1),
        _word("SCRUB BRUSH THING", 10, 700, line_id=2),
        _word("1.25T", 920, 701, line_id=3),
    ]
    out = canonical_words(words)
    assert any(w["text"] == "1.25T" for w in out)


def test_long_receipts_scale_pitch_instead_of_clipping():
    words = [_word("DESCRIPTION", 0, 900, line_id=1)]
    for i in range(30):
        y = 850 - i * 25
        words.append(_word(f"ITEM NUMBER {i} THING", 10, y, line_id=2 + i))
        words.append(_word("1.25", 720, y + 1, line_id=100 + i))
    out = canonical_words(words)
    assert out and min(w["bbox"][1] for w in out) >= 0.0


def test_intact_rows_keep_their_amounts():
    words = [
        _word("DESCRIPTION", 0, 800, line_id=1),
        # one intact source line: desc + qty + price + total
        _word("SCRUB", 10, 700, line_id=2),
        _word("BRUSH", 60, 700, line_id=2),
        _word("2", 620, 700, w=15, line_id=2),
        _word("1.25", 720, 700, line_id=2),
        _word("2.50T", 920, 700, line_id=2),
    ]
    out = canonical_words(words)
    texts = [w["text"] for w in out]
    assert "SCRUB" in texts and "BRUSH" in texts
    assert "2.50T" in texts  # total kept, observed flag kept
    assert "1.25" in texts


def test_same_line_tax_summary_survives():
    words = [
        _word("DESCRIPTION", 0, 800, line_id=1),
        _word("ITEM ONE THING", 10, 700, line_id=2),
        _word("1.25T", 920, 701, line_id=3),
        _word("SALES", 400, 500, line_id=4),
        _word("TAX", 460, 500, line_id=4),
        _word("0.52", 830, 500, line_id=4),
    ]
    out = canonical_words(words)
    texts = [w["text"] for w in out]
    assert "SALES" in texts and "TAX" in texts
    assert "$0.52" in texts  # summary amounts carry the template $ prefix


def test_sixty_item_receipts_never_emit_below_canvas():
    words = [_word("DESCRIPTION", 0, 960, line_id=1)]
    for i in range(60):
        y = 940 - i * 15
        words.append(_word(f"ITEM NUMBER {i} THING", 10, y, line_id=2 + i))
        words.append(_word("1.25", 720, y + 1, line_id=100 + i))
    out = canonical_words(words)
    assert out and min(w["bbox"][1] for w in out) >= 0.0


def test_modal_price_reconciles_fragments_that_lost_their_leading_digit():
    words = [
        _word("DESCRIPTION", 0, 800, line_id=1),
        _word("ITEM ONE THING", 10, 700, line_id=2),
        _word("1.25T", 920, 701, line_id=3),  # clean total
        _word("ITEM TWO THING", 10, 660, line_id=4),
        _word(".25T", 920, 661, line_id=5),  # lost leading digit
        _word("ITEM SIX THING", 10, 620, line_id=6),
        _word("9.99T", 920, 621, line_id=7),  # distinct cents: untouched
    ]
    out = canonical_words(words)
    totals = [w["text"] for w in out if w["text"].endswith("T")]
    assert totals.count("1.25T") == 2  # damaged .25T repaired to modal
    assert "9.99T" in totals  # different cents never touched


def test_summary_arithmetic_reconciles_damaged_tax_and_total():
    words = [
        _word("DESCRIPTION", 0, 800, line_id=1),
        _word("ITEM ONE THING", 10, 700, line_id=2),
        _word("1.25T", 920, 701, line_id=3),
        _word("Sub", 400, 500, line_id=4),
        _word("Total", 440, 500, line_id=4),
        _word("13.75", 830, 500, line_id=5),
        _word("SALES", 400, 460, line_id=6),
        _word("TAX", 460, 460, line_id=6),
        _word(".15", 830, 460, line_id=7),  # lost the leading 1 (true 1.15)
        _word("Total", 400, 420, line_id=8),
        _word(".90", 830, 420, line_id=9),  # lost 14. (true 14.90)
        _word("American", 400, 380, line_id=10),
        _word("Expres", 470, 380, line_id=10),
        _word("14.90", 830, 380, line_id=11),
    ]
    out = canonical_words(words)
    texts = [w["text"] for w in out]
    assert "$1.15" in texts  # tender - subtotal, cents preserved ($ prefix)
    assert texts.count("$14.90") == 2  # Total repaired to the tender amount


def test_emit_never_exceeds_canvas_top_or_bottom():
    words = [
        _word("DESCRIPTION", 0, 800, line_id=1),
        _word("ITEM ONE THING", 10, 700, line_id=2),
        _word("1.25T", 920, 701, line_id=3),
    ]
    out = canonical_words(words)
    assert max(w["bbox"][3] for w in out) <= 1000.0
    assert min(w["bbox"][1] for w in out) >= 0.0


def test_merge_dedupe_only_at_seam():
    words = [
        _word("DESCRIPTION", 0, 800, line_id=1),
        # same visual row split at the seam with an overlap token
        _word("STORAGE GREY", 10, 700, line_id=2),
        _word("GREY CLLPSB 11X10", 300, 703, line_id=3),
        _word("1.25T", 920, 701, line_id=4),
        # legitimate repetition elsewhere must survive
        _word("DOG FOOD", 10, 660, line_id=5),
        _word("DOG BOWL", 250, 662, line_id=6),
        _word("1.25T", 920, 661, line_id=7),
    ]
    out = canonical_words(words)
    texts = [w["text"] for w in out]
    assert texts.count("GREY") == 1  # seam overlap deduped
    assert texts.count("DOG") == 2  # non-seam repetition preserved


# ---- DT_TRUTH_ROUND failing-first tests -----------------------------------


def test_cross_line_suffix_attaches_within_measured_pitch():
    # Ground truth #1: size suffixes exist in OCR at x~0.38 on a NEIGHBORING
    # line_id, dy ~ 0.010 (beyond the old 0.008 window). The suffix must
    # join its item row, not be dropped.
    words = [
        _word("DESCRIPTION", 0, 800, line_id=1),
        _word("CLLPSBLE STORAGE GREY", 10, 700, line_id=2),
        _word("11X10.", 380, 710, line_id=3),  # dy=0.010, desc-tail x
        _word("1.25T", 920, 701, line_id=4),
    ]
    out = canonical_words(words)
    texts = [w["text"] for w in out]
    assert "11X10." in texts or "11X10.5" in texts


def test_duplicate_items_vote_text_canonicalization():
    # Ground truth #1: identical items vote per token; the clipped "11X10."
    # rows inherit "11X10.5" from the clean instances (receipt-derived only).
    words = [_word("DESCRIPTION", 0, 800, line_id=1)]
    ys = [700, 660, 620, 580]
    suffixes = ["11X10.5", "11X10.5", "11X10.5", "11X10."]
    for i, (y, sfx) in enumerate(zip(ys, suffixes)):
        words.append(
            _word(f"CLLPSBLE STORAGE GREY {sfx}", 10, y, line_id=2 + i)
        )
        words.append(_word("1.25T", 920, y + 1, line_id=100 + i))
    out = canonical_words(words)
    texts = [w["text"] for w in out]
    assert texts.count("11X10.5") == 4  # clipped row inherits the majority
    assert "11X10." not in texts


def test_footer_register_and_associate_render_at_bottom_in_order():
    # Ground truth #1: register tokens (incl. alnum '501454B6') live at
    # y~0.09 and 'sociate: isabella' exists at y~0.03 -- both must emit, in
    # template order (register above associate), BELOW the policy box.
    words = [
        _word("DESCRIPTION", 0, 800, line_id=1),
        _word("ITEM ONE THING", 10, 700, line_id=2),
        _word("1.25T", 920, 701, line_id=3),
        _word("7534", 30, 88, line_id=4),
        _word("501454B6", 200, 88, line_id=5),
        _word("9/17/22 17:08", 500, 88, line_id=6),
        _word("sociate: isabella", 140, 33, line_id=7),
    ]
    out = canonical_words(words)
    texts = [w["text"] for w in out]
    assert "501454B6" in texts, "alnum register token dropped"
    assert any("isabella" in t for t in texts), "associate line dropped"
    reg_y = next(w["bbox"][1] for w in out if w["text"] == "501454B6")
    assoc_y = next(w["bbox"][1] for w in out if "isabella" in w["text"])
    star_ys = [w["bbox"][1] for w in out if w["text"].startswith("****")]
    assert reg_y < min(star_ys), "register must render below the policy box"
    assert assoc_y < reg_y, "associate renders after the register line"


def test_generative_template_regeneration_structure():
    # Acceptance (DT_TRUTH_ROUND step 4d): the template's own content, fed
    # through the generative path, reproduces the template's structure.
    import compose_dollartree as cd

    fx = cd.load_fixture()
    words = cd.generate_receipt(
        fx["items"],
        tax_rate=fx["tax_rate"],
        tender_label=fx["summary"]["tender_label"],
    )
    texts = [w["text"] for w in words]
    # 24 qty-1 item lines, arithmetic identical to the printed template
    assert texts.count("1.00T") == 24
    assert "$24.00" in texts and "$1.44" in texts
    assert texts.count("$25.44") == 2

    # separator inventory: 3 double rules, 2 dashed, 2 star borders
    def _rules(ch):
        return sum(1 for t in texts if t.startswith(ch * 4) and len(t) > 40)

    assert _rules("=") == 3
    assert _rules("-") == 2
    assert _rules("*") == 2  # box borders (the masked PAN is shorter)
    # section ORDER: header -> rules -> colhdr -> items -> summary ->
    # payment -> rule -> narration -> box -> register -> associate -> rule
    idx = {t: texts.index(t) for t in ("DESCRIPTION", "Sub", "Purchase")}
    assert idx["DESCRIPTION"] < texts.index("1.00T")
    assert texts.index("1.00T") < idx["Sub"] < idx["Purchase"]
    assert texts.index("7534") > idx["Purchase"]
    assert any("Tommya" in t for t in texts)
    reg_i = texts.index("7534")
    assoc_i = next(i for i, t in enumerate(texts) if "Tommya" in t)
    assert assoc_i > reg_i


def test_vote_never_creates_chimeras_across_distinct_products():
    words = [_word("DESCRIPTION", 0, 800, line_id=1)]
    for i, name in enumerate(
        [
            "BAMBOO CHARCOAL TOOTHBRUSH",
            "BAMBOO CHARCOAL TOOTHBRUSH",
            "BAMBOO CHARCOAL SOAP",
        ]
    ):
        y = 700 - i * 40
        words.append(_word(name, 10, y, line_id=2 + i))
        words.append(_word("1.00T", 920, y + 1, line_id=100 + i))
    out = canonical_words(words)
    texts = [w["text"] for w in out]
    assert texts.count("SOAP") == 1  # distinct product keeps its identity
    assert texts.count("TOOTHBRUSH") == 2


def test_generated_tax_uses_half_up_cent_rounding():
    import compose_dollartree as cd

    words = cd.generate_receipt(
        [{"name": "SINGLE THING", "qty": 1, "price": 0.25, "taxable": True}],
        tax_rate=0.06,
    )
    texts = [w["text"] for w in words]
    assert "$0.02" in texts  # 0.015 rounds half-up, not banker's 0.01
