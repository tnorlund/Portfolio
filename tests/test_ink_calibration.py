"""Unit tests for the pure ink-calibration solver."""
import os
import sys

import pytest
from PIL import Image, ImageDraw

sys.path.insert(
    0,
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "synthesis_loop"),
)

from ink_calibration import derive_bitmap_thin, measure_density_ratio


def _receipt_image(stroke: int, *, size=(400, 600), margin=0) -> Image.Image:
    """A fake receipt: rows of black bars whose thickness sets ink density."""
    img = Image.new("RGB", size, (245, 245, 240))
    draw = ImageDraw.Draw(img)
    inner_w = size[0] - 2 * margin
    inner_h = size[1] - 2 * margin
    for row in range(12):
        y = margin + int(inner_h * (0.06 + row * 0.075))
        draw.rectangle(
            [margin + int(0.08 * inner_w), y,
             margin + int(0.9 * inner_w), y + stroke],
            fill=(20, 20, 20),
        )
    return img


def _words(n=24):
    """Word boxes (0-1000, y-up) covering the bar rows."""
    words = []
    for row in range(12):
        y_top = 1000 - (60 + row * 75)  # y-up: top of row
        for col in range(2):
            words.append({
                "text": "WORD",
                "bbox": [80 + col * 420, y_top - 40, 480 + col * 420, y_top],
            })
    return words[:n]


def test_measure_density_ratio_detects_lighter_ink():
    real = _receipt_image(stroke=8)
    lighter = _receipt_image(stroke=4)
    ratio = measure_density_ratio(real, lighter, _words(), synth_margin=0)
    assert ratio is not None
    assert ratio < 0.85


def test_measure_density_ratio_requires_enough_words():
    real = _receipt_image(stroke=8)
    assert (
        measure_density_ratio(real, real, _words(4), synth_margin=0) is None
    )


def test_derive_bitmap_thin_converges_to_matching_density():
    real = _receipt_image(stroke=8)

    # Fake renderer: thin erodes the stroke linearly, 0.0 -> too fat (12px).
    def render(thin: float) -> Image.Image:
        stroke = max(1, int(round(12 - 10 * thin)))
        return _receipt_image(stroke=stroke)

    result = derive_bitmap_thin(
        render, real, _words(), synth_margin=0, tol=0.05
    )
    assert result is not None
    thin, ratio = result
    # stroke(thin) == 8 at thin = 0.4
    assert thin == pytest.approx(0.4, abs=0.12)
    assert ratio == pytest.approx(1.0, abs=0.1)


def test_derive_bitmap_thin_endpoint_when_already_light():
    real = _receipt_image(stroke=8)

    def render(thin: float) -> Image.Image:
        stroke = max(1, int(round(6 - 4 * thin)))  # lighter than real even at 0
        return _receipt_image(stroke=stroke)

    result = derive_bitmap_thin(render, real, _words(), synth_margin=0)
    assert result is not None
    assert result[0] == 0.0
