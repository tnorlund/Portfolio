"""Unit tests for the Tier 0 exact-duplicate detector (pure functions)."""

from types import SimpleNamespace

from receipt_upload.dedup.detector import find_exact_duplicates


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
