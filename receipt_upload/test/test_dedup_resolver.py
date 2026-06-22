"""Unit tests for the deterministic merge resolver (survivor + VALID gap-fills)."""

from types import SimpleNamespace

from receipt_upload.dedup.context import LabelObs, build_merge_dossiers
from receipt_upload.dedup.resolver import resolve_dossier


def _r(image_id, receipt_id, sha, w=100, h=100):
    return SimpleNamespace(
        image_id=image_id, receipt_id=receipt_id, sha256=sha, width=w, height=h
    )


def _obs(label, line, word, text, status=None):
    return LabelObs(label, line, word, text, status)


def _resolve(receipts, words, labels):
    return resolve_dossier(build_merge_dossiers(receipts, words, labels)[0])


def test_survivor_label_wins_no_gap_fill_when_survivor_labels_word():
    # survivor a#1 labels the word; dropped copy disagrees -> survivor wins, no gap-fill
    receipts = [_r("a", 1, "S"), _r("a", 2, "S")]
    words = {("a", 1): {(1, 1): "M", (3, 4): "$5.90"}, ("a", 2): {(1, 1): "M", (3, 4): "$5.90"}}
    labels = {
        ("a", 1): [_obs("MERCHANT_NAME", 1, 1, "M", "VALID"), _obs("GRAND_TOTAL", 3, 4, "$5.90", "VALID")],
        ("a", 2): [_obs("SUBTOTAL", 3, 4, "$5.90", "VALID")],  # disagrees on 3:4
    }
    res = _resolve(receipts, words, labels)
    assert res.survivor == "a#1"
    assert res.gap_fills == []  # survivor already labels 3:4 -> not a gap
    assert res.action == "drop_redundant"


def test_valid_label_fills_survivor_gap():
    # survivor a#1 has no label on TAX word; dropped copy has VALID TAX -> gap-filled
    receipts = [_r("a", 1, "S"), _r("a", 2, "S")]
    words = {("a", 1): {(1, 1): "M", (2, 2): "TAX"}, ("a", 2): {(1, 1): "M", (2, 2): "TAX"}}
    labels = {
        ("a", 1): [_obs("MERCHANT_NAME", 1, 1, "M", "VALID"), _obs("DATE", 9, 9, "x", "VALID")],
        ("a", 2): [_obs("TAX", 2, 2, "TAX", "VALID")],
    }
    res = _resolve(receipts, words, labels)
    assert res.survivor == "a#1"
    assert len(res.gap_fills) == 1
    gf = res.gap_fills[0]
    assert gf.locus == "2:2" and gf.label == "TAX" and gf.from_member == "a#2"
    assert res.action == "consolidate_then_drop"


def test_invalid_label_never_resurrected_into_gap():
    # dropped copy's only label on the gap word is INVALID -> NOT filled
    receipts = [_r("a", 1, "S"), _r("a", 2, "S")]
    words = {("a", 1): {(1, 1): "M", (2, 2): "9.99"}, ("a", 2): {(1, 1): "M", (2, 2): "9.99"}}
    labels = {
        ("a", 1): [_obs("MERCHANT_NAME", 1, 1, "M", "VALID")],
        ("a", 2): [_obs("UNIT_PRICE", 2, 2, "9.99", "INVALID")],
    }
    res = _resolve(receipts, words, labels)
    assert res.gap_fills == []  # INVALID is deliberate, never resurrected
    assert res.action == "drop_redundant"


def test_gap_disagreement_left_unlabeled():
    # two dropped copies VALID-disagree on a survivor gap word -> skip, don't guess
    receipts = [_r("a", 1, "S"), _r("a", 2, "S"), _r("a", 3, "S")]
    words = {k: {(1, 1): "M", (2, 2): "9.99"} for k in [("a", 1), ("a", 2), ("a", 3)]}
    labels = {
        ("a", 1): [_obs("MERCHANT_NAME", 1, 1, "M", "VALID"), _obs("DATE", 8, 8, "x", "VALID"), _obs("TIME", 9, 9, "y", "VALID")],
        ("a", 2): [_obs("LINE_TOTAL", 2, 2, "9.99", "VALID")],
        ("a", 3): [_obs("UNIT_PRICE", 2, 2, "9.99", "VALID")],
    }
    res = _resolve(receipts, words, labels)
    assert res.survivor == "a#1"  # most labels
    assert res.gap_fills == []
    dis = [s for s in res.skipped_gaps if s["reason"] == "valid_disagreement"]
    assert len(dis) == 1
    assert set(dis[0]["candidates"]) == {"LINE_TOTAL", "UNIT_PRICE"}


def test_survivor_legacy_label_is_occupied_not_a_gap():
    # survivor labels $5.90 as legacy AMOUNT; a dropped VALID GRAND_TOTAL must NOT
    # be migrated over it (that would adjudicate the survivor's own label).
    receipts = [_r("a", 1, "S"), _r("a", 2, "S")]
    words = {("a", 1): {(1, 1): "M", (3, 4): "$5.90"}, ("a", 2): {(1, 1): "M", (3, 4): "$5.90"}}
    labels = {
        ("a", 1): [_obs("MERCHANT_NAME", 1, 1, "M", "VALID"),
                   _obs("DATE", 7, 7, "x", "VALID"),
                   _obs("AMOUNT", 3, 4, "$5.90", "VALID")],  # legacy -> occupies 3:4
        ("a", 2): [_obs("GRAND_TOTAL", 3, 4, "$5.90", "VALID")],
    }
    res = _resolve(receipts, words, labels)
    assert res.survivor == "a#1"
    assert all(gf.locus != "3:4" for gf in res.gap_fills)


def test_byte_identical_repeated_token_targeted_by_position():
    # byte-identical re-upload (same sha, different images): the dropped copy's
    # OWN (line:word) pins which occurrence of a repeated "$9.99" to fill, so it
    # is no longer ambiguous and IS recovered onto the survivor.
    receipts = [_r("a", 1, "S"), _r("b", 1, "S")]
    words = {
        ("a", 1): {(1, 1): "M", (2, 2): "$9.99", (5, 5): "$9.99"},
        ("b", 1): {(1, 1): "M", (2, 2): "$9.99", (5, 5): "$9.99"},
    }
    labels = {
        # survivor (2 validated) labels neither $9.99 occurrence
        ("a", 1): [_obs("MERCHANT_NAME", 1, 1, "M", "VALID"),
                   _obs("DATE", 9, 9, "x", "VALID")],
        ("b", 1): [_obs("LINE_TOTAL", 5, 5, "$9.99", "VALID")],  # the 5:5 one
    }
    res = _resolve(receipts, words, labels)
    assert res.survivor == "a#1"
    assert len(res.gap_fills) == 1
    assert res.gap_fills[0].locus == "5:5"  # exact occurrence from the drop
    assert res.gap_fills[0].label == "LINE_TOTAL"


def test_byte_identical_skips_when_position_absent_and_text_repeated():
    # safety guard: dropped copy labels a $9.99 at a position the survivor does
    # NOT have, and $9.99 is repeated in the survivor -> can't target -> skip.
    receipts = [_r("a", 1, "S"), _r("b", 1, "S")]
    words = {
        ("a", 1): {(1, 1): "M", (2, 2): "$9.99", (5, 5): "$9.99"},
        ("b", 1): {(1, 1): "M", (8, 8): "$9.99"},  # labeled occurrence at 8:8
    }
    labels = {
        ("a", 1): [_obs("MERCHANT_NAME", 1, 1, "M", "VALID"),
                   _obs("DATE", 7, 7, "x", "VALID")],
        ("b", 1): [_obs("LINE_TOTAL", 8, 8, "$9.99", "VALID")],
    }
    res = _resolve(receipts, words, labels)
    assert res.survivor == "a#1"
    assert res.gap_fills == []
    assert any(
        s["reason"] == "no_unique_survivor_target" for s in res.skipped_gaps
    )


def test_cross_image_gap_fill_targets_concrete_position():
    # unique token -> gap-fill carries the SURVIVOR's (line:word) target, not the
    # dropped copy's. survivor a#1 is unambiguous (2 validated labels).
    receipts = [_r("a", 1, "S"), _r("b", 1, "S")]
    words = {
        ("a", 1): {(1, 1): "M", (7, 7): "D", (4, 2): "TAX"},
        ("b", 1): {(9, 9): "M", (3, 3): "TAX"},
    }
    labels = {
        ("a", 1): [_obs("MERCHANT_NAME", 1, 1, "M", "VALID"), _obs("DATE", 7, 7, "D", "VALID")],
        ("b", 1): [_obs("TAX", 3, 3, "TAX", "VALID")],
    }
    res = _resolve(receipts, words, labels)
    assert res.survivor == "a#1"
    assert len(res.gap_fills) == 1
    assert res.gap_fills[0].locus == "4:2"  # the survivor's TAX word, not b's "3:3"
    assert res.gap_fills[0].label == "TAX"


def test_legacy_alias_gap_fill_uses_canonical():
    # dropped copy has VALID legacy ITEM_TOTAL -> fills survivor gap as LINE_TOTAL
    receipts = [_r("a", 1, "S"), _r("a", 2, "S")]
    words = {("a", 1): {(1, 1): "M", (3, 3): "x", (2, 2): "9.99"},
             ("a", 2): {(1, 1): "M", (3, 3): "x", (2, 2): "9.99"}}
    labels = {
        ("a", 1): [_obs("MERCHANT_NAME", 1, 1, "M", "VALID"), _obs("DATE", 3, 3, "x", "VALID")],
        ("a", 2): [_obs("ITEM_TOTAL", 2, 2, "9.99", "VALID")],  # dropped; survivor lacks 2:2
    }
    res = _resolve(receipts, words, labels)
    assert len(res.gap_fills) == 1 and res.gap_fills[0].label == "LINE_TOTAL"


def test_cross_image_gap_fill_requires_survivor_to_have_word():
    # cross-image: dropped copy labels a word the survivor doesn't contain -> no fill
    receipts = [_r("a", 1, "S"), _r("b", 1, "S")]
    words = {("a", 1): {(1, 1): "ACME", (2, 2): "MILK"}, ("b", 1): {(5, 5): "ACME", (6, 6): "EGGS"}}
    labels = {
        ("a", 1): [_obs("MERCHANT_NAME", 1, 1, "ACME", "VALID")],
        ("b", 1): [_obs("MERCHANT_NAME", 5, 5, "ACME", "VALID"), _obs("PRODUCT_NAME", 6, 6, "EGGS", "VALID")],
    }
    res = _resolve(receipts, words, labels)
    assert res.scope == "cross_image"
    # survivor lacks the word "EGGS" entirely -> PRODUCT_NAME not filled
    assert all(gf.word_text != "EGGS" for gf in res.gap_fills)
