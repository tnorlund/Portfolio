"""Offline contracts for glyphstudio.mint (donor adoption + normalization)."""

from __future__ import annotations

import json
import os
import shutil

import pytest
from glyphstudio import mint
from glyphstudio.schema import load_font, load_glyphs

FONTS = mint.FONTS_DIR


@pytest.fixture
def scratch_font(tmp_path):
    """A copy of the committed speedway font with its donor glyphs removed."""
    src = os.path.join(FONTS, "speedway")
    dst = tmp_path / "speedway"
    shutil.copytree(src, dst)
    removed = []
    for name in os.listdir(dst / "glyphs"):
        path = dst / "glyphs" / name
        with open(path, encoding="utf-8") as fh:
            g = json.load(fh)
        if g.get("donor"):
            os.remove(path)
            removed.append(g["char"])
    assert removed, "fixture expects speedway to carry donor glyphs"
    return str(dst), removed


def test_cap_band_from_flat_caps_of_traced_glyphs():
    glyphs = load_glyphs(os.path.join(FONTS, "speedway"))
    band = mint.cap_band(glyphs, only_traced=True)
    assert band is not None
    lo, hi = band
    assert 30 < lo < 80 and 920 < hi < 970


def test_adopt_donor_remaps_onto_target_band_and_tags_provenance(scratch_font):
    font_dir, removed = scratch_font
    before = load_glyphs(font_dir)
    band = mint.cap_band(before, only_traced=True)
    adopted = mint.adopt_donor(
        os.path.join(FONTS, "vons"),
        font_dir,
        "BEGP",
        target_band=band,
        note="test",
    )
    assert sorted(adopted) == ["B", "E", "G", "P"]
    after = load_glyphs(font_dir)
    for ch in "BEGP":
        g = after[ord(ch)]
        assert g["provenance"] == "edited"
        assert g["donor"] == "vons"
        assert "trace" not in g
        _, _, y0, y1 = mint._extents(g)
        # donor cap band now sits within a few units of the target band
        assert abs(y1 - band[1]) < 40 and y0 > band[0] - 40


def test_squeeze_wide_only_touches_non_traced_glyphs(scratch_font):
    font_dir, _ = scratch_font
    glyphs = load_glyphs(font_dir)
    extent = mint.cell_extent(glyphs)
    assert extent and 300 < extent < 600
    # widen a hand-authored glyph far past the cell, then squeeze
    target = glyphs[ord("M")]
    assert target["provenance"] == "edited"
    mint._walk(target, lambda x, y: (x * 2.5, y))
    path = os.path.join(font_dir, "glyphs", "u004d.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(target, fh)
    traced_before = {
        cp: json.dumps(g["strokes"], sort_keys=True)
        for cp, g in glyphs.items()
        if g.get("provenance") == "traced"
    }
    squeezed = mint.squeeze_wide(font_dir, extent, skip=set())
    assert "M" in squeezed
    after = load_glyphs(font_dir)
    x0, x1, _, _ = mint._extents(after[ord("M")])
    assert x1 - x0 <= extent * 1.01
    for cp, geo in traced_before.items():
        assert json.dumps(after[cp]["strokes"], sort_keys=True) == geo


def test_handcraft_preferred_is_a_subset_of_handcraft_specs():
    from glyphstudio.handcraft import DEFAULT_W

    assert mint.HANDCRAFT_PREFERRED <= set(DEFAULT_W) | set("#")


# --- mint --suggest-donor: rank sibling faces by shape IoU ---


def _self_corpus(font_name: str, out_npz, *, copies: int = 8, chars: int = 12):
    """A samples npz whose stacks ARE ``font_name``'s own glyphs.

    Each well-sampled char is ``copies`` identical rasters of the font's
    glyph pasted onto a 120px canvas (ref cap 40), so that font must win a
    shape-IoU ranking against any sibling.
    """
    import numpy as np
    from glyphstudio.raster import rasterize_glyph
    from glyphstudio.samples import canvas_geometry
    from glyphstudio.schema import merged_params

    font_dir = os.path.join(FONTS, font_name)
    font = load_font(font_dir)
    glyphs = load_glyphs(font_dir)
    canvas_h = 120
    ref_cap, _ = canvas_geometry(canvas_h)
    stacks = {}
    for ch in "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789":
        g = glyphs.get(ord(ch))
        if g is None or not g.get("strokes"):
            continue
        bitmap, _ = rasterize_glyph(g, merged_params(font, g), ref_cap)
        h, w = bitmap.shape
        canvas = np.zeros((canvas_h, max(canvas_h, w + 10)), bool)
        canvas[10 : 10 + h, 5 : 5 + w] = bitmap.astype(bool)
        stacks[str(ord(ch))] = np.stack([canvas] * copies)
        if len(stacks) >= chars:
            break
    assert len(stacks) >= 8
    np.savez_compressed(out_npz, **stacks)
    return str(out_npz)


@pytest.fixture
def sibling_root(tmp_path):
    """A fonts root holding just two committed siblings (speedway, vons)."""
    root = tmp_path / "fonts"
    root.mkdir()
    for name in ("speedway", "vons"):
        os.symlink(os.path.join(FONTS, name), root / name)
    return str(root)


def test_suggest_donor_ranks_the_source_face_first(tmp_path, sibling_root):
    samples = _self_corpus("speedway", tmp_path / "speedway.refined.npz")
    ranked = mint.suggest_donor(samples, sibling_root, exclude=set())
    assert [r["font"] for r in ranked] == ["speedway", "vons"]
    assert ranked[0]["iou"] > 0.9  # its own glyphs, resized onto themselves
    assert ranked[0]["iou"] > ranked[1]["iou"]
    for r in ranked:
        assert r["chars"] >= 5
        assert 0.0 <= r["iou"] <= 1.0


def test_suggest_donor_excludes_the_font_being_minted(tmp_path, sibling_root):
    samples = _self_corpus("speedway", tmp_path / "speedway.refined.npz")
    ranked = mint.suggest_donor(samples, sibling_root, exclude={"speedway"})
    assert [r["font"] for r in ranked] == ["vons"]


def test_suggest_donor_needs_well_sampled_chars(tmp_path, sibling_root):
    # 8 copies per char < min_samples 9 -> no stacks qualify -> no ranking.
    samples = _self_corpus("speedway", tmp_path / "thin.npz", copies=8)
    assert (
        mint.suggest_donor(samples, sibling_root, exclude=set(), min_samples=9)
        == []
    )


def test_suggest_donor_skips_dirs_without_font_json(tmp_path, sibling_root):
    os.makedirs(os.path.join(sibling_root, "notafont"))
    samples = _self_corpus("speedway", tmp_path / "speedway.refined.npz")
    ranked = mint.suggest_donor(samples, sibling_root, exclude=set())
    assert "notafont" not in {r["font"] for r in ranked}


def test_main_suggest_donor_prints_ranking_and_exits_zero(
    tmp_path, sibling_root, capsys
):
    samples = _self_corpus("speedway", tmp_path / "speedway.refined.npz")
    # font_dir basename is what --suggest-donor excludes; the dir need not
    # exist because the command exits before the mint proper.
    rc = mint.main(
        [
            samples,
            str(tmp_path / "speedway"),
            "--fonts-root",
            sibling_root,
            "--suggest-donor",
        ]
    )
    assert rc == 0
    out = capsys.readouterr().out
    ranking = [l for l in out.splitlines() if "shape IoU" in l]
    assert len(ranking) == 1 and "vons" in ranking[0]
    assert "speedway" not in out  # the face being minted is excluded
    assert out.rstrip().endswith("suggest --donor vons")
