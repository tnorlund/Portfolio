"""Unit tests for the deterministic merge-dossier builder (pure functions)."""

from types import SimpleNamespace

from receipt_upload.dedup.context import LabelObs, build_merge_dossiers


def _r(image_id, receipt_id, sha, w=100, h=100):
    return SimpleNamespace(
        image_id=image_id, receipt_id=receipt_id, sha256=sha, width=w, height=h
    )


def _obs(label, line, word, text, status=None):
    return LabelObs(label=label, line_id=line, word_id=word, word_text=text,
                    validation_status=status)


def test_groups_only_shared_sha_with_multiple_receipts():
    receipts = [_r("a", 1, "S1"), _r("b", 1, "S1"), _r("c", 1, "S2")]
    ds = build_merge_dossiers(receipts, {}, {})
    assert len(ds) == 1
    assert ds[0].anchor == "receipt_sha256"
    assert {m.key_str for m in ds[0].members} == {"a#1", "b#1"}


def test_within_vs_cross_image_scope():
    # same image -> within_image; different images -> cross_image
    same = build_merge_dossiers([_r("a", 1, "S"), _r("a", 2, "S")], {}, {})
    cross = build_merge_dossiers([_r("a", 1, "S"), _r("b", 1, "S")], {}, {})
    assert same[0].scope == "within_image"
    assert cross[0].scope == "cross_image"


def test_survivor_prefers_label_quality():
    # same dims (real dups always match dims); b#1 has a validated label -> survives
    receipts = [_r("a", 1, "S", 100, 100), _r("b", 1, "S", 100, 100)]
    labels = {
        ("a", 1): [],
        ("b", 1): [_obs("MERCHANT_NAME", 1, 1, "ACME", status="VALID")],
    }
    words = {("a", 1): {(1, 1): "ACME"}, ("b", 1): {(1, 1): "ACME"}}
    d = build_merge_dossiers(receipts, words, labels)[0]
    assert d.survivor_suggested == "b#1"  # label quality decides the survivor
    # survivor has labels, so the empty-survivor warning must NOT fire
    assert not any("0 canonical labels" in n for n in d.notes)


def test_empty_survivor_warning_when_all_labels_on_one_copy():
    # both copies labelless except the LOWER-ranked one -> survivor empty warning
    receipts = [_r("a", 1, "S", 10, 10), _r("b", 1, "S", 10, 10)]
    words = {("a", 1): {(1, 1): "X"}, ("b", 1): {(1, 1): "X"}}
    labels = {("a", 1): [], ("b", 1): []}
    d = build_merge_dossiers(receipts, words, labels)[0]
    assert any("0 canonical labels" in n for n in d.notes)


def test_positional_conflict_detected_within_image():
    receipts = [_r("a", 1, "S"), _r("a", 2, "S")]
    words = {("a", 1): {(3, 4): "$5.90"}, ("a", 2): {(3, 4): "$5.90"}}
    labels = {
        ("a", 1): [_obs("SUBTOTAL", 3, 4, "$5.90")],
        ("a", 2): [_obs("GRAND_TOTAL", 3, 4, "$5.90")],
    }
    d = build_merge_dossiers(receipts, words, labels)[0]
    assert len(d.conflicts) == 1
    c = d.conflicts[0]
    assert c.locus == "3:4" and c.word_text == "$5.90"
    assert {o["label"] for o in c.options} == {"SUBTOTAL", "GRAND_TOTAL"}
    assert d.deterministic_action == "consolidate_then_drop"


def test_junk_labels_flagged_and_excluded_from_union():
    receipts = [_r("a", 1, "S"), _r("b", 1, "S")]
    words = {("a", 1): {(1, 1): "X"}, ("b", 1): {(1, 1): "X"}}
    labels = {
        ("a", 1): [_obs("REMOVE_DUPLICATE_LINE_TOTAL", 1, 1, "X")],
        ("b", 1): [_obs("MERCHANT_NAME", 1, 1, "X")],
    }
    d = build_merge_dossiers(receipts, words, labels)[0]
    assert "REMOVE_DUPLICATE_LINE_TOTAL" not in d.label_union
    assert "MERCHANT_NAME" in d.label_union
    assert any("REMOVE_DUPLICATE_LINE_TOTAL" in f["junk_labels"] for f in d.junk_flags)


def test_labels_only_on_nonsurvivor_surfaced():
    # survivor a#1 lacks TAX that lives only on b#1 -> must not be lost
    receipts = [_r("a", 1, "S"), _r("b", 1, "S")]
    words = {("a", 1): {(1, 1): "T", (2, 2): "TAX"}, ("b", 1): {(1, 1): "T", (2, 2): "TAX"}}
    labels = {
        ("a", 1): [_obs("MERCHANT_NAME", 1, 1, "T", status="VALID")],
        ("b", 1): [_obs("TAX", 2, 2, "TAX")],
    }
    d = build_merge_dossiers(receipts, words, labels)[0]
    assert d.survivor_suggested == "a#1"
    lost = {(x["label"], x["word_text"]) for x in d.labels_only_on_nonsurvivor}
    assert ("TAX", "TAX") in lost
    assert d.deterministic_action == "consolidate_then_drop"


def test_within_image_lost_label_is_position_aware():
    # repeated token "$9.99" at two positions; survivor labels 5:5, dropped labels
    # 8:8 — the 8:8 label IS a real positional gap and must be surfaced (not
    # suppressed by the equal (word_text,label) on a different occurrence).
    receipts = [_r("a", 1, "S"), _r("a", 2, "S")]
    words = {
        ("a", 1): {(1, 1): "x", (5, 5): "$9.99", (8, 8): "$9.99"},
        ("a", 2): {(1, 1): "x", (5, 5): "$9.99", (8, 8): "$9.99"},
    }
    labels = {
        ("a", 1): [_obs("DATE", 1, 1, "x", "VALID"), _obs("LINE_TOTAL", 5, 5, "$9.99", "VALID")],
        ("a", 2): [_obs("LINE_TOTAL", 8, 8, "$9.99", "VALID")],  # OTHER occurrence
    }
    d = build_merge_dossiers(receipts, words, labels)[0]
    assert d.survivor_suggested == "a#1"
    lost_pos = {x["pos"] for x in d.labels_only_on_nonsurvivor}
    assert "8:8" in lost_pos  # positional gap surfaced
    assert d.deterministic_action == "consolidate_then_drop"


def test_clean_group_is_drop_redundant():
    receipts = [_r("a", 1, "S"), _r("a", 2, "S")]
    words = {("a", 1): {(1, 1): "ACME"}, ("a", 2): {(1, 1): "ACME"}}
    labels = {
        ("a", 1): [_obs("MERCHANT_NAME", 1, 1, "ACME")],
        ("a", 2): [_obs("MERCHANT_NAME", 1, 1, "ACME")],
    }
    d = build_merge_dossiers(receipts, words, labels)[0]
    assert not d.conflicts and not d.labels_only_on_nonsurvivor and not d.junk_flags
    assert d.deterministic_action == "drop_redundant"
