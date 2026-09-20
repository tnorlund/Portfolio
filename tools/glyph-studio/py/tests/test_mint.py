"""Offline contracts for glyphstudio.mint (donor adoption + normalization)."""

from __future__ import annotations

import json
import os
import shutil

import pytest
from glyphstudio import mint
from glyphstudio.schema import load_glyphs

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
