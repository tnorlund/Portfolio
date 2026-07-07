"""Tests for the embedding-matched TTF fallback (M3).

Builds a small real-crop atlas, then checks the fallback font matcher and the
fallback glyph hook. A monospace TTF must be available; the suite skips if the
host has none (CI without system fonts)."""

from __future__ import annotations

import math

import pytest
from PIL import Image, ImageDraw

from receipt_agent.agents.label_evaluator.rendering import glyph_ttf_fallback
from receipt_agent.agents.label_evaluator.rendering.glyph_atlas import (
    build_glyph_atlas,
)
from receipt_agent.agents.label_evaluator.rendering.glyph_ttf_fallback import (
    available_candidates,
    make_ttf_fallback,
    match_fallback_font,
)

_W, _H = 320, 1600
_HAS_TTF = bool(available_candidates())
_skip_no_ttf = pytest.mark.skipif(
    not _HAS_TTF, reason="no monospace TTF on host"
)


def _letter(char, x, y, w, h, *, line_id, word_id, letter_id):
    return {
        "text": char,
        "confidence": 0.99,
        "image_id": "img-1",
        "receipt_id": 1,
        "line_id": line_id,
        "word_id": word_id,
        "letter_id": letter_id,
        "bounding_box": {"x": x, "y": y, "width": w, "height": h},
    }


def _atlas():
    letters = []
    image = Image.new("RGB", (_W, _H), (250, 249, 246))
    draw = ImageDraw.Draw(image)

    def add(char, x, y, w, h, *, line_id, word_id, letter_id, coverage):
        lt = _letter(
            char,
            x,
            y,
            w,
            h,
            line_id=line_id,
            word_id=word_id,
            letter_id=letter_id,
        )
        box = lt["bounding_box"]
        left, right = box["x"] * _W, (box["x"] + box["width"]) * _W
        top = (1.0 - box["y"] - box["height"]) * _H
        bottom = (1.0 - box["y"]) * _H
        frac = math.sqrt(coverage)
        iw, ih = (right - left) * frac, (bottom - top) * frac
        cx, cy = (left + right) / 2, (top + bottom) / 2
        draw.rectangle(
            [cx - iw / 2, cy - ih / 2, cx + iw / 2, cy + ih / 2],
            fill=(10, 10, 10),
        )
        letters.append(lt)

    chars = "ABCEHOST0123"
    for li, ytop in enumerate((0.80, 0.74, 0.68), start=2):
        for wi, ch in enumerate(chars):
            add(
                ch,
                0.06 + wi * 0.06,
                ytop,
                0.022,
                0.02,
                line_id=li,
                word_id=2,
                letter_id=wi + 1,
                coverage=0.22,
            )
    atlas = build_glyph_atlas(
        [
            {
                "image_id": "img-1",
                "receipt_id": 1,
                "letters": letters,
                "raw_image": image,
            }
        ],
        "TestMart",
        min_samples=5,
    )
    assert atlas is not None
    return atlas


@_skip_no_ttf
def test_match_picks_an_available_font_with_scores():
    match = match_fallback_font(_atlas())
    assert match.font_path in available_candidates()
    assert match.scores  # one score per candidate
    assert 0.0 <= match.score <= 1.0
    # The chosen font is the argmax of the per-font scores.
    assert match.score == max(match.scores.values())


def test_available_candidates_requires_fixed_pitch(monkeypatch):
    candidates = ("/fonts/thermal-mono.ttf", "/fonts/script.ttf")
    monkeypatch.setattr(
        glyph_ttf_fallback.os.path, "exists", lambda path: True
    )
    monkeypatch.setattr(
        glyph_ttf_fallback,
        "_is_fixed_pitch_font",
        lambda path: path.endswith("thermal-mono.ttf"),
    )

    assert available_candidates(candidates) == ["/fonts/thermal-mono.ttf"]


@_skip_no_ttf
def test_fallback_renders_missing_char_glyph():
    atlas = _atlas()
    fallback = make_ttf_fallback(atlas)
    style = atlas.styles["body"]
    glyph = fallback("Z", style, 30)  # 'Z' is not in the atlas
    assert glyph is not None
    assert glyph.mode == "RGBA"
    # Real ink drawn, sized roughly to the requested cap height.
    assert max(glyph.getchannel("A").getdata()) > 200
    assert 12 <= glyph.height <= 60


@_skip_no_ttf
def test_fallback_reserves_fixed_monospace_advance():
    atlas = _atlas()
    fallback = make_ttf_fallback(atlas)
    style = atlas.styles["body"]
    narrow = fallback("I", style, 30)
    wide = fallback("W", style, 30)

    assert narrow is not None
    assert wide is not None
    assert narrow.width == wide.width
    narrow_bbox = narrow.getchannel("A").getbbox() or (0, 0, 0, 0)
    assert narrow_bbox[2] < narrow.width


@_skip_no_ttf
def test_fallback_skips_blank_and_degenerate():
    atlas = _atlas()
    fallback = make_ttf_fallback(atlas)
    style = atlas.styles["body"]
    assert fallback(" ", style, 30) is None
    assert fallback("Z", style, 0) is None


@_skip_no_ttf
def test_custom_score_font_hook_is_used():
    # A scorer that always prefers the last candidate must drive the choice.
    cands = available_candidates()
    target = cands[-1]

    def scorer(path, probe):
        return 1.0 if path == target else 0.0

    match = match_fallback_font(_atlas(), score_font=scorer)
    assert match.font_path == target


@_skip_no_ttf
def test_fallback_integrates_with_renderer():
    from receipt_agent.agents.label_evaluator.rendering.glyph_renderer import (
        GlyphRenderConfig,
        render_receipt_glyphs,
    )

    atlas = _atlas()
    fallback = make_ttf_fallback(atlas)
    # 'Z'/'Q' missing from atlas -> fallback fills them; render must not crash.
    receipt = {
        "words": [{"text": "ZQ", "bbox": [100, 800, 220, 830], "labels": []}]
    }
    cfg = GlyphRenderConfig(width=300, height=700, noise=0.0, blur=0.0)
    image = render_receipt_glyphs(
        receipt, atlas, config=cfg, fallback=fallback
    )
    assert image.convert("L").getextrema()[0] < 150  # ink present
