"""Unit tests for the CDN re-warp tool (pure parts only).

No AWS, no network: aspect grading, the OCR box projection, the
text-alignment gate that guards every republish, and the CDN key
conflict check.
"""

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest
from PIL import Image as PIL_Image
from PIL import ImageDraw

from scripts import rewarp_receipt_cdn_images as rw

IMG = "0e2f8a2c-1111-4222-8333-944445555666"


def make_receipt(width: int, height: int, **overrides) -> SimpleNamespace:
    """Duck-typed receipt occupying the middle of a portrait photo."""
    fields = {
        "image_id": IMG,
        "receipt_id": 1,
        "width": width,
        "height": height,
        "top_left": {"x": 0.25, "y": 0.75},
        "top_right": {"x": 0.75, "y": 0.75},
        "bottom_right": {"x": 0.75, "y": 0.25},
        "bottom_left": {"x": 0.25, "y": 0.25},
        "cdn_s3_bucket": "site-bucket",
        "cdn_s3_key": f"assets/{IMG}/1.jpg",
        "cdn_thumbnail_s3_key": f"assets/{IMG}/1_thumbnail.jpg",
    }
    fields.update(overrides)
    return SimpleNamespace(**fields)


def make_word(x: float, y: float, width: float, height: float, text: str):
    return SimpleNamespace(
        text=text,
        bounding_box={"x": x, "y": y, "width": width, "height": height},
    )


# --------------------------------------------------------------------- #
# Aspect grading                                                        #
# --------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "cdn_size,expected",
    [
        ((866, 2688), "ok"),
        ((96, 300), "ok"),  # thumbnail, integer-rounded
        ((2688, 866), "rotated_90"),
        ((300, 96), "rotated_90"),
        ((848, 2420), "aspect_mismatch"),  # stale crop, still portrait
    ],
)
def test_variant_verdicts(monkeypatch, cdn_size, expected):
    monkeypatch.setattr(
        rw, "_probe_cdn_size", lambda *a, **k: (cdn_size, None)
    )
    probe = rw._probe_variant(
        None, "bucket", "full", "key.jpg", rw._aspect(866, 2688)
    )
    assert probe.verdict == expected


def test_scan_status_is_worst_variant(monkeypatch):
    """A receipt is only 'ok' when every published variant agrees."""
    sizes = {
        "assets/x/1.jpg": (866, 2688),
        "assets/x/1_thumbnail.jpg": (2688, 866),
    }
    monkeypatch.setattr(
        rw,
        "_probe_cdn_size",
        lambda client, bucket, key: (sizes[key], None),
    )
    receipt = make_receipt(
        866,
        2688,
        cdn_s3_key="assets/x/1.jpg",
        cdn_thumbnail_s3_key="assets/x/1_thumbnail.jpg",
    )
    result = rw.scan_receipt(receipt, None, None)
    assert result.status == "rotated_90"
    assert result.is_mismatch
    assert {v.name for v in result.bad_variants} == {"thumbnail"}


def test_quad_aspect_flags_inconsistent_entity():
    """Corners spanning a 3:4 photo cannot describe a 1:4 receipt."""
    receipt = make_receipt(200, 800)
    # Corners cover the full 600x800 image -> quad aspect 0.75, while the
    # declared dimensions claim 0.25.
    receipt.top_left = {"x": 0.0, "y": 1.0}
    receipt.top_right = {"x": 1.0, "y": 1.0}
    receipt.bottom_right = {"x": 1.0, "y": 0.0}
    receipt.bottom_left = {"x": 0.0, "y": 0.0}
    quad = rw._quad_aspect(receipt, 600, 800)
    assert quad == pytest.approx(0.75)
    assert not rw._close(quad, rw._aspect(200, 800), rw.QUAD_ASPECT_TOLERANCE)


# --------------------------------------------------------------------- #
# OCR box projection                                                    #
# --------------------------------------------------------------------- #


def test_word_boxes_flip_y_up_to_pil_rows():
    """Receipt-relative y is bottom-up; PIL rows count downward."""
    word = make_word(0.1, 0.8, 0.5, 0.1, "TOTAL")
    (box,) = rw._word_boxes_in_pixels([word], 100, 1000)
    left, top, right, bottom = box
    assert (left, right) == (10, 60)
    # y=0.8 is the bottom edge, y+h=0.9 the top -> rows 100..200.
    assert (top, bottom) == (100, 200)


# --------------------------------------------------------------------- #
# Text-alignment gate                                                   #
# --------------------------------------------------------------------- #


def _receipt_canvas(width: int, height: int, words) -> PIL_Image.Image:
    """White canvas with a dark bar drawn exactly where each word sits."""
    canvas = PIL_Image.new("RGB", (width, height), "white")
    draw = ImageDraw.Draw(canvas)
    for box in rw._word_boxes_in_pixels(words, width, height):
        draw.rectangle(box, fill=(20, 20, 20))
    return canvas


def test_alignment_gate_accepts_matching_text():
    words = [
        make_word(0.1, 0.05 + 0.09 * i, 0.6, 0.04, f"ITEM{i}")
        for i in range(10)
    ]
    canvas = _receipt_canvas(400, 1200, words)
    gap, measured = rw.text_alignment_score(canvas, words)
    assert measured > 0
    assert gap > rw.DEFAULT_DARKNESS_MARGIN


def test_alignment_gate_rejects_shifted_text():
    """The gate is what stops a wrong warp from being published."""
    words = [
        make_word(0.1, 0.05 + 0.09 * i, 0.6, 0.04, f"ITEM{i}")
        for i in range(10)
    ]
    # Render the text half a line lower than the OCR boxes claim: the
    # boxes now straddle blank paper and their own gaps.
    shifted = [
        make_word(0.1, 0.05 + 0.09 * i + 0.045, 0.6, 0.04, f"ITEM{i}")
        for i in range(10)
    ]
    canvas = _receipt_canvas(400, 1200, shifted)
    gap, _ = rw.text_alignment_score(canvas, words)
    assert gap < rw.DEFAULT_DARKNESS_MARGIN


def test_alignment_gate_rejects_blank_canvas():
    words = [
        make_word(0.1, 0.05 + 0.09 * i, 0.6, 0.04, f"ITEM{i}")
        for i in range(10)
    ]
    canvas = PIL_Image.new("RGB", (400, 1200), "white")
    gap, _ = rw.text_alignment_score(canvas, words)
    assert gap == pytest.approx(0.0, abs=0.01)


def test_alignment_score_reports_no_boxes_when_words_missing():
    canvas = PIL_Image.new("RGB", (400, 1200), "white")
    assert rw.text_alignment_score(canvas, []) == (None, 0)


def test_probe_boxes_spread_down_the_receipt():
    """Sampling must span the receipt, not cluster at one edge."""
    words = [
        make_word(0.1, 0.01 + 0.0098 * i, 0.6, 0.005, f"ITEM{i}")
        for i in range(100)
    ]
    boxes = rw._select_probe_boxes(words, 400, 4000, sample=5)
    assert len(boxes) == 5
    tops = [box[1] for box in boxes]
    assert max(tops) - min(tops) > 2000


# --------------------------------------------------------------------- #
# CDN key safety                                                        #
# --------------------------------------------------------------------- #


def test_no_conflicts_for_conventional_keys():
    receipt = make_receipt(866, 2688)
    base = rw._derive_base_key(receipt)
    assert base == f"assets/{IMG}/1"
    assert rw._expected_key_conflicts(receipt, base) == []


def test_conflicts_reported_for_legacy_key_layout():
    """Legacy ``{image_id}_RECEIPT_00003`` keys must block a republish."""
    receipt = make_receipt(
        942,
        1480,
        cdn_s3_key=f"assets/{IMG}_RECEIPT_00003.jpg",
        cdn_thumbnail_s3_key=f"assets/{IMG}_RECEIPT_00003_thumb.jpg",
    )
    base = rw._derive_base_key(receipt)
    conflicts = rw._expected_key_conflicts(receipt, base)
    assert [name for name, _, _ in conflicts] == ["cdn_thumbnail_s3_key"]


def test_close_is_symmetric():
    assert rw._close(0.32, 0.33, 0.05) == rw._close(0.33, 0.32, 0.05)
    assert not rw._close(0.32, 1.0 / 0.32, 0.05)
    assert math.isclose(rw._aspect(866, 2688), 866 / 2688)
