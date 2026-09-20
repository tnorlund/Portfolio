"""glyph_review and the gold/export path resolve through ONE function."""

from __future__ import annotations

import os
import sys
from types import SimpleNamespace

import pytest

from synthesis_loop import glyph_review

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STUDIO_PY = os.path.join(_ROOT, "tools", "glyph-studio", "py")


def _boom(*_a, **_k):
    raise AssertionError("the corpus path must not run on a closed review")


def test_review_inputs_match_the_closed_gold_inputs():
    if _STUDIO_PY not in sys.path:
        sys.path.append(_STUDIO_PY)
    import render_merchant_gold

    rsr = SimpleNamespace(
        corpus_font_inputs=_boom,
        cached_font_profile=_boom,
        resolve_bitmap_thin=_boom,
    )
    typ = {
        "bitmap_font": {"regular": "r.npz", "heavy": "h.npz"},
        "condense": 0.895,
        "pitch_ratio": 0.6,
    }
    review_prof, review_typ = glyph_review.review_inputs(
        rsr,
        "Costco Wholesale",
        typ,
        table="t",
        region="us-east-1",
        canvas_height=2999,
        canvas_width=760,
        section_scale={"HEADER": 0.8},
    )
    gold_prof, gold_typ = render_merchant_gold.closed_gold_inputs(
        "Costco Wholesale",
        typ,
        table="t",
        region="us-east-1",
        section_scale={"HEADER": 0.8},
        canvas_height=2999,
        canvas_width=760,
    )
    assert review_typ == gold_typ
    assert review_prof == gold_prof
    # the recorded cap sized the profile for THIS canvas
    assert review_prof.font_height == pytest.approx((22 / 0.72) / 2979)
    assert review_typ["bitmap_thin"] == 0.0  # vendor.json pin, not solved
    assert review_typ["ocr_cap_height_ratio"] == pytest.approx(0.72)


def test_review_inputs_calibrate_flag_uses_the_shared_corpus_helper():
    seen = {}

    def corpus_font_inputs(table, merchant, *, region, typography, **kw):
        seen.update(table=table, merchant=merchant, region=region, **kw)
        return "PROF", dict(typography, bitmap_thin=0.3)

    rsr = SimpleNamespace(
        corpus_font_inputs=corpus_font_inputs,
        cached_font_profile=_boom,
        resolve_bitmap_thin=_boom,
    )
    prof, typ = glyph_review.review_inputs(
        rsr,
        "Costco Wholesale",
        {"bitmap_font": {"regular": "r.npz"}},
        table="t",
        region="r",
        canvas_height=1000,
        atlas="ATLAS",
        section_scale={},
        calibrate_from_corpus=True,
    )
    assert prof == "PROF"
    assert typ["bitmap_thin"] == 0.3
    assert seen["atlas"] == "ATLAS"
    assert seen["merchant"] == "Costco Wholesale"


def test_closed_profile_font_height_is_the_corpus_word_height_quantity():
    """cap 22 @ 760x2497 -> font_px 31 (word height), and the renderer's own
    OCR metrics recover cap 22 from 31 px words, closing the loop with what
    cached_font_profile records (median OCR word-box height / paper)."""
    if _STUDIO_PY not in sys.path:
        sys.path.append(_STUDIO_PY)
    import render_merchant_gold
    from glyphstudio.vendor_package import gold_render_pins

    from receipt_agent.agents.label_evaluator.rendering.receipt_grid import (
        GridWord,
        build_grid_spec,
    )
    from receipt_agent.agents.label_evaluator.rendering.receipt_renderer import (
        RenderConfig,
        _ocr_grid_metrics,
    )

    prof = render_merchant_gold.closed_font_profile(
        "Sprouts Farmers Market",
        gold_render_pins("sprouts"),
        canvas_height=2497,
        canvas_width=760,
    )
    config = RenderConfig(
        width=760,
        height=2497,
        margin=10,
        ocr_font_sizing=True,
        ocr_cap_height_ratio=0.72,
    )
    spec = build_grid_spec(prof, 740, 2477, config)
    assert spec.font_px == 31  # not the 45 px the 0.018 fallback gave
    words = [
        GridWord(
            left=20.0 + 90 * i,
            top=100.0 + 40 * i,
            right=80.0 + 90 * i,
            bottom=131.0 + 40 * i,
            text=f"WORD{i:02d}",
            ink=(0, 0, 0),
        )
        for i in range(12)
    ]
    cap_px, _advance = _ocr_grid_metrics(words, spec, config)
    assert cap_px == 22
