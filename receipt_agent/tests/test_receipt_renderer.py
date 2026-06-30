"""Tests for the receipt PNG renderer (deterministic, no AWS)."""

from __future__ import annotations

import os

from PIL import Image

from receipt_agent.agents.label_evaluator.rendering.font_profile import (
    MerchantFontProfile,
)
from receipt_agent.agents.label_evaluator.rendering.receipt_renderer import (
    RenderConfig,
    _condense_factor,
    _detect_coord_max,
    _to_pixel_box,
    render_real_vs_synthetic,
    render_receipt,
    save_receipt_png,
)


def _word(text, x0, y0, x1, y1, labels=None):
    return {
        "text": text,
        "bbox": [x0, y0, x1, y1],
        "labels": labels or [],
    }


def _synthetic_receipt():
    # 0-1000 space, y high-is-top. Two rows: a product+price near the top, a
    # total lower down.
    return {
        "lines": [
            {
                "line_id": 1,
                "words": [
                    _word("MILK", 80, 900, 220, 930, ["PRODUCT_NAME"]),
                    _word("3.49", 820, 900, 900, 930, ["LINE_TOTAL"]),
                ],
            },
            {
                "line_id": 2,
                "words": [
                    _word("TOTAL", 80, 820, 240, 850, ["GRAND_TOTAL"]),
                    _word("3.49", 820, 820, 900, 850, ["GRAND_TOTAL"]),
                ],
            },
        ]
    }


def test_render_receipt_returns_image_of_config_size():
    config = RenderConfig(width=300, height=700)
    image = render_receipt(_synthetic_receipt(), config=config)
    assert isinstance(image, Image.Image)
    assert image.size == (300, 700)


def test_render_receipt_draws_ink_on_background():
    config = RenderConfig(width=300, height=700)
    image = render_receipt(_synthetic_receipt(), config=config)
    colors = image.getcolors(maxcolors=100000) or []
    # More than one color → something was drawn over the background.
    assert len(colors) > 1


def test_top_of_receipt_renders_above_bottom():
    # y high-is-top: the row at y~900 must paint higher (smaller pixel y) than
    # the row at y~820.
    config = RenderConfig(width=300, height=700, background=(255, 255, 255))
    receipt = {
        "lines": [
            {"line_id": 1, "words": [_word("TOPLINE", 80, 900, 400, 940)]},
            {"line_id": 2, "words": [_word("BOTLINE", 80, 60, 400, 100)]},
        ]
    }
    image = render_receipt(receipt, config=config)
    gray = image.convert("L")
    px = gray.load()
    # Find topmost and bottommost dark rows.
    dark_rows = [
        y
        for y in range(gray.height)
        for x in range(gray.width)
        if px[x, y] < 128
    ]
    assert dark_rows, "expected dark ink"
    top_dark = min(dark_rows)
    bottom_dark = max(dark_rows)
    # The TOPLINE ink should sit in the upper half, BOTLINE in the lower half.
    assert top_dark < gray.height / 2 < bottom_dark


def test_detect_coord_max_normalized_vs_pixel():
    norm = [{"bbox": [0.1, 0.8, 0.3, 0.83]}]
    pixel = [{"bbox": [80, 900, 220, 930]}]
    assert _detect_coord_max(norm) == 1.0
    assert _detect_coord_max(pixel) == 1000.0


def test_to_pixel_box_flips_y():
    config = RenderConfig(width=100, height=100, margin=0)
    # A box near the top (y~990) should map near pixel-y 0.
    box = _to_pixel_box([0, 980, 1000, 1000], 1000.0, config, 100, 100)
    assert box is not None
    left, top, right, bottom = box
    assert top < 5
    assert left == 0 and right == 100


def test_condense_factor_clamped():
    # Very wide aspect clamps to the upper bound; missing profile -> 1.0.
    wide = MerchantFontProfile(
        merchant_name="M", receipt_count=1, font_height=0.01,
        char_width=0.05, char_aspect=5.0, line_pitch=0.02,
        price_column_x=0.8, dominant_style_label="x",
    )
    assert _condense_factor(wide) == 1.6
    assert _condense_factor(None) == 1.0


def test_render_with_profile_and_label_colors():
    profile = MerchantFontProfile(
        merchant_name="M", receipt_count=1, font_height=0.013,
        char_width=0.023, char_aspect=1.7, line_pitch=0.015,
        price_column_x=0.85, dominant_style_label="regular wide mixed",
    )
    config = RenderConfig(color_by_label=True, draw_price_column=True)
    image = render_receipt(_synthetic_receipt(), profile=profile, config=config)
    # Red ink (LINE_TOTAL/GRAND_TOTAL) should appear somewhere.
    colors = {c for _, c in (image.getcolors(maxcolors=200000) or [])}
    assert any(r > 150 and g < 90 and b < 90 for (r, g, b) in colors)


def test_render_real_vs_synthetic_is_wider_than_single():
    config = RenderConfig(width=200, height=500)
    single = render_receipt(_synthetic_receipt(), config=config)
    combined = render_real_vs_synthetic(
        _synthetic_receipt(), _synthetic_receipt(), config=config
    )
    assert combined.width > single.width
    assert combined.height >= single.height


def test_save_receipt_png(tmp_path):
    out = os.path.join(str(tmp_path), "nested", "receipt.png")
    path = save_receipt_png(_synthetic_receipt(), out)
    assert os.path.exists(path)
    with Image.open(path) as img:
        assert img.format == "PNG"


def test_render_empty_receipt_still_returns_image():
    image = render_receipt({"lines": []}, config=RenderConfig(width=50, height=80))
    assert image.size == (50, 80)


def test_render_skips_nonfinite_and_degenerate_boxes():
    receipt = {
        "words": [
            {"text": "BAD", "bbox": [float("nan"), 0, 1, 1]},
            {"text": "SHORT", "bbox": [1, 2, 3]},
            {"text": "STR", "bbox": "nope"},
            {"text": "GOOD", "bbox": [80, 900, 400, 940]},
        ]
    }
    # Must not raise; non-finite/degenerate boxes are skipped.
    image = render_receipt(receipt, config=RenderConfig(width=200, height=400))
    assert image.size == (200, 400)


def test_color_by_label_handles_bio_prefixes():
    receipt = {
        "words": [
            {"text": "3.49", "bbox": [820, 900, 900, 930],
             "labels": ["B-LINE_TOTAL"]},
        ]
    }
    image = render_receipt(receipt, config=RenderConfig(color_by_label=True))
    colors = {c for _, c in (image.getcolors(maxcolors=200000) or [])}
    # The B-LINE_TOTAL token should render in red despite the BIO prefix.
    assert any(r > 150 and g < 90 and b < 90 for (r, g, b) in colors)


def test_grid_mode_renders_and_is_deterministic():
    receipt = _synthetic_receipt()
    cfg = RenderConfig(width=300, height=600, grid_mode=True,
                       min_font_px=9, max_font_px=28)
    a = render_receipt(receipt, config=cfg, coord_max=1000.0)
    b = render_receipt(receipt, config=cfg, coord_max=1000.0)
    assert a.size == (300, 600)
    assert a.tobytes() == b.tobytes()  # deterministic


def test_grid_mode_aligns_line_total_column():
    # Two LINE_TOTAL tokens of different lengths sharing a right edge should
    # land on the same grid column (right-anchored prices).
    from receipt_agent.agents.label_evaluator.rendering.receipt_grid import (
        GridSpec, token_start_col,
    )
    spec = GridSpec(cell_w=7.0, cell_h=15.0, font_px=14, grid_left=10.0)
    # right edge identical, lengths differ
    sc_a = token_start_col("1.99", left=860.0, right=900.0, spec=spec)
    sc_b = token_start_col("12.99", left=853.0, right=900.0, spec=spec)
    right_a = spec.grid_left + (sc_a + len("1.99")) * spec.cell_w
    right_b = spec.grid_left + (sc_b + len("12.99")) * spec.cell_w
    assert abs(right_a - right_b) < spec.cell_w  # within one cell


def test_grid_mode_disabled_by_default():
    assert RenderConfig().grid_mode is False


def test_draw_grid_line_separates_flag_after_right_anchored_price():
    """Tight-gap contract part (c): the de-glue lives in the render-time cursor.

    A tax flag whose SOURCE bbox butts up against (or overlaps) a right-anchored
    price column must still render at least one cell PAST the price's right edge.
    This is what lets the synthesizer emit a TIGHT source gap after a price
    instead of a bloated full-cell gap. We capture the column each token is drawn
    at and assert the flag clears the price's drawn right edge.
    """
    from receipt_agent.agents.label_evaluator.rendering import receipt_grid as rg

    spec = rg.GridSpec(cell_w=7.0, cell_h=15.0, font_px=14, grid_left=10.0)
    ink = (0, 0, 0)
    # name (left), price (right-anchored to col ~127), flag whose own source left
    # would land it BEFORE the price's right edge (glued) without the cursor fix.
    name = rg.GridWord(left=40.0, top=100.0, right=180.0, bottom=120.0, text="MILK", ink=ink)
    price = rg.GridWord(left=820.0, top=100.0, right=900.0, bottom=120.0, text="$4.39", ink=ink)
    flag = rg.GridWord(left=885.0, top=100.0, right=914.0, bottom=120.0, text="F", ink=ink)

    drawn: list[tuple[str, int]] = []
    orig = rg.draw_token_chars
    try:
        rg.draw_token_chars = lambda draw, text, start_col, *a, **k: drawn.append(
            (text, start_col)
        )
        rg.draw_grid_line(
            draw=None,
            line=[name, price, flag],
            baseline_y=115.0,
            spec=spec,
            font=None,
        )
    finally:
        rg.draw_token_chars = orig

    starts = {text: col for text, col in drawn}
    price_end = starts["$4.39"] + rg.drawn_cell_count("$4.39")
    # The price keeps its right-anchored column.
    assert starts["$4.39"] == round((900.0 - 10.0) / 7.0) - rg.drawn_cell_count("$4.39")
    # The flag renders at least one full cell past the price's right edge.
    assert starts["F"] >= price_end + 1


# Real Target summary cluster (0-1000 space, y high-is-top) -- the v5 floor case
# where the renderer fused three printed lines into one row ("NV TAX 8.37500 ...
# TOTAL"). Each printed line keeps its own source_line id.
_TARGET_SUMMARY = [
    # line 30: SUBTOTAL row
    ("SUBTOTAL", 527, 530, 715, 547, 30),
    ("$8.07", 916, 527, 971, 545, 30),
    # line 31: tax row (a left amount cluster + an "on $8.07" reference)
    ("T = NV TAX 8.37500", 87, 514, 531, 534, 31),
    ("on $8.07", 522, 512, 715, 531, 31),
    # line 32: grand total
    ("TOTAL", 594, 496, 710, 514, 32),
    ("$8.07", 916, 496, 971, 514, 32),
]


def _target_summary_grid_words(spec):
    """Build the Target summary cluster as pixel-space GridWords for `spec`."""
    from receipt_agent.agents.label_evaluator.rendering import receipt_grid as rg

    cfg = RenderConfig(width=460, height=1100, grid_mode=True,
                       min_font_px=9, max_font_px=28)
    inner_w = cfg.width - 2 * cfg.margin
    inner_h = cfg.height - 2 * cfg.margin
    words = []
    for text, x0, y0, x1, y1, line_id in _TARGET_SUMMARY:
        px = _to_pixel_box([x0, y0, x1, y1], 1000.0, cfg, inner_w, inner_h)
        left, top, right, bottom = px
        words.append(rg.GridWord(left=left, top=top, right=right, bottom=bottom,
                                 text=text, ink=(0, 0, 0), source_line=line_id))
    return words


def _target_summary_spec():
    from receipt_agent.agents.label_evaluator.rendering import receipt_grid as rg
    from PIL import Image, ImageDraw
    cfg = RenderConfig(width=460, height=1100, grid_mode=True,
                       min_font_px=9, max_font_px=28)
    inner_w = cfg.width - 2 * cfg.margin
    inner_h = cfg.height - 2 * cfg.margin
    draw = ImageDraw.Draw(Image.new("RGB", (cfg.width, cfg.height)))
    base = rg.build_grid_spec(None, inner_w, inner_h, cfg)
    from receipt_agent.agents.label_evaluator.rendering.receipt_renderer import (
        _load_grid_font,
    )
    font = _load_grid_font(base.font_px, cfg)
    adv = rg.glyph_advance(draw, font)
    return rg.build_grid_spec(None, inner_w, inner_h, cfg, char_advance_px=adv)


def test_grid_summary_rows_do_not_fuse_target_floor():
    """Regression: the three printed summary lines must render as three rows.

    This is the v5 Target 1.5-floor collapse. The fix is overlap-aware row
    grouping; the scorecard asserts zero fused rows and an aligned amount column.
    """
    from receipt_agent.agents.label_evaluator.rendering import receipt_grid as rg
    from receipt_agent.agents.label_evaluator.rendering.layout_score import (
        score_grid_layout,
    )

    spec = _target_summary_spec()
    words = _target_summary_grid_words(spec)
    rows = rg.group_words_into_grid_lines(words, spec.cell_h)
    report = score_grid_layout(rows, spec)

    # Three printed lines -> three rendered rows, none fusing source lines.
    assert report["row_merge_count"] == 0, (rows, report)
    # The three $8.07 amounts share one right-edge column.
    assert report["amount_col_spread"] <= 1, report


def test_amount_lane_aligns_jittered_prices():
    """Prices whose source right edges jitter a few px snap to one column."""
    from receipt_agent.agents.label_evaluator.rendering import receipt_grid as rg

    spec = rg.GridSpec(cell_w=7.0, cell_h=15.0, font_px=14, grid_left=10.0)
    ink = (0, 0, 0)
    # Two rows, amounts whose right edges differ by ~2 cells of source jitter.
    r1 = [rg.GridWord(left=820, top=100, right=900, bottom=118, text="$4.39", ink=ink)]
    r2 = [rg.GridWord(left=805, top=130, right=886, bottom=148, text="$12.99", ink=ink)]
    lane = rg.amount_lane_end([r1, r2], spec)
    ends = []
    for row in (r1, r2):
        for p in rg.plan_grid_line(row, spec, amount_lane=lane):
            if p.is_price:
                ends.append(p.end_col)
    # Both amounts end on the same column after lane snapping.
    assert max(ends) - min(ends) == 0, ends


def test_body_gaps_collapse_but_preserve_real_columns():
    """Stage 4: source-whitespace gaps, not absolute snapped columns.

    The source pitch (~8px/char here) is wider than the rendered cell (6px), so
    the absolute column inflates every inter-word gap. Body tokens with normal
    one-space source spacing must render tight, while a token with a genuinely
    large source gap (a tax-flag middle column) keeps its position.
    """
    from receipt_agent.agents.label_evaluator.rendering import receipt_grid as rg

    spec = rg.GridSpec(cell_w=6.0, cell_h=14.0, font_px=13, grid_left=0.0)
    ink = (0, 0, 0)
    # "AAA BBB CCC" -- 3-char words, one ~8px space between (normal spacing).
    aaa = rg.GridWord(left=0, top=0, right=24, bottom=14, text="AAA", ink=ink)
    bbb = rg.GridWord(left=32, top=0, right=56, bottom=14, text="BBB", ink=ink)
    ccc = rg.GridWord(left=64, top=0, right=88, bottom=14, text="CCC", ink=ink)
    plan = rg.plan_grid_line([aaa, bbb, ccc], spec)
    starts = {p.word.text: p.start_col for p in plan}
    # Tight single-cell gaps (source-based), NOT the inflated absolute columns
    # (round(32/6)=5, round(64/6)=11 -> 2- and 4-cell gaps).
    assert starts["BBB"] == starts["AAA"] + 3 + 1
    assert starts["CCC"] == starts["BBB"] + 3 + 1

    # A genuine far-right middle column (big source gap) stays a far column:
    # never pulled tight to a 1-cell gap, never pushed past its own column.
    name = rg.GridWord(left=0, top=0, right=40, bottom=14, text="Fruit", ink=ink)
    flag = rg.GridWord(left=120, top=0, right=132, bottom=14, text="NF", ink=ink)
    plan2 = rg.plan_grid_line([name, flag], spec)
    name_end = next(p.end_col for p in plan2 if p.word.text == "Fruit")
    flag_start = next(p.start_col for p in plan2 if p.word.text == "NF")
    assert flag_start - name_end >= 8           # still a distinct far column
    assert flag_start <= round(120 / 6)         # never pushed past its column


def test_assign_row_baselines_decramps_only_tight_rows():
    """Cramped rows get pushed to min_pitch; well-spaced rows are untouched."""
    from receipt_agent.agents.label_evaluator.rendering import receipt_grid as rg

    ink = (0, 0, 0)
    def row(top):
        return [rg.GridWord(left=10, top=top, right=80, bottom=top + 18,
                            text="X", ink=ink)]
    # rows at baselines that would be 6, 6, 40 apart (first two cramped).
    rows = [row(0), row(6), row(46)]
    ascent = 14
    bases = rg.assign_row_baselines(rows, ascent, min_pitch=20.0)
    # First two were 6 apart -> pushed to >= 20; the third (already 40 below the
    # 2nd's natural base) keeps its own baseline (no propagated drift).
    assert bases[1] - bases[0] >= 20.0
    assert bases[2] == 46 + ascent  # untouched
