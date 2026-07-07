"""Tests for the style-aware glyph atlas (deterministic, no AWS).

A synthetic raw receipt image is painted with three line tiers — a tall logo
line, thin-ink regular body lines, and heavy-ink bold lines — so the atlas's
weight/logo separation, real-crop retention, lookup, and round-trip persistence
can be checked without touching Dynamo/S3.
"""

from __future__ import annotations

from PIL import Image, ImageDraw
from receipt_agent.agents.label_evaluator.rendering.glyph_atlas import (
    GlyphAtlas,
    _is_promo_text,
    _logo_match_score,
    build_glyph_atlas,
    extract_glyph_image,
    load_atlas,
    save_atlas,
)

_W, _H = 320, 1600


def _letter(char, x, y, w, h, *, line_id, word_id, letter_id):
    """A normalized (bottom-left origin) OCR letter dict #994 accepts."""
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


def _paint(draw, letter, *, ink_coverage):
    """Paint a letter's ink into the raw image at its (flipped) pixel box."""
    box = letter["bounding_box"]
    left = box["x"] * _W
    right = (box["x"] + box["width"]) * _W
    top = (1.0 - box["y"] - box["height"]) * _H
    bottom = (1.0 - box["y"]) * _H
    bw, bh = right - left, bottom - top
    # Center an ink rectangle covering `ink_coverage` of the box area.
    import math

    frac = math.sqrt(ink_coverage)
    iw, ih = bw * frac, bh * frac
    cx, cy = (left + right) / 2, (top + bottom) / 2
    draw.rectangle(
        [cx - iw / 2, cy - ih / 2, cx + iw / 2, cy + ih / 2], fill=(10, 10, 10)
    )


def _build_inputs():
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
        _paint(draw, lt, ink_coverage=coverage)
        letters.append(lt)

    # Logo line near the top: tall (h=0.045) display chars (~3x body height).
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

    # Regular body lines: realistic narrow glyphs (w=0.022), thin ink, height 0.02.
    chars = "AB12"
    for li, ytop in enumerate((0.80, 0.74, 0.68), start=2):
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
                coverage=0.20,
            )

    # Bold lines: heavy ink (higher coverage), same glyph box.
    for li, ytop in enumerate((0.55, 0.49), start=5):
        for wi, ch in enumerate(chars):
            add(
                ch,
                0.10 + wi * 0.05,
                ytop,
                0.022,
                0.02,
                line_id=li,
                word_id=3,
                letter_id=wi + 1,
                coverage=0.55,
            )

    return [
        {
            "image_id": "img-1",
            "receipt_id": 1,
            "letters": letters,
            "raw_image": image,
        }
    ]


def _atlas() -> GlyphAtlas:
    atlas = build_glyph_atlas(_build_inputs(), "TestMart", min_samples=5)
    assert atlas is not None
    return atlas


def test_extract_glyph_image_returns_tight_rgba_ink():
    image = Image.new("RGB", (60, 60), (255, 255, 255))
    ImageDraw.Draw(image).rectangle([20, 20, 40, 50], fill=(0, 0, 0))
    box = {"x": 0.2, "y": 0.2, "width": 0.4, "height": 0.5}
    glyph = extract_glyph_image(image, box, y_origin="top_left")
    assert glyph is not None
    assert glyph.mode == "RGBA"
    # Tight to ink: bbox of alpha equals the whole crop.
    assert glyph.getbbox() == (0, 0, glyph.width, glyph.height)
    # The ink is opaque, the background fully transparent somewhere is N/A after
    # the tight crop, but max alpha must be high (real ink retained).
    assert max(glyph.getchannel("A").getdata()) > 200


def test_extract_glyph_image_dense_glyph_not_inverted():
    """A glyph that fills most of its box (more ink than paper) must NOT flip
    polarity and turn the paper margin into ink — the regression codex flagged.
    """
    image = Image.new("RGB", (40, 40), (250, 250, 250))
    # Ink fills most of the box (a thin paper border remains): ink is the
    # majority class, so the old "ink = minority" heuristic would wrongly flip.
    ImageDraw.Draw(image).rectangle([6, 6, 34, 34], fill=(8, 8, 8))
    box = {"x": 0.1, "y": 0.1, "width": 0.8, "height": 0.8}
    glyph = extract_glyph_image(
        image, box, y_origin="top_left", expand_x_ratio=0.0, expand_y_ratio=0.0
    )
    assert glyph is not None
    alpha = glyph.getchannel("A")
    # The dark block is ink (opaque); had polarity flipped, the corners (paper)
    # would be opaque and the center transparent. Check the center is opaque.
    cx, cy = glyph.width // 2, glyph.height // 2
    assert alpha.getpixel((cx, cy)) > 200


def test_extract_glyph_image_handles_inverted_ink():
    # Light ink on dark paper -> still produces ink-as-alpha after polarity flip.
    image = Image.new("RGB", (60, 60), (15, 15, 15))
    ImageDraw.Draw(image).rectangle([20, 20, 40, 50], fill=(240, 240, 240))
    box = {"x": 0.2, "y": 0.2, "width": 0.4, "height": 0.5}
    glyph = extract_glyph_image(image, box, y_origin="top_left")
    assert glyph is not None
    assert max(glyph.getchannel("A").getdata()) > 200


def test_atlas_separates_body_and_bold_weight():
    atlas = _atlas()
    assert "body" in atlas.styles
    assert "bold" in atlas.styles
    body = atlas.styles["body"]
    bold = atlas.styles["bold"]
    # Bold detected via higher ink density than the body baseline.
    assert bold.median_ink_ratio > body.median_ink_ratio
    assert bold.weight == "bold"
    assert body.weight == "regular"


def test_atlas_captures_logo_and_excludes_it_from_body():
    atlas = _atlas()
    assert atlas.logo is not None
    assert atlas.logo_text in ("VS", "SV")  # order by center-x
    # The tall logo glyphs (V, S) must not pollute the body char set.
    assert "V" not in atlas.styles["body"].chars()


def test_tall_promo_line_does_not_replace_logo():
    inputs = _build_inputs()
    letters = inputs[0]["letters"]
    image = inputs[0]["raw_image"]
    draw = ImageDraw.Draw(image)
    x = 0.05
    for word_id, word in enumerate(("MEMBER", "SAVINGS"), start=1):
        for i, ch in enumerate(word, start=1):
            letter = _letter(
                ch,
                x,
                0.88,
                0.030,
                0.07,
                line_id=77,
                word_id=word_id,
                letter_id=i,
            )
            _paint(draw, letter, ink_coverage=0.6)
            letters.append(letter)
            x += 0.038
        x += 0.045

    assert _is_promo_text("SAVE50%OFF")
    assert _is_promo_text("MEMBER SAVINGS")
    assert _is_promo_text("MEMBERSAVINGS")
    assert not _is_promo_text("TARGET")
    assert not _is_promo_text("OFFICE DEPOT")
    assert _logo_match_score("VONS", "Vons") == 1.0
    assert _logo_match_score("SAVE MART", "Save Mart") == 1.0
    assert _logo_match_score("SAVE MART", "Save Mart Supermarkets") == 1.0
    assert _logo_match_score("HOT TOPIC", "Hot Topic Inc") == 1.0
    assert _logo_match_score("MART", "Save Mart") < 0.75
    assert _logo_match_score("SAVE", "Save Mart Supermarkets") < 0.75
    assert _logo_match_score("TOPIC", "Hot Topic Inc") < 0.75
    assert _logo_match_score("SAVE BIG", "Save Mart Supermarkets") < 0.75

    atlas = build_glyph_atlas(inputs, "TestMart", min_samples=5)
    assert atlas is not None
    assert atlas.logo_text in ("VS", "SV")


def test_atlas_covers_expected_body_chars_with_real_crops():
    atlas = _atlas()
    for ch in "AB12":
        crop = atlas.glyph(ch, role="body")
        assert crop is not None, ch
        assert crop.image.mode == "RGBA"
        assert crop.width >= 1 and crop.height >= 1


def test_bold_lookup_falls_back_to_body_for_missing_char():
    atlas = _atlas()
    # '@' is in neither style; bold lookup returns None without raising.
    assert atlas.glyph("@", bold=True) is None
    # A char present in body but (hypothetically) not bold still resolves.
    assert atlas.glyph("A", bold=True) is not None


def test_variation_report_shape():
    report = _atlas().variation_report()
    assert report["merchant_name"] == "TestMart"
    assert report["logo_present"] is True
    assert "body" in report["styles"]
    assert report["styles"]["bold"]["weight"] == "bold"
    assert report["covered_char_count"] >= 4


def test_save_and_load_atlas_round_trips(tmp_path):
    atlas = _atlas()
    save_atlas(atlas, str(tmp_path))
    loaded = load_atlas(str(tmp_path))
    assert loaded.merchant_name == atlas.merchant_name
    assert set(loaded.styles) == set(atlas.styles)
    assert loaded.logo is not None
    assert loaded.covered_chars() == atlas.covered_chars()
    # Glyph images survive the round trip as RGBA.
    crop = loaded.glyph("A", role="body")
    assert crop is not None and crop.image.mode == "RGBA"


def test_oversized_letter_box_is_rejected():
    """A 'letter' whose box spans a whole word must not enter the atlas."""
    inputs = _build_inputs()
    letters = inputs[0]["letters"]
    image = inputs[0]["raw_image"]
    draw = ImageDraw.Draw(image)
    # A bogus 'Z' letter with a word-wide box (10x the median glyph width).
    bogus = _letter(
        "Z", 0.05, 0.30, 0.80, 0.02, line_id=99, word_id=9, letter_id=1
    )
    _paint(draw, bogus, ink_coverage=0.4)
    letters.append(bogus)
    atlas = build_glyph_atlas(inputs, "TestMart", min_samples=5)
    assert atlas is not None
    # 'Z' only ever appears as the bogus word-wide box; rejecting it means no 'Z'
    # glyph enters the atlas at all.
    assert "Z" not in atlas.covered_chars()


def test_real_thermal_dash_passes_aspect_guard():
    """On real thermal receipts a tight-ink hyphen is ~1.2 aspect (thick mark),
    so it passes the char-agnostic aspect guard with no special-case exemption —
    and keeping the guard char-agnostic leaves no merge hole for a '-x' crop.
    """
    inputs = _build_inputs()
    letters = inputs[0]["letters"]
    image = inputs[0]["raw_image"]
    draw = ImageDraw.Draw(image)
    for li, ytop in enumerate((0.62, 0.60), start=20):
        dash = _letter(
            "-", 0.30, ytop, 0.022, 0.02, line_id=li, word_id=7, letter_id=1
        )
        box = dash["bounding_box"]
        left = box["x"] * _W
        right = (box["x"] + box["width"]) * _W
        midy = (1.0 - box["y"] - box["height"] / 2) * _H
        # A thick dash mark (~1.3 aspect), like the real Vons '-' crops.
        draw.rectangle(
            [left + 1, midy - 3, right - 1, midy + 3], fill=(10, 10, 10)
        )
        letters.append(dash)
    atlas = build_glyph_atlas(inputs, "TestMart", min_samples=5)
    assert atlas is not None
    crop = atlas.glyph("-")
    assert crop is not None
    assert crop.aspect <= 1.7  # naturally passes; no exemption needed


def test_edge_sliver_is_trimmed():
    """A thin detached ink column at a glyph's edge (neighbour bleed) is removed,
    while the main glyph body is kept."""
    image = Image.new("RGB", (60, 40), (250, 250, 250))
    d = ImageDraw.Draw(image)
    d.rectangle([18, 10, 40, 34], fill=(8, 8, 8))  # main glyph body
    d.rectangle(
        [6, 12, 7, 32], fill=(8, 8, 8)
    )  # 1px detached sliver, gap between
    box = {"x": 0.05, "y": 0.05, "width": 0.9, "height": 0.85}
    trimmed = extract_glyph_image(
        image,
        box,
        y_origin="top_left",
        expand_x_ratio=0.0,
        expand_y_ratio=0.0,
        trim_edge_slivers=True,
    )
    untrimmed = extract_glyph_image(
        image,
        box,
        y_origin="top_left",
        expand_x_ratio=0.0,
        expand_y_ratio=0.0,
        trim_edge_slivers=False,
    )
    assert trimmed is not None and untrimmed is not None
    # Trimming drops the sliver + the gap, so the trimmed glyph is narrower.
    assert trimmed.width < untrimmed.width


def test_vertically_split_glyph_not_trimmed():
    """A char split only vertically (like ':') is a single column-run, so the
    sliver trim must leave it intact."""
    image = Image.new("RGB", (40, 60), (250, 250, 250))
    d = ImageDraw.Draw(image)
    d.rectangle([16, 8, 24, 16], fill=(8, 8, 8))  # top dot
    d.rectangle([16, 40, 24, 48], fill=(8, 8, 8))  # bottom dot
    box = {"x": 0.05, "y": 0.05, "width": 0.9, "height": 0.9}
    glyph = extract_glyph_image(
        image, box, y_origin="top_left", expand_x_ratio=0.0, expand_y_ratio=0.0
    )
    assert glyph is not None
    # Both dots retained: the glyph spans the full vertical extent of the marks.
    assert glyph.height >= 30


def test_build_returns_none_without_usable_receipts():
    assert build_glyph_atlas([], "Empty") is None
    assert (
        build_glyph_atlas(
            [
                {
                    "image_id": "x",
                    "receipt_id": 1,
                    "letters": [],
                    "raw_image": None,
                }
            ],
            "Empty",
        )
        is None
    )
