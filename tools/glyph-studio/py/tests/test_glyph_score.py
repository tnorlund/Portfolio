"""Unit tests for glyphstudio.glyph_score (pure, no network)."""

import numpy as np
import pytest

from glyphstudio.family_cluster import normalize_glyph
from glyphstudio.glyph_score import (
    GlyphResult,
    anchor_similarity,
    build_anchor_references,
    build_self_references,
    crop_similarity,
    group_rollup,
    load_refpack,
    per_char_table,
    percentile_of,
    save_refpack,
    score_glyph,
    score_instances,
    self_similarity,
    shifted_iou_stack,
    weighted_glyphscore,
)
from glyphstudio.typography import shifted_iou

RNG = np.random.default_rng(7)


def _letter_E(size: int = 32) -> np.ndarray:
    g = np.zeros((size, size), bool)
    g[:, 2:7] = True
    g[0:5, 2:28] = True
    g[14:18, 2:24] = True
    g[27:32, 2:28] = True
    return g


def _letter_O(size: int = 32) -> np.ndarray:
    g = np.zeros((size, size), bool)
    yy, xx = np.mgrid[0:size, 0:size]
    r = ((yy - 15.5) / 15.0) ** 2 + ((xx - 15.5) / 12.0) ** 2
    return (r <= 1.0) & (r >= 0.45)


def _jitter(g: np.ndarray, p: float = 0.04, seed: int = 0) -> np.ndarray:
    """A 'printed' copy: flip a few pixels near the shape + 1px shift."""
    rng = np.random.default_rng(seed)
    out = g.copy()
    flip = rng.random(g.shape) < p
    out ^= flip
    dy, dx = rng.integers(-1, 2, 2)
    return normalize_glyph(np.roll(np.roll(out, dy, 0), dx, 1))


def _stack(g, n=12, seed0=0):
    return np.stack([_jitter(g, seed=seed0 + i) for i in range(n)])


# --- primitives ---------------------------------------------------------------


def test_shifted_iou_stack_matches_scalar():
    e, o = _letter_E(), _letter_O()
    stack = np.stack([e, o, _jitter(e, seed=3)])
    vec = shifted_iou_stack(e, stack)
    ref = [shifted_iou(e, s) for s in stack]
    assert np.allclose(vec, ref)


def test_shifted_iou_stack_empty():
    assert shifted_iou_stack(_letter_E(), np.zeros((0, 32, 32), bool)).size == 0


def test_percentile_midrank():
    dist = np.array([0.1, 0.2, 0.3, 0.4])
    assert percentile_of(dist, 0.05) == pytest.approx(0.5 / 5)
    assert percentile_of(dist, 0.9) == pytest.approx(4.5 / 5)
    # tie: value equal to one member sits between its neighbors
    assert 0.2 < percentile_of(dist, 0.3) < 0.8
    assert np.isnan(percentile_of(np.array([]), 0.5))


def test_self_similarity_spread_is_tight_for_consistent_prints():
    st = _stack(_letter_E(), n=10)
    d = self_similarity(st)
    assert d.shape == (10,)
    assert d.min() > 0.5  # jittered copies of one shape agree strongly


def test_crop_similarity_median_not_max():
    e = _letter_E()
    stack = np.stack([e, _letter_O(), _letter_O()])  # one match, two misses
    val = crop_similarity(e, stack)
    assert val < 0.6  # median punishes matching only the outlier


# --- self mode: the metric must separate same-face from different-face --------


def test_self_mode_same_letterform_scores_high():
    refs = build_self_references({"E": _stack(_letter_E(), n=12)})
    picks = [
        score_glyph(refs["E"], _jitter(_letter_E(), seed=s)).pct
        for s in range(90, 100)
    ]
    # same-face candidates land INSIDE the real variation: the MASS sits
    # mid-distribution (individual samples may straddle the n=12 min — that
    # is what a percentile of a small distribution does at the edges)
    assert float(np.mean(picks)) > 0.3
    assert sum(p > 0.05 for p in picks) >= 8


def test_self_mode_wrong_letterform_scores_floor():
    refs = build_self_references({"E": _stack(_letter_E(), n=12)})
    r = score_glyph(refs["E"], _letter_O())
    assert r.raw < 0.4
    assert r.pct < 0.1  # below every real crop


def test_noise_glyph_scores_floor():
    refs = build_self_references({"E": _stack(_letter_E(), n=12)})
    noise = normalize_glyph(RNG.random((32, 32)) < 0.3)
    assert score_glyph(refs["E"], noise).pct < 0.1


def test_min_ref_enforced():
    refs = build_self_references({"E": _stack(_letter_E(), n=3)})
    assert refs == {}  # distributions, not n=3


# --- anchor mode ---------------------------------------------------------------


def test_anchor_mode_crops_center_the_distribution():
    e = _letter_E()
    crops = _stack(e, n=12)
    refs = build_anchor_references({"E": crops}, {"E": e})
    # the designed glyph itself beats its own printed copies
    r = score_glyph(refs["E"], e)
    assert r.pct > 0.8
    # a printed copy sits INSIDE the distortion spread
    r2 = score_glyph(refs["E"], _jitter(e, seed=55))
    assert 0.05 < r2.pct
    # a different letterform sits below it
    assert score_glyph(refs["E"], _letter_O()).pct < 0.1


def test_anchor_similarity_values_match_scalar():
    e = _letter_E()
    crops = _stack(e, n=8)
    d = anchor_similarity(crops, e)
    assert np.allclose(d, [shifted_iou(c, e) for c in crops])


def test_anchor_mode_skips_chars_missing_either_side():
    refs = build_anchor_references(
        {"E": _stack(_letter_E(), 12), "O": _stack(_letter_O(), 12)},
        {"E": _letter_E()},
    )
    assert set(refs) == {"E"}


# --- aggregation -----------------------------------------------------------------


def test_score_instances_skips_unknown_chars_and_keeps_groups():
    refs = build_self_references({"E": _stack(_letter_E(), n=12)})
    res = score_instances(
        refs,
        [("E", _jitter(_letter_E(), seed=1), "line0"), ("Z", _letter_O(), "line0")],
    )
    assert len(res) == 1 and res[0].group == "line0"


def test_per_char_table_and_rollup():
    results = [
        GlyphResult("E", 0.6, 0.5, "l0"),
        GlyphResult("E", 0.6, 0.7, "l0"),
        GlyphResult("O", 0.2, 0.1, "l1"),
    ]
    tab = per_char_table(results)
    assert tab["E"]["n"] == 2 and tab["E"]["pct_mean"] == pytest.approx(0.6)
    roll = group_rollup(results)
    assert roll["l0"]["n"] == 2 and roll["l1"]["pct_mean"] == pytest.approx(0.1)


def test_weighted_glyphscore_instance_pooling_and_char_weights():
    results = [
        GlyphResult("E", 0.6, 0.8),
        GlyphResult("E", 0.6, 0.8),
        GlyphResult("O", 0.2, 0.2),
    ]
    assert weighted_glyphscore(results) == pytest.approx(60.0)
    # char-weighted: E is used 9x as often as O in the corpus
    refs = build_self_references(
        {"E": _stack(_letter_E(), 9), "O": _stack(_letter_O(), 9)},
        min_ref=9,
    )
    refs["E"].count, refs["O"].count = 90, 10
    per_char = [GlyphResult("E", 0.6, 0.8), GlyphResult("O", 0.2, 0.2)]
    assert weighted_glyphscore(per_char, refs) == pytest.approx(
        100 * (0.8 * 90 + 0.2 * 10) / 100
    )
    assert np.isnan(weighted_glyphscore([]))


# --- refpack I/O -----------------------------------------------------------------


def test_refpack_roundtrip(tmp_path):
    pack = {"E": _stack(_letter_E(), 9), ".": _stack(_letter_O(), 8)}
    p = str(tmp_path / "pack.npz")
    save_refpack(p, pack)
    loaded = load_refpack(p)
    assert set(loaded) == {"E", "."}
    assert np.array_equal(loaded["E"], pack["E"])
    assert loaded["."].dtype == bool
