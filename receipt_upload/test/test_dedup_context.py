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
    # both copies labelless -> survivor empty warning fires
    receipts = [_r("a", 1, "S", 10, 10), _r("b", 1, "S", 10, 10)]
    words = {("a", 1): {(1, 1): "X"}, ("b", 1): {(1, 1): "X"}}
    labels = {("a", 1): [], ("b", 1): []}
    d = build_merge_dossiers(receipts, words, labels)[0]
    assert any("0 canonical labels" in n for n in d.notes)
