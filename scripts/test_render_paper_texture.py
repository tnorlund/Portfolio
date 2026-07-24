"""Regression tests for paper/ink separation in synthetic receipt texture."""

import os
import sys

from PIL import Image, ImageStat

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import render_synthetic_receipts as rsr  # noqa: E402


def test_protected_paper_does_not_create_ink_dark_speckles():
    paper = Image.new("RGB", (200, 400), (250, 249, 245))

    unprotected = rsr._composite_paper_texture(paper, seed=123, strength=1.0)
    protected = rsr._composite_paper_texture(
        paper,
        seed=123,
        strength=1.0,
        protect_paper=True,
    )

    assert ImageStat.Stat(unprotected.convert("L")).extrema[0][0] < 210
    assert ImageStat.Stat(protected.convert("L")).extrema[0][0] >= 205


def test_protected_paper_keeps_intentional_ink_dark():
    paper = Image.new("RGB", (200, 400), (250, 249, 245))
    for x in range(70, 130):
        for y in range(180, 220):
            paper.putpixel((x, y), (38, 36, 34))

    protected = rsr._composite_paper_texture(
        paper,
        seed=123,
        strength=1.0,
        protect_paper=True,
    )

    assert (
        ImageStat.Stat(protected.crop((80, 185, 120, 215)).convert("L")).mean[
            0
        ]
        < 80
    )
