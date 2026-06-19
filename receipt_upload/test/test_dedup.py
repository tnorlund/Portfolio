"""Unit tests for the Tier 0 + Tier 1 duplicate detector (pure functions)."""

from types import SimpleNamespace

from receipt_upload.dedup.detector import (
    detect_duplicates,
    find_exact_duplicates,
    find_signature_candidates,
    normalize_merchant_key,
)


def _r(image_id, receipt_id, sha256, w=100, h=100):
    return SimpleNamespace(
        image_id=image_id, receipt_id=receipt_id, sha256=sha256, width=w, height=h
    )


def test_exact_groups_only_when_multiple_distinct_receipts():
    receipts = [
        _r("a", 1, "S1"),
        _r("b", 1, "S1"),          # exact dup of a#1
        _r("c", 1, "S2"),          # unique
    ]
    groups = find_exact_duplicates(receipts)
    assert len(groups) == 1
    g = groups[0]
    assert g.method == "exact" and g.action == "auto-merge"
    assert {g.keeper, *g.duplicates} == {("a", 1), ("b", 1)}


def test_exact_requires_matching_dimensions():
    # identical sha but different dimensions == blank/failed crop collision, not a
    # real duplicate (tobytes-only hash) -> must NOT be grouped/auto-merged
    receipts = [_r("a", 1, "S1", 50, 50), _r("b", 1, "S1", 200, 200)]
    assert find_exact_duplicates(receipts) == []


def test_exact_groups_same_dims_and_picks_keeper():
    receipts = [_r("a", 1, "S1", 100, 100), _r("b", 1, "S1", 100, 100)]
    g = find_exact_duplicates(receipts)[0]
    assert {g.keeper, *g.duplicates} == {("a", 1), ("b", 1)}


def test_signature_excludes_pairs_already_exact():
    receipts = [_r("a", 1, "S1"), _r("b", 1, "S1")]
    sig = {("a", 1): "m|9.99|2024-01-01|2", ("b", 1): "m|9.99|2024-01-01|2"}
    # a#1/b#1 are already an exact pair -> no NEW signature candidate
    rep = detect_duplicates(receipts, sig)
    assert rep["summary"]["exact_groups"] == 1
    assert rep["summary"]["signature_candidate_groups"] == 0


def test_signature_surfaces_byte_different_near_dup():
    receipts = [_r("a", 1, "S1"), _r("c", 1, "S2")]   # different bytes
    sig = {("a", 1): "m|9.99|2024-01-01|2", ("c", 1): "m|9.99|2024-01-01|2"}
    groups = find_signature_candidates(receipts, sig)
    assert len(groups) == 1
    assert groups[0].method == "signature" and groups[0].action == "review"
    assert {groups[0].keeper, *groups[0].duplicates} == {("a", 1), ("c", 1)}


def test_signature_skips_none_signatures():
    receipts = [_r("a", 1, "S1"), _r("c", 1, "S2")]
    groups = find_signature_candidates(receipts, {("a", 1): None, ("c", 1): None})
    assert groups == []


def test_normalize_merchant_prefers_place_id():
    assert normalize_merchant_key("PID123", "Sprouts").startswith("pid:")
    assert normalize_merchant_key(None, "Sprouts Farmers").startswith("name:")
    assert normalize_merchant_key(None, "") is None
