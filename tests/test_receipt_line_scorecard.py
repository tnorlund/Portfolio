from __future__ import annotations

from PIL import Image, ImageDraw

from synthesis_loop.receipt_line_scorecard import (
    _ocr_box_to_pixels,
    score_receipt_images,
)


def _word(text, line_id, word_id, bbox):
    return {
        "text": text,
        "line_id": line_id,
        "word_id": word_id,
        "bbox": bbox,
    }


def _draw_box(image, bbox, *, margin=0, inset=0, shift_x=0, extra_h=0):
    box = _ocr_box_to_pixels(bbox, image.width, image.height, margin=margin)
    assert box is not None
    left, top, right, bottom = box
    left += inset + shift_x
    right -= inset - shift_x
    top += inset - extra_h / 2
    bottom -= inset - extra_h / 2
    ImageDraw.Draw(image).rectangle(
        [int(left), int(top), int(right), int(bottom)], fill=(0, 0, 0)
    )


def test_scorecard_detects_colon_gap_height_and_density():
    words = [
        _word("CODE:", 1, 1, [50, 880, 150, 800]),
        _word("123", 1, 2, [154, 880, 230, 800]),
    ]
    real = Image.new("RGB", (300, 160), (255, 255, 255))
    synth = Image.new("RGB", (300, 160), (255, 255, 255))

    _draw_box(real, words[0]["bbox"], inset=5)
    _draw_box(real, words[1]["bbox"], inset=5)
    _draw_box(synth, words[0]["bbox"], inset=4, extra_h=8)
    _draw_box(synth, words[1]["bbox"], inset=4, shift_x=12, extra_h=8)

    report = score_receipt_images(real, synth, words, synth_margin=0, row_pitch_px=28)

    assert report["summary"]["height_ratio_median"] > 1.05
    assert report["summary"]["density_ratio_median"] > 1.0
    assert report["failures"]
    assert any(
        "colon_gap" in reason
        for failure in report["failures"]
        for reason in failure["reasons"]
    )


def test_scorecard_flags_oversized_barcode_caption():
    words = [
        _word("99022003402972471754", 10, 1, [120, 650, 880, 550]),
    ]
    real = Image.new("RGB", (300, 160), (255, 255, 255))
    synth = Image.new("RGB", (300, 160), (255, 255, 255))

    _draw_box(real, words[0]["bbox"], inset=6)
    _draw_box(synth, words[0]["bbox"], inset=2, extra_h=10)

    report = score_receipt_images(real, synth, words, synth_margin=0, row_pitch_px=28)

    assert report["summary"]["barcode_caption_height_ratio_median"] > 1.35
    assert report["failures"][0]["severity"] == "BLOCKER"
    assert any(
        "barcode_caption_height_ratio" in reason
        for reason in report["failures"][0]["reasons"]
    )
