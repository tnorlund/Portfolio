"""Unit tests for glyphstudio.typography (pure, synthetic masks only)."""

from __future__ import annotations

import numpy as np
import pytest

from glyphstudio.family_cluster import normalize_glyph
from glyphstudio.stylescan import group_visual_lines
from glyphstudio.typography import (
    LineShape,
    assign_tiers,
    build_style_runs,
    clean_letter_mask,
    cluster_line_shapes,
    estimate_slant,
    exemplar_glyphs,
    intra_line_overlap,
    line_attribution,
    line_pair_iou,
    line_shape,
    section_run_crosstab,
    shifted_iou,
)


# --- synthetic letterforms ---------------------------------------------------


def block_H(h=20, w=14):
    m = np.zeros((h, w), bool)
    m[:, :3] = True
    m[:, -3:] = True
    m[h // 2 - 1 : h // 2 + 1, :] = True
    return m


def block_O(h=20, w=14):
    m = np.zeros((h, w), bool)
    m[:3, :] = True
    m[-3:, :] = True
    m[:, :3] = True
    m[:, -3:] = True
    return m


def round_O(h=20, w=20):
    yy, xx = np.mgrid[:h, :w]
    cy, cx = (h - 1) / 2, (w - 1) / 2
    r = ((yy - cy) / cy) ** 2 + ((xx - cx) / cx) ** 2
    return (r <= 1.0) & (r >= 0.45)


def sheared(mask, deg):
    """Shear a mask rightward by ``deg`` (top leans right)."""
    m = np.asarray(mask, bool)
    h, w = m.shape
    t = np.tan(np.radians(deg))
    pad = int(abs(t) * h) + 2
    out = np.zeros((h, w + 2 * pad), bool)
    ys, xs = np.where(m)
    yc = (h - 1) / 2
    nx = np.round(xs + pad + t * (yc - ys)).astype(int)
    out[ys, np.clip(nx, 0, out.shape[1] - 1)] = True
    return out


# --- clean_letter_mask -------------------------------------------------------


def test_clean_mask_drops_edge_bleed():
    m = block_H().copy()
    m = np.pad(m, ((0, 0), (0, 8)))
    m[4:16, -2:] = True  # neighbor fragment hugging the right edge
    cleaned = clean_letter_mask(m)
    assert not cleaned[:, -2:].any()
    assert (cleaned[:, :14] == block_H()).all()


def test_clean_mask_drops_specks_keeps_multipart():
    # an "i": dot above a bar, plus a 2px speck in a corner
    m = np.zeros((20, 9), bool)
    m[6:20, 3:6] = True  # bar
    m[0:3, 3:6] = True  # dot (stacked, central x)
    m[18:20, 0] = True  # speck
    cleaned = clean_letter_mask(m)
    assert cleaned[0:3, 3:6].any(), "dot must survive"
    assert not cleaned[18:20, 0].any(), "speck must go"


def test_clean_mask_drops_rule_fragment():
    m = np.zeros((20, 30), bool)
    m[2:16, 12:18] = True  # letter body
    m[18, :] = True  # underline fragment across full width
    cleaned = clean_letter_mask(m)
    assert not cleaned[18].any()
    assert cleaned[2:16, 12:18].all()


def test_clean_mask_keeps_largest_even_at_edge():
    m = np.zeros((10, 10), bool)
    m[:, :2] = True  # only component, at the edge
    assert clean_letter_mask(m).sum() == m.sum()


def test_clean_mask_empty():
    m = np.zeros((5, 5), bool)
    assert clean_letter_mask(m).sum() == 0


# --- shifted_iou / attribution ----------------------------------------------


def test_shifted_iou_forgives_jitter():
    a = normalize_glyph(block_H())
    b = np.roll(a, 2, axis=1)
    assert shifted_iou(a, b, max_shift=2) == 1.0
    assert shifted_iou(a, b, max_shift=0) < 1.0


def test_line_attribution_scores_and_gate():
    atlas = {"H": normalize_glyph(block_H()), "O": normalize_glyph(block_O())}
    letters = [("H", atlas["H"]), ("O", atlas["O"])] * 3
    score, n = line_attribution(letters, atlas, min_letters=4)
    assert n == 6 and score == 1.0
    # different letterforms -> low score
    other = [("O", normalize_glyph(round_O()))] * 4
    score2, _ = line_attribution(other, atlas, min_letters=4)
    assert score2 is not None and score2 < 0.75
    # too few letters -> None (distributions, not n=1)
    score3, n3 = line_attribution(letters[:2], atlas, min_letters=4)
    assert score3 is None and n3 == 2


def test_line_attribution_skips_unknown_chars():
    atlas = {"H": normalize_glyph(block_H())}
    letters = [("H", atlas["H"])] * 4 + [("@", atlas["H"])] * 10
    score, n = line_attribution(letters, atlas, min_letters=4)
    assert n == 4 and score == 1.0


# --- slant -------------------------------------------------------------------


def test_slant_upright_is_zero():
    bar = np.zeros((24, 8), bool)
    bar[:, 3:5] = True
    assert abs(estimate_slant(bar)) < 1.5


def test_slant_detects_italic_lean():
    bar = np.zeros((24, 8), bool)
    bar[:, 3:5] = True
    lean = sheared(bar, 15)
    got = estimate_slant(lean)
    assert 10 <= got <= 20, got
    # and leftward lean is negative
    got_l = estimate_slant(sheared(bar, -15))
    assert -20 <= got_l <= -10, got_l


def test_slant_tiny_mask_is_zero():
    m = np.zeros((6, 6), bool)
    m[2, 2] = True
    assert estimate_slant(m) == 0.0


# --- contamination -----------------------------------------------------------


def test_intra_line_overlap():
    clean = [(0, 0, 10, 20), (12, 0, 22, 20), (24, 0, 34, 20)]
    assert intra_line_overlap(clean) == 0.0
    stamped = [(0, 0, 10, 20), (2, 0, 12, 20), (4, 0, 14, 20)]
    assert intra_line_overlap(stamped) == 1.0
    assert intra_line_overlap(clean[:1]) == 0.0


# --- discovery clustering ----------------------------------------------------


def _shape(key, pairs):
    return line_shape(key, [(c, normalize_glyph(m)) for c, m in pairs])


def test_cluster_separates_two_typefaces():
    fa = [("H", block_H()), ("O", block_O()), ("I", block_H()[:, :6])]
    fb = [("H", block_H().T[:14, :]), ("O", round_O()), ("I", round_O()[:, :8])]
    lines = [_shape(f"a{i}", fa) for i in range(3)] + [
        _shape(f"b{i}", fb) for i in range(3)
    ]
    clusters = cluster_line_shapes(lines, threshold=0.6, min_shared=3)
    assert len(clusters) == 2
    keys = [{lines[i].key[0] for i in cl} for cl in clusters]
    assert {"a"} in keys and {"b"} in keys


def test_cluster_min_shared_gate():
    # one shared identical char must NOT merge two otherwise-alien lines
    a = _shape("a", [("H", block_H()), ("X", block_O()), ("Y", block_O())])
    b = _shape("b", [("H", block_H()), ("P", round_O()), ("Q", round_O())])
    clusters = cluster_line_shapes([a, b], threshold=0.6, min_shared=3)
    assert len(clusters) == 2


def test_line_pair_iou_no_shared():
    a = _shape("a", [("H", block_H())])
    b = _shape("b", [("O", block_O())])
    assert line_pair_iou(a, b) == (0.0, 0)


def test_exemplar_glyphs_votes():
    fa = [("H", block_H()), ("O", block_O())]
    members = [_shape(f"a{i}", fa) for i in range(3)]
    members.append(_shape("odd", [("Z", block_H())]))
    ex = exemplar_glyphs(members, min_votes=2)
    assert "H" in ex and "O" in ex and "Z" not in ex
    single = exemplar_glyphs(members[:1], min_votes=2)
    assert "H" in single  # single-line cluster keeps its own glyphs


# --- tiers -------------------------------------------------------------------


def test_assign_tiers():
    lines = [
        {"cap_px": 20.0, "stroke_med": 2.0},
        {"cap_px": 20.0, "stroke_med": 2.1},
        {"cap_px": 21.0, "stroke_med": 2.0},
        {"cap_px": 32.0, "stroke_med": 2.0},  # large
        {"cap_px": 20.0, "stroke_med": 3.0},  # bold
        {"cap_px": None, "stroke_med": None},  # unmeasured -> normal
    ]
    assign_tiers(lines)
    assert [l["tier"] for l in lines] == [
        "normal",
        "normal",
        "normal",
        "large",
        "bold",
        "normal",
    ]


# --- runs + crosstab ---------------------------------------------------------


def _line(tf, lids, tier="normal", ul=False, rv=False):
    return {
        "typeface": tf,
        "tier": tier,
        "underline": ul,
        "reverse_video": rv,
        "line_ids": lids,
    }


def test_build_style_runs_groups_and_gaps():
    lines = [
        _line("T0", [1]),
        _line("T0", [2]),
        {"typeface": None, "line_ids": [3]},  # unattributed: transparent
        _line("T0", [4]),
        _line("T1", [5], tier="large"),
        _line("T0", [6]),
        _line("T0", [7], ul=True),
    ]
    runs = build_style_runs(lines)
    assert [(r.typeface, r.line_ids) for r in runs] == [
        ("T0", [1, 2, 4]),
        ("T1", [5]),
        ("T0", [6]),
        ("T0", [7]),
    ]
    assert runs[3].underline is True


def test_section_run_crosstab():
    lines = [
        _line("T0", [1]),
        _line("T0", [2]),
        _line("T1", [3], tier="large"),
        _line("T0", [4]),
    ]
    runs = build_style_runs(lines)
    sections = [
        {"section_type": "STOREFRONT", "line_ids": [1, 2, 3]},  # spans 2 faces
        {"section_type": "ITEMS", "line_ids": [4]},
        {"section_type": "FOOTER", "line_ids": [99]},  # unmeasured
    ]
    xt = section_run_crosstab(sections, runs)
    sf = xt[0]
    assert sf["multi_run"] and sf["multi_typeface"] and sf["n_runs"] == 2
    assert xt[1] == {
        "section_type": "ITEMS",
        "n_lines": 1,
        "n_measured": 1,
        "run_ids": [2],
        "n_runs": 1,
        "typefaces": ["T0"],
        "multi_run": False,
        "n_styles": 1,
        "multi_style": False,
        "multi_typeface": False,
    }
    assert xt[2]["n_measured"] == 0 and xt[2]["n_runs"] == 0


def test_crosstab_multi_run_vs_multi_style():
    # same style interrupted by another block: multi_run but NOT multi_style
    lines = [
        _line("T0", [1]),
        _line("T1", [2], tier="large"),
        _line("T0", [3]),
    ]
    runs = build_style_runs(lines)
    xt = section_run_crosstab(
        [{"section_type": "ITEMS", "line_ids": [1, 3]}], runs
    )
    assert xt[0]["multi_run"] and not xt[0]["multi_style"]


def test_line_slant_skips_diagonal_chars():
    from glyphstudio.typography import line_slant

    pairs = [("X", 14.0), ("X", 15.0), ("X", 13.0), ("X", 14.0)]
    assert line_slant(pairs) is None  # all-diagonal line: no verdict
    upright = [("l", 0.0), ("t", 1.0), ("d", -1.0), ("X", 14.0)]
    assert abs(line_slant(upright)) <= 1.0
    nan = float("nan")
    assert line_slant([("l", nan), ("t", nan), ("d", 0.0)]) is None


# --- stylescan refactor: extracted grouping stays greedy-top-down ------------


def test_group_visual_lines():
    def w(cy, h=10):
        return {"cy": cy, "h": h}

    lines = group_visual_lines([w(100), w(10), w(12), w(104), w(50)])
    assert [len(l) for l in lines] == [2, 1, 2]
    assert [round(x["cy"]) for x in lines[0]] == [10, 12]


if __name__ == "__main__":
    import sys

    sys.exit(pytest.main([__file__, "-v"]))
