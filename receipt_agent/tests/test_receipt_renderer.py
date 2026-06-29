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
