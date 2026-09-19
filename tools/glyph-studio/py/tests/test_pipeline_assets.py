"""Offline tests for the SynthesisPipeline asset exporter glue.

Fixtures are the committed inputs and outputs themselves: the Sprouts font
sources under ``tools/glyph-studio/fonts/sprouts`` and the shipped tree
under ``portfolio/public/synthetic-receipts/pipeline/sprouts``. Where the
exporter is deterministic from committed inputs (font grid, dot params,
skeleton, logo mask) the tests pin byte-equality with what is on prod.
"""

from __future__ import annotations

import json
import os

import numpy as np
import pytest
from glyphstudio import pipeline_assets as pa
from glyphstudio.compile import compile_font
from glyphstudio.schema import load_font
from PIL import Image

_HERE = os.path.dirname(os.path.abspath(__file__))
_STUDIO = os.path.abspath(os.path.join(_HERE, "..", ".."))
_ROOT = os.path.abspath(os.path.join(_STUDIO, "..", ".."))
SPROUTS_FONT = os.path.join(_STUDIO, "fonts", "sprouts")
PIPELINE = os.path.join(
    _ROOT, "portfolio", "public", "synthetic-receipts", "pipeline"
)
SPROUTS_ASSETS = os.path.join(PIPELINE, "sprouts")
MANIFEST = os.path.join(_STUDIO, "fixtures", "pipeline_merchants.json")

needs_assets = pytest.mark.skipif(
    not os.path.isdir(SPROUTS_ASSETS), reason="portfolio assets not present"
)


def _stack(n: int = 12, ref_cap: int = 60) -> np.ndarray:
    """A synthetic corpus: ``n`` jittered prints of a cap-height bar."""
    h, w = ref_cap * 3, ref_cap * 2
    _, baseline = pa.canvas_geometry(h)
    stack = np.zeros((n, h, w), dtype=bool)
    for i in range(n):
        dx = (i % 3) - 1
        stack[i, baseline - ref_cap : baseline, 50 + dx : 70 + dx] = True
    stack[-1] = False
    stack[-1, baseline - 2 : baseline, 60:62] = True  # 4 px speck
    return stack


class TestCharPrints:
    def test_tight_crop_and_upsample_in_corpus_order(self):
        prints = pa.char_prints(_stack(), count=5)
        assert len(prints) == 5
        for mask in prints:
            assert mask.shape == (60 * 3, 20 * 3)
            assert mask.all()

    def test_specks_are_skipped(self):
        stack = _stack(n=3)
        prints = pa.char_prints(stack, count=10)
        assert len(prints) == 2

    def test_gray_png_is_black_ink_on_white(self):
        image = pa.mask_to_gray(pa.char_prints(_stack(), count=1)[0])
        assert image.mode == "L"
        assert set(np.unique(np.asarray(image)).tolist()) == {0}


class TestCharCloud:
    def test_geometry_tracks_baseline_and_cap(self):
        image, geom = pa.char_cloud(_stack())
        assert image.mode == "L"
        assert (geom["imageW"], geom["imageH"]) == image.size
        assert geom["capHeightPx"] == 180
        # The bar sits on the baseline; the crop pads 3 corpus rows below.
        assert geom["baselineFromBottomPx"] == 3 * pa.PRINT_UPSAMPLE
        # Solid ink spans columns 49..70 (the bar plus its 1 px jitter), so
        # its centre is column 60; the crop starts 3 px left of column 49.
        expected_center = (60 - (49 - pa.CLOUD_PAD_PX)) * pa.PRINT_UPSAMPLE
        assert geom["inkCenterXPx"] == pytest.approx(expected_center)

    def test_full_consensus_is_black_paper_is_white(self):
        image, _ = pa.char_cloud(_stack())
        arr = np.asarray(image)
        assert arr.max() == 255
        assert arr.min() < 40

    def test_empty_stack_raises(self):
        with pytest.raises(ValueError):
            pa.char_cloud(np.zeros((3, 180, 120), dtype=bool))


class TestDotParams:
    def test_matches_shipped_sprouts_values(self):
        font = load_font(SPROUTS_FONT)
        got = pa.dot_params(font, hero="S", samples=140, cloud_geom=None)
        assert got == {
            "dotSize": 100.0,
            "refCap": 60,
            "weightDefault": 1.0,
            "weightBold": 1.33,
            "hero": "S",
            "samples": 140,
        }

    def test_bold_is_the_heavy_ratio_of_the_font_weight(self):
        font = {"refCap": 60, "params": {"weight": 1.2, "dot": {"size": 91.5}}}
        got = pa.dot_params(
            font, hero="T", samples=84, cloud_geom={"imageW": 1}
        )
        assert got["weightBold"] == pytest.approx(1.596)
        assert got["dotSize"] == 91.5
        assert got["cloudGeom"] == {"imageW": 1}


@pytest.fixture(scope="module")
def sprouts_bitmap_font(tmp_path_factory):
    from receipt_agent.agents.label_evaluator.rendering.bitmap_font import (
        BitmapFont,
    )

    npz = str(tmp_path_factory.mktemp("font") / "sprouts.glyphs.npz")
    compile_font(SPROUTS_FONT, npz)
    return BitmapFont(npz)


class TestFontGrid:
    def test_offset_subtracts_rows_trimmed_below_the_ink(self):
        def glyph_fn(ch, cap_px):
            arr = np.zeros((cap_px, 10), dtype=np.uint8)
            arr[5:30, 2:8] = 255  # ink, then 10 empty rows at the bottom
            return Image.fromarray(arr, "L"), cap_px, 4

        masks, metrics = pa.font_grid(glyph_fn, cap_px=40, codepoints=[65])
        assert masks[65].shape == (25, 6)
        assert metrics == {
            "capHeight": 40,
            "glyphs": {"65": {"width": 6, "height": 25, "offset": 4 - 10}},
        }

    def test_missing_glyphs_are_skipped(self):
        masks, metrics = pa.font_grid(lambda ch, cap: None, codepoints=[65])
        assert masks == {} and metrics["glyphs"] == {}

    @needs_assets
    def test_reproduces_shipped_sprouts_masks(self, sprouts_bitmap_font):
        masks, metrics = pa.font_grid(sprouts_bitmap_font.glyph)
        assert len(masks) == 94
        with open(os.path.join(SPROUTS_ASSETS, "font_metrics.json")) as fh:
            shipped = json.load(fh)
        assert metrics["capHeight"] == shipped["capHeight"]
        identical = 0
        for cp, mask in masks.items():
            path = os.path.join(SPROUTS_ASSETS, "font_grid", f"{cp}.png")
            alpha = np.asarray(Image.open(path))[..., 3] > 127
            if alpha.shape == mask.shape and np.array_equal(alpha, mask):
                identical += 1
        # One glyph was hand-edited after the figure shipped; the rest are
        # byte-identical to prod.
        assert identical >= 93
        # The shipped metrics floored the scaled baseline offset; the
        # renderer's BitmapFont rounds it, so a few glyphs sit 1 px apart.
        size_mismatches = 0
        for cp, metric in metrics["glyphs"].items():
            was = shipped["glyphs"][cp]
            if (metric["width"], metric["height"]) != (
                was["width"],
                was["height"],
            ):
                size_mismatches += 1
            assert abs(metric["offset"] - was["offset"]) <= 1, cp
        assert size_mismatches <= 1

    def test_rgba_mask_has_zero_rgb_and_binary_alpha(self):
        image = pa.mask_to_rgba(np.array([[1, 0], [0, 1]], dtype=bool))
        arr = np.asarray(image)
        assert image.mode == "RGBA"
        assert not arr[..., :3].any()
        assert arr[..., 3].tolist() == [[255, 0], [0, 255]]


class TestLabelFile:
    SINK = [
        {"word_index": 0, "text": "SPROUTS", "px": (100.0, 20.0, 300.0, 50.0)},
        {"word_index": 1, "text": "TOTAL", "px": (100.0, 900.0, 200.0, 930.0)},
        {"word_index": 2, "text": "10.78", "px": (600.0, 900.0, 700.0, 930.0)},
        {"word_index": None, "text": "clone", "px": (10.0, 60.0, 50.0, 80.0)},
        {"word_index": 3, "text": "zero", "px": (10.0, 60.0, 10.0, 80.0)},
    ]
    WORDS = [
        {"text": "SPROUTS", "labels": ["MERCHANT_NAME"]},
        {"text": "TOTAL", "labels": ["GRAND_TOTAL"]},
        {"text": "10.78", "labels": ["B-GRAND_TOTAL"]},
        {"text": "zero", "labels": []},
    ]

    def test_schema_and_inner_box_mapping(self):
        out = pa.label_file(
            self.SINK,
            self.WORDS,
            width=760,
            height=1000,
            merchant="Sprouts Farmers Market",
            receipt_key="img#1",
        )
        assert out["tokens"] == ["SPROUTS", "TOTAL", "10.78", "clone"]
        assert out["ner_tags"] == [
            "B-MERCHANT_NAME",
            "B-GRAND_TOTAL",
            "B-GRAND_TOTAL",
            "O",
        ]
        assert len(out["bboxes"]) == 4
        x0, y0, x1, y1 = out["bboxes"][0]
        # px (100, 20)-(300, 50) inside the 740x980 inner box, y flipped.
        assert x0 == pytest.approx((90 / 740) * 1000)
        assert x1 == pytest.approx((290 / 740) * 1000)
        assert y1 == pytest.approx((1 - 10 / 980) * 1000)
        assert y0 == pytest.approx((1 - 40 / 980) * 1000)
        assert out["metadata"] == {
            "operation": "re_render_real_receipt",
            "boxes": "render_true",
            "render": {"width": 760, "height": 1000, "margin": 10},
            "quality": {"grand_total": "10.78"},
        }
        assert out["merchant_name"] == "Sprouts Farmers Market"
        assert out["receipt_key"] == "img#1"

    def test_boxes_are_clamped_to_0_1000(self):
        sink = [
            {"word_index": 0, "text": "x", "px": (-50.0, -5.0, 900.0, 40.0)}
        ]
        out = pa.label_file(
            sink,
            self.WORDS,
            width=760,
            height=100,
            merchant="m",
            receipt_key="k",
        )
        box = out["bboxes"][0]
        assert box[0] == 0.0 and box[2] == 1000.0 and box[3] == 1000.0


class TestComposeSteps:
    def test_bands_split_by_box_centre(self):
        labels = {
            "tokens": ["a", "b", "c", "d", "e"],
            "bboxes": [
                [0, 950, 10, 970],  # top 3% -> header
                [0, 790, 10, 810],  # exactly on the 0.20 cut -> items
                [0, 600, 10, 620],  # 39% -> items
                [0, 300, 10, 320],  # 69% -> summary
                [0, 10, 10, 30],  # 98% -> footer
            ],
        }
        out = pa.compose_steps(labels)
        assert out == {
            "groups": {
                "header": [0],
                "items": [1, 2],
                "summary": [3],
                "footer": [4],
            },
            "tokens_total": 5,
        }

    @needs_assets
    def test_every_shipped_token_lands_in_exactly_one_group(self):
        with open(os.path.join(SPROUTS_ASSETS, "final.labels.json")) as fh:
            labels = json.load(fh)
        out = pa.compose_steps(labels)
        seen = sorted(i for g in out["groups"].values() for i in g)
        assert seen == list(range(len(labels["tokens"])))
        assert set(out["groups"]) == set(pa.COMPOSE_GROUP_ORDER)


class TestLogoAndScan:
    def test_logo_mask_is_trimmed_alpha_from_ink(self):
        gray = np.full((20, 40), 255, dtype=np.uint8)
        gray[5:15, 10:30] = 0
        gray[8, 20] = 128
        mask = pa.logo_mask(Image.fromarray(gray, "L"))
        arr = np.asarray(mask)
        assert mask.mode == "RGBA" and mask.size == (20, 10)
        assert not arr[..., :3].any()
        assert arr[0, 0, 3] == 255 and arr[3, 10, 3] == 127

    @needs_assets
    def test_logo_mask_reproduces_shipped_sprouts_logo(self):
        shipped = Image.open(os.path.join(SPROUTS_ASSETS, "logo.png"))
        alpha = np.asarray(shipped)[..., 3]
        gray = Image.fromarray((255 - alpha).astype(np.uint8), "L")
        assert np.array_equal(
            np.asarray(pa.logo_mask(gray)), np.asarray(shipped)
        )

    def test_receipt_height_keeps_true_aspect(self):
        assert pa.receipt_height(839, 3311, 760) == 2999
        assert pa.receipt_height(794, 2609, 760) == 2497

    def test_normalize_and_thumbnail_sizes(self):
        scan = Image.new("RGB", (800, 2400), "white")
        assert pa.normalize_real(scan).size == (760, 2280)
        assert pa.normalize_real(scan, height=2281).size == (760, 2281)
        assert pa.thumbnail(scan).size == (300, 900)


class TestStyleAnnotated:
    def test_display_strings_come_from_measured_values(self):
        assert pa.style_display(
            {"underlineRate": 0.415, "sizeScale": 1.05}
        ) == ("Underlined ~42% of the time")
        assert pa.style_display({"weight": "bold", "sizeScale": 1.1}) == (
            "Bold + 10% taller"
        )
        assert pa.style_display({"sizeScale": 0.95}) == "Slightly condensed"
        assert pa.style_display({"sizeScale": 0.85}) == "15% smaller"
        assert pa.style_display({"reverseVideo": "amount_field"}) == (
            "White-on-black (reverse video)"
        )
        assert pa.style_display({"sizeScale": 1.0, "weight": "normal"}) == (
            "Body text"
        )

    def test_body_sections_are_dropped_and_measurements_carried(self):
        stylemap = {
            "sections": {
                "item": {"sizeScale": 1.0, "weight": "normal"},
                "balance_due": {
                    "sizeScale": 1.1,
                    "weight": "bold",
                    "match": "^BALANCE DUE",
                    "notes": "n=12",
                },
            }
        }
        out = pa.style_annotated(
            stylemap,
            merchant="Sprouts Farmers Market",
            crops={"balance_due": "style_crops/balance_due.png"},
        )
        assert out["merchant"] == "Sprouts Farmers Market"
        assert out["sections"] == [
            {
                "name": "balance_due",
                "display": "Bold + 10% taller",
                "crop": "style_crops/balance_due.png",
                "sizeScale": 1.1,
                "weight": "bold",
                "match": "^BALANCE DUE",
                "notes": "n=12",
            }
        ]

    def test_crop_boxes_take_the_first_run_of_matching_lines(self):
        def word(text, line_id, x, y):
            return {
                "text": text,
                "line_id": line_id,
                # payload boxes: [tl.x, tl.y, br.x, br.y], y-up
                "bbox": [x, y + 10, x + 40, y],
            }

        words = [
            word("DAIRY", 1, 100, 900),
            word("MILK", 2, 100, 880),
            word("3.99", 2, 600, 880),
            word("BALANCE", 3, 100, 500),
            word("DUE", 3, 200, 500),
            word("10.78", 3, 600, 500),
        ]
        classify = lambda text: (  # noqa: E731
            "section_header" if text == "DAIRY" else "item"
        )
        boxes = pa.style_crop_boxes(
            words,
            classify,
            ["section_header", "balance_due", "missing"],
            matches={"balance_due": "^BALANCE DUE"},
        )
        assert boxes["section_header"] == [100, 900, 140, 910]
        assert boxes["balance_due"] == [100, 500, 640, 510]
        assert "missing" not in boxes

    def test_crop_receipt_flips_y_and_pads(self):
        scan = Image.new("RGB", (1000, 2000), "white")
        crop = pa.crop_receipt(scan, [100, 500, 600, 520], pad_frac=0.0)
        assert crop.size[0] == pytest.approx(530, abs=2)
        assert crop.size[1] == pytest.approx(40, abs=2)


class TestManifest:
    def test_manifest_covers_every_figure_merchant(self):
        with open(MANIFEST, encoding="utf-8") as fh:
            merchants = json.load(fh)["merchants"]
        assert set(merchants) == {
            "sprouts",
            "costco",
            "vons",
            "traderjoes",
            "cvs",
            "target",
            "innout",
            "wildfork",
            "speedway",
        }
        for slug, spec in merchants.items():
            assert os.path.isdir(
                os.path.join(_STUDIO, "fonts", spec["font"])
            ), slug
            assert len(spec["hero"]) == 1
            assert set(spec["receipt"]) == {"image_id", "receipt_id"}
