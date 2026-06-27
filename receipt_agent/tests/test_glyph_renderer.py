"""Tests for the glyph-stamping renderer (deterministic, no AWS).

Builds a tiny atlas from a synthetic raw image (same approach as
``test_glyph_atlas``), then renders synthesized receipt dicts and asserts ink is
stamped, the logo gate works, and missing-glyph fallback is invoked.
"""

from __future__ import annotations

from PIL import Image, ImageDraw

from receipt_agent.agents.label_evaluator.rendering.glyph_atlas import (
    build_glyph_atlas,
)
from receipt_agent.agents.label_evaluator.rendering.glyph_renderer import (
    GlyphRenderConfig,
    render_receipt_glyphs,
    save_receipt_glyphs,
)

_W, _H = 320, 1600


def _letter(char, x, y, w, h, *, line_id, word_id, letter_id):
    return {
        "text": char, "confidence": 0.99, "image_id": "img-1", "receipt_id": 1,
        "line_id": line_id, "word_id": word_id, "letter_id": letter_id,
        "bounding_box": {"x": x, "y": y, "width": w, "height": h},
    }


def _atlas():
    import math
    letters = []
    image = Image.new("RGB", (_W, _H), (250, 249, 246))
    draw = ImageDraw.Draw(image)

    def add(char, x, y, w, h, *, line_id, word_id, letter_id, coverage):
        lt = _letter(char, x, y, w, h, line_id=line_id, word_id=word_id,
                     letter_id=letter_id)
        box = lt["bounding_box"]
        left = box["x"] * _W
        right = (box["x"] + box["width"]) * _W
        top = (1.0 - box["y"] - box["height"]) * _H
        bottom = (1.0 - box["y"]) * _H
        frac = math.sqrt(coverage)
        iw, ih = (right - left) * frac, (bottom - top) * frac
        cx, cy = (left + right) / 2, (top + bottom) / 2
        draw.rectangle([cx - iw / 2, cy - ih / 2, cx + iw / 2, cy + ih / 2],
                       fill=(10, 10, 10))
        letters.append(lt)

    # Logo line + body lines covering the chars we render below.
    add("V", 0.30, 0.94, 0.05, 0.045, line_id=1, word_id=1, letter_id=1, coverage=0.6)
    add("S", 0.40, 0.94, 0.05, 0.045, line_id=1, word_id=1, letter_id=2, coverage=0.6)
    chars = "ABC123"
    for li, ytop in enumerate((0.80, 0.74, 0.68, 0.62), start=2):
        for wi, ch in enumerate(chars):
            add(ch, 0.10 + wi * 0.05, ytop, 0.022, 0.02,
                line_id=li, word_id=2, letter_id=wi + 1, coverage=0.22)
    atlas = build_glyph_atlas(
        [{"image_id": "img-1", "receipt_id": 1, "letters": letters,
          "raw_image": image}], "TestMart", min_samples=5)
    assert atlas is not None
    return atlas


def _word(text, x0, y0, x1, y1, labels=None):
    return {"text": text, "bbox": [x0, y0, x1, y1], "labels": labels or []}


def _receipt():
    # 0-1000 space, y high-is-top.
    return {"lines": [
        {"line_id": 1, "words": [_word("ABC", 100, 950, 300, 985,
                                       ["MERCHANT_NAME"])]},  # tall logo line
        {"line_id": 2, "words": [_word("ABC", 80, 800, 240, 820),
                                 _word("123", 820, 800, 900, 820,
                                       ["LINE_TOTAL"])]},
    ]}


def test_render_returns_image_of_config_size():
    config = GlyphRenderConfig(width=300, height=700, noise=0.0, blur=0.0)
    image = render_receipt_glyphs(_receipt(), _atlas(), config=config)
    assert isinstance(image, Image.Image)
    assert image.size == (300, 700)


def test_render_stamps_ink_on_paper():
    config = GlyphRenderConfig(width=300, height=700, noise=0.0, blur=0.0)
    image = render_receipt_glyphs(_receipt(), _atlas(), config=config)
    gray = image.convert("L")
    darks = [v for v in gray.getdata() if v < 120]
    assert len(darks) > 50  # real ink was stamped


def test_logo_gate_only_fires_on_tall_merchant_line():
    # A small MERCHANT_NAME line (same height as body) must NOT be replaced by the
    # logo image — only the genuinely tall display line is.
    atlas = _atlas()
    receipt = {"lines": [
        {"line_id": 1, "words": [_word("ABC", 100, 800, 240, 820,
                                       ["MERCHANT_NAME"])]},  # body-height
    ]}
    config = GlyphRenderConfig(width=300, height=700, noise=0.0, blur=0.0)
    # Should render glyphs (not crash, not stamp logo); ink present.
    image = render_receipt_glyphs(receipt, atlas, config=config)
    assert image.convert("L").getextrema()[0] < 150


def test_missing_glyph_invokes_fallback():
    atlas = _atlas()
    calls = []

    def fallback(char, style, px_height):
        calls.append(char)
        g = Image.new("RGBA", (px_height // 2, px_height), (0, 0, 0, 255))
        return g

    # 'Z' is not in the atlas -> fallback must be asked for it.
    receipt = {"words": [_word("Z", 100, 800, 160, 830)]}
    config = GlyphRenderConfig(width=300, height=700, noise=0.0, blur=0.0)
    render_receipt_glyphs(receipt, atlas, config=config, fallback=fallback)
    assert "Z" in calls


def test_bold_line_renders_without_error():
    atlas = _atlas()
    receipt = {"lines": [
        {"line_id": 1, "words": [_word("MEMBER", 80, 800, 300, 820),
                                 _word("SAVINGS", 320, 800, 520, 820)]},
    ]}
    config = GlyphRenderConfig(width=400, height=600, noise=0.0, blur=0.0)
    image = render_receipt_glyphs(receipt, atlas, config=config)
    assert image.size == (400, 600)


def test_save_receipt_glyphs(tmp_path):
    out = str(tmp_path / "nested" / "r.png")
    path = save_receipt_glyphs(_receipt(), _atlas(), out,
                               config=GlyphRenderConfig(noise=0.0, blur=0.0))
    with Image.open(path) as img:
        assert img.format == "PNG"


def test_flat_receipt_with_degenerate_bboxes_does_not_crash():
    # Non-numeric / short / NaN bboxes in a FLAT word list must be skipped during
    # line grouping, not crash (matches receipt_renderer's defensive handling).
    atlas = _atlas()
    receipt = {"words": [
        {"text": "BAD", "bbox": [float("nan"), 0, 1, 1]},
        {"text": "STR", "bbox": "nope"},
        {"text": "SHORT", "bbox": [1, 2, 3]},
        {"text": "OK", "bbox": [100, 800, 200, 830]},
    ]}
    config = GlyphRenderConfig(width=300, height=400, noise=0.0, blur=0.0)
    image = render_receipt_glyphs(receipt, atlas, config=config)
    assert image.size == (300, 400)


def test_empty_receipt_returns_paper():
    config = GlyphRenderConfig(width=80, height=120, noise=0.0, blur=0.0)
    image = render_receipt_glyphs({"lines": []}, _atlas(), config=config)
    assert image.size == (80, 120)
