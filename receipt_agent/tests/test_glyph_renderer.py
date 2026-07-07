"""Tests for the glyph-stamping renderer (deterministic, no AWS).

Builds a tiny atlas from a synthetic raw image (same approach as
``test_glyph_atlas``), then renders synthesized receipt dicts and asserts ink is
stamped, the logo gate works, and missing-glyph fallback is invoked.
"""

from __future__ import annotations

import pytest
from PIL import Image, ImageDraw
from receipt_agent.agents.label_evaluator.rendering.font_profile import (
    MerchantFontProfile,
)
from receipt_agent.agents.label_evaluator.rendering.glyph_atlas import (
    build_glyph_atlas,
)
from receipt_agent.agents.label_evaluator.rendering.glyph_renderer import (
    GlyphRenderConfig,
    _apply_ink_density,
    _glyph_height_px,
    _glyph_variant_index,
    _line_barcode_digits,
    _line_is_logo,
    _line_is_rule,
    _pitch_norm,
    _prefer_font_for_char,
    _snap_to_pitch,
    _stamp_barcode,
    _thermal_finish,
    render_real_vs_glyph,
    render_receipt_glyphs,
    save_receipt_glyphs,
)

_W, _H = 320, 1600


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
    import math

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
        left = box["x"] * _W
        right = (box["x"] + box["width"]) * _W
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

    # Logo line + body lines covering the chars we render below.
    add(
        "V",
        0.30,
        0.94,
        0.05,
        0.045,
        line_id=1,
        word_id=1,
        letter_id=1,
        coverage=0.6,
    )
    add(
        "S",
        0.40,
        0.94,
        0.05,
        0.045,
        line_id=1,
        word_id=1,
        letter_id=2,
        coverage=0.6,
    )
    chars = "ABC123"
    for li, ytop in enumerate((0.80, 0.74, 0.68, 0.62), start=2):
        for wi, ch in enumerate(chars):
            add(
                ch,
                0.10 + wi * 0.05,
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


def _word(text, x0, y0, x1, y1, labels=None):
    return {"text": text, "bbox": [x0, y0, x1, y1], "labels": labels or []}


def _receipt():
    # 0-1000 space, y high-is-top.
    return {
        "lines": [
            {
                "line_id": 1,
                "words": [_word("ABC", 100, 950, 300, 985, ["MERCHANT_NAME"])],
            },  # tall logo line
            {
                "line_id": 2,
                "words": [
                    _word("ABC", 80, 800, 240, 820),
                    _word("123", 820, 800, 900, 820, ["LINE_TOTAL"]),
                ],
            },
        ]
    }


def _profile(char_width=0.02, font_height=0.02):
    return MerchantFontProfile(
        merchant_name="TestMart",
        receipt_count=1,
        font_height=font_height,
        char_width=char_width,
        char_aspect=char_width / font_height,
        line_pitch=None,
        price_column_x=None,
        dominant_style_label="test",
    )


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
    receipt = {
        "lines": [
            {
                "line_id": 1,
                "words": [_word("ABC", 100, 800, 240, 820, ["MERCHANT_NAME"])],
            },  # body-height
        ]
    }
    config = GlyphRenderConfig(width=300, height=700, noise=0.0, blur=0.0)
    # Should render glyphs (not crash, not stamp logo); ink present.
    image = render_receipt_glyphs(receipt, atlas, config=config)
    assert image.convert("L").getextrema()[0] < 150


def _tall(text, labels, x0=100):
    # A display-height word (height 30) near the top of the receipt.
    return _word(text, x0, 950, x0 + max(1, len(text)) * 12, 980, labels)


def test_logo_gate_matches_wordmark_and_rejects_mislabelled_lines():
    BODY = 10.0  # body_h_ref; tall words are 30 -> 3x, past the 1.6x gate
    # genuine wordmark line is stamped as the logo
    assert _line_is_logo([_tall("VONS", ["MERCHANT_NAME"])], BODY, "VONS")
    # a partial wordmark token still matches the full mark ("CVS" in "CVS pharmacy")
    assert _line_is_logo(
        [_tall("CVS", ["MERCHANT_NAME"])], BODY, "•CVS pharmacy"
    )
    # a mislabelled time / price / phone that happens to be tall + MERCHANT_NAME
    # tagged must NOT pull in the logo image (it does not match the wordmark)
    assert not _line_is_logo(
        [_tall("6:33", ["MERCHANT_NAME"])], BODY, "COSTCO"
    )
    assert not _line_is_logo(
        [_tall("17.49", ["MERCHANT_NAME"])], BODY, "COSTCO"
    )
    assert not _line_is_logo(
        [_tall("495-4938", ["MERCHANT_NAME"])], BODY, "•CVS pharmacy"
    )


def test_logo_gate_rejects_promo_sentence_mentioning_merchant():
    BODY = 10.0
    # "to WIN a $250 Sprouts gift card. Go to:" — tall, one word MERCHANT_NAME
    # tagged, but a sentence, so the logo image must not stamp over it.
    promo = [
        _tall("to", [], 60),
        _tall("WIN", [], 90),
        _tall("a", [], 140),
        _tall("$250", [], 170),
        _tall("Sprouts", ["MERCHANT_NAME"], 230),
        _tall("gift", [], 320),
        _tall("card", [], 380),
        _tall("Go", [], 440),
    ]
    assert not _line_is_logo(promo, BODY, "SPROUTS")


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


def test_numeric_body_source_uses_fallback_for_amount_tokens():
    atlas = _atlas()
    calls = []

    def fallback(char, style, px_height):
        calls.append(char)
        return Image.new("RGBA", (px_height // 2, px_height), (0, 0, 0, 255))

    receipt = {"words": [_word("12.30", 100, 800, 260, 830, ["LINE_TOTAL"])]}
    config = GlyphRenderConfig(
        width=300,
        height=700,
        noise=0.0,
        blur=0.0,
        body_glyph_source="numeric",
    )
    render_receipt_glyphs(receipt, atlas, config=config, fallback=fallback)
    assert calls == list("12.30")


def test_numeric_body_source_keeps_plain_words_on_atlas():
    atlas = _atlas()
    calls = []

    def fallback(char, style, px_height):
        calls.append(char)
        return Image.new("RGBA", (px_height // 2, px_height), (0, 0, 0, 255))

    receipt = {"words": [_word("ABC", 100, 800, 260, 830)]}
    config = GlyphRenderConfig(
        width=300,
        height=700,
        noise=0.0,
        blur=0.0,
        body_glyph_source="numeric",
    )
    render_receipt_glyphs(receipt, atlas, config=config, fallback=fallback)
    assert calls == []


def test_numeric_body_source_prefers_font_for_atlas_confusables():
    numeric = GlyphRenderConfig(body_glyph_source="numeric")
    atlas = GlyphRenderConfig(body_glyph_source="atlas")

    assert _prefer_font_for_char(numeric, "o", word_prefers_font=False)
    assert _prefer_font_for_char(numeric, "W", word_prefers_font=False)
    assert _prefer_font_for_char(numeric, "i", word_prefers_font=False)
    assert _prefer_font_for_char(numeric, "5", word_prefers_font=False)
    assert not _prefer_font_for_char(numeric, "A", word_prefers_font=False)
    assert not _prefer_font_for_char(atlas, "o", word_prefers_font=False)
    assert _prefer_font_for_char(atlas, "A", word_prefers_font=True)


def test_bold_line_renders_without_error():
    atlas = _atlas()
    receipt = {
        "lines": [
            {
                "line_id": 1,
                "words": [
                    _word("MEMBER", 80, 800, 300, 820),
                    _word("SAVINGS", 320, 800, 520, 820),
                ],
            },
        ]
    }
    config = GlyphRenderConfig(width=400, height=600, noise=0.0, blur=0.0)
    image = render_receipt_glyphs(receipt, atlas, config=config)
    assert image.size == (400, 600)


def test_save_receipt_glyphs(tmp_path):
    out = str(tmp_path / "nested" / "r.png")
    path = save_receipt_glyphs(
        _receipt(),
        _atlas(),
        out,
        config=GlyphRenderConfig(noise=0.0, blur=0.0),
    )
    with Image.open(path) as img:
        assert img.format == "PNG"


def test_save_and_comparison_helpers_accept_font_profile(tmp_path):
    config = GlyphRenderConfig(width=300, height=700, noise=0.0, blur=0.0)
    profile = _profile(char_width=0.018)

    out = str(tmp_path / "profiled.png")
    save_receipt_glyphs(
        _receipt(), _atlas(), out, profile=profile, config=config
    )
    with Image.open(out) as img:
        assert img.size == (300, 700)

    real = Image.new("RGB", (80, 160), (255, 255, 255))
    comparison = render_real_vs_glyph(
        real, _receipt(), _atlas(), profile=profile, config=config
    )
    assert comparison.height == 722  # render height plus label banner


def test_snap_to_pitch_uses_render_margin_as_grid_origin():
    # The renderer's pixel boxes already include config.margin. Fixed-pitch
    # snapping must preserve that origin, otherwise every line drifts by
    # margin % pitch and price columns no longer sit on the receipt grid.
    assert _snap_to_pitch(19.0, 6.0, origin=10.0) == 22.0
    assert _snap_to_pitch(19.0, 6.0, origin=0.0) == 18.0


def test_profile_pitch_is_clamped_to_receipt_geometry():
    # A merchant profile is an anchor, not permission to outgrow the row boxes.
    # The synthetic receipt's own word boxes show 0.01 advance; a noisy/wide
    # profile should be clipped near that measured pitch instead of causing
    # right-edge overflow in dense price columns.
    words = [
        _word("ABCD", 100, 800, 140, 820),
        _word("1234", 820, 800, 860, 820, ["LINE_TOTAL"]),
    ]
    assert _pitch_norm(words, 1000.0, _profile(char_width=0.03)) == 0.0105


def test_profile_height_is_clamped_to_row_geometry():
    assert (
        _glyph_height_px(
            line_h=12.0, inner_h=1000, font_h_norm=0.03, row_pitch_px=16.0
        )
        == 12.0
    )


def test_right_edge_overflow_is_clamped_inside_printable_margin():
    atlas = _atlas()
    config = GlyphRenderConfig(
        width=160,
        height=220,
        margin=10,
        noise=0.0,
        blur=0.0,
        ink_jitter=0,
        paper_realism=0.0,
    )
    receipt = {
        "lines": [
            {"line_id": 1, "words": [_word("ABC123", 900, 800, 1080, 830)]},
        ]
    }
    image = render_receipt_glyphs(
        receipt, atlas, config=config, coord_max=1000.0
    )
    gray = image.convert("L")
    assert any(value < 120 for value in gray.getdata())
    page_right = config.width - config.margin
    right_margin = [
        gray.getpixel((x, y))
        for x in range(page_right, config.width)
        for y in range(config.height)
    ]
    assert all(value >= 120 for value in right_margin)


def test_glyph_variant_index_uses_cleanest_crop_for_text_legibility():
    style = _atlas().style_for_role("body")
    assert style is not None
    config = GlyphRenderConfig(seed=77)
    word_seed = 12345

    first = _glyph_variant_index(
        config, style, bold=False, word_seed=word_seed, char="s"
    )
    second = _glyph_variant_index(
        config, style, bold=False, word_seed=word_seed, char="S"
    )
    assert first == second
    assert first == 0


def test_rule_lines_are_detected_as_structural_rules():
    assert _line_is_rule([_word("********", 100, 500, 300, 520)])
    assert _line_is_rule(
        [
            _word("----", 100, 500, 180, 520),
            _word("----", 190, 500, 270, 520),
        ]
    )
    assert not _line_is_rule([_word("TOTAL", 100, 500, 180, 520)])
    assert not _line_is_rule([_word("***", 100, 500, 130, 520)])


def test_barcode_digits_accept_long_and_grouped_numbers_only():
    assert (
        _line_barcode_digits(
            [
                _word("3509", 100, 200, 180, 220),
                _word("7155", 190, 200, 270, 220),
                _word("1559", 280, 200, 360, 220),
                _word("749120", 370, 200, 500, 220),
            ]
        )
        == "350971551559749120"
    )
    assert (
        _line_barcode_digits([_word("1234567890123", 100, 200, 300, 220)])
        is None
    )
    assert (
        _line_barcode_digits([_word("12345678901234A", 100, 200, 300, 220)])
        is None
    )


def test_stamp_barcode_draws_only_for_wide_barcode_lines():
    config = GlyphRenderConfig(
        width=300, height=300, margin=10, noise=0.0, blur=0.0
    )
    inner_w = config.width - config.margin * 2
    inner_h = config.height - config.margin * 2
    digits = "123456789012345678"

    wide = Image.new(
        "RGBA", (config.width, config.height), config.paper + (255,)
    )
    _stamp_barcode(
        wide,
        [_word(digits, 100, 500, 900, 520)],
        1000.0,
        config,
        inner_w,
        inner_h,
        digits,
    )
    wide_dark = sum(1 for px in wide.convert("L").getdata() if px < 80)
    assert wide_dark > 100

    narrow = Image.new(
        "RGBA", (config.width, config.height), config.paper + (255,)
    )
    _stamp_barcode(
        narrow,
        [_word(digits, 100, 500, 300, 520)],
        1000.0,
        config,
        inner_w,
        inner_h,
        digits,
    )
    assert sum(1 for px in narrow.convert("L").getdata() if px < 80) == 0

    off_center = Image.new(
        "RGBA", (config.width, config.height), config.paper + (255,)
    )
    _stamp_barcode(
        off_center,
        [_word(digits, 0, 500, 450, 520)],
        1000.0,
        config,
        inner_w,
        inner_h,
        digits,
    )
    assert sum(1 for px in off_center.convert("L").getdata() if px < 80) == 0

    occupied = Image.new(
        "RGBA", (config.width, config.height), config.paper + (255,)
    )
    ImageDraw.Draw(occupied).rectangle(
        [100, 130, 200, 138], fill=config.ink + (255,)
    )
    before = sum(1 for px in occupied.convert("L").getdata() if px < 80)
    _stamp_barcode(
        occupied,
        [_word(digits, 100, 500, 900, 520)],
        1000.0,
        config,
        inner_w,
        inner_h,
        digits,
    )
    assert (
        sum(1 for px in occupied.convert("L").getdata() if px < 80) == before
    )


def test_thermal_finish_paper_realism_is_deterministic_and_disableable():
    pytest.importorskip("numpy")
    base = Image.new("RGBA", (12, 12), (250, 249, 245, 255))
    ImageDraw.Draw(base).rectangle([3, 4, 8, 7], fill=(30, 30, 30, 255))

    flat_config = GlyphRenderConfig(
        width=12, height=12, blur=0.0, paper_realism=0.0
    )
    flat = _thermal_finish(base, flat_config)
    assert flat.mode == "RGB"
    assert flat.getpixel((0, 0)) == (250, 249, 245)

    realism_config = GlyphRenderConfig(
        width=12, height=12, blur=0.0, seed=123, paper_realism=0.8
    )
    first = _thermal_finish(base, realism_config)
    second = _thermal_finish(base, realism_config)
    assert first.mode == "RGB"
    assert first.tobytes() == second.tobytes()
    assert first.tobytes() != flat.tobytes()


def test_apply_ink_density_strengthens_alpha_and_clamps():
    alpha = Image.new("L", (3, 1))
    alpha.putdata([0, 100, 240])
    config = GlyphRenderConfig(ink_density=1.25)

    dense = _apply_ink_density(alpha, config)

    assert list(dense.getdata()) == [0, 125, 255]


def test_flat_receipt_with_degenerate_bboxes_does_not_crash():
    # Non-numeric / short / NaN bboxes in a FLAT word list must be skipped during
    # line grouping, not crash (matches receipt_renderer's defensive handling).
    atlas = _atlas()
    receipt = {
        "words": [
            {"text": "BAD", "bbox": [float("nan"), 0, 1, 1]},
            {"text": "STR", "bbox": "nope"},
            {"text": "SHORT", "bbox": [1, 2, 3]},
            {"text": "OK", "bbox": [100, 800, 200, 830]},
        ]
    }
    config = GlyphRenderConfig(width=300, height=400, noise=0.0, blur=0.0)
    image = render_receipt_glyphs(receipt, atlas, config=config)
    assert image.size == (300, 400)


def test_empty_receipt_returns_paper():
    config = GlyphRenderConfig(width=80, height=120, noise=0.0, blur=0.0)
    image = render_receipt_glyphs({"lines": []}, _atlas(), config=config)
    assert image.size == (80, 120)
