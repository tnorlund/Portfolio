#!/usr/bin/env python3
"""Build the Dollar Tree header lockup PNG (black-on-white, thermal ink model).

The Dollar Tree thermal header prints a circular tree emblem followed by the
"DOLLAR TREE" wordmark in a heavy bold-italic sans (brand green -> solid black
on thermal). ``_merchant_logo`` in render_synthetic_receipts loads the PNG as
grayscale where dark == ink, so we emit black glyphs + a black tree emblem on a
white ground.

The 5 real Dollar Tree receipts in dev are heavily curled photos, so the
data-driven logo_master vote is too noisy (1/5 captures align) and drops the
tree emblem entirely. This fabricates a clean lockup that matches the real
wordmark style (bold italic + circular tree emblem) for a legible v0 header.

    build_dollartree_logo.py <out_png>
"""

from __future__ import annotations

import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

_ARIAL_BOLD_ITALIC = "/System/Library/Fonts/Supplemental/Arial Bold Italic.ttf"
_ARIAL_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"


def _bold_italic(size: int):
    for path in (_ARIAL_BOLD_ITALIC, _ARIAL_BOLD):
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _tree_emblem(h: int) -> Image.Image:
    """Circular tree emblem, black ink on white, roughly h x h."""
    S = h * 4  # supersample for smooth edges, downscaled at the end
    img = Image.new("L", (S, S), 255)
    d = ImageDraw.Draw(img)
    cx = cy = S // 2
    r = int(S * 0.46)
    # thin outer ring
    ring = max(3, int(S * 0.035))
    d.ellipse([cx - r, cy - r, cx + r, cy + r], outline=0, width=ring)
    # trunk
    tw = int(S * 0.09)
    trunk_top = int(cy + S * 0.02)
    trunk_bot = int(cy + r * 0.62)
    d.rectangle([cx - tw // 2, trunk_top, cx + tw // 2, trunk_bot], fill=0)
    # roots (two angled legs)
    d.line(
        [cx, trunk_bot, cx - int(S * 0.14), trunk_bot + int(S * 0.05)],
        fill=0,
        width=tw,
    )
    d.line(
        [cx, trunk_bot, cx + int(S * 0.14), trunk_bot + int(S * 0.05)],
        fill=0,
        width=tw,
    )
    # canopy: a cluster of overlapping filled circles (rounded oak crown)
    crown_cy = int(cy - S * 0.10)
    cr = int(S * 0.15)
    for dx, dy in [
        (0, -int(S * 0.10)),
        (-int(S * 0.15), 0),
        (int(S * 0.15), 0),
        (-int(S * 0.08), int(S * 0.08)),
        (int(S * 0.08), int(S * 0.08)),
        (0, 0),
    ]:
        d.ellipse(
            [
                cx + dx - cr,
                crown_cy + dy - cr,
                cx + dx + cr,
                crown_cy + dy + cr,
            ],
            fill=0,
        )
    # radiating branch hint: a few short strokes from trunk into canopy
    for ang in (-0.9, -0.3, 0.3, 0.9):
        x2 = int(cx + np.sin(ang) * S * 0.18)
        y2 = int(crown_cy + S * 0.02)
        d.line([cx, trunk_top, x2, y2], fill=0, width=max(2, tw // 2))
    return img.resize((h, h), Image.LANCZOS)


def build(out_png: str) -> None:
    H = 240
    fs = int(H * 0.80)
    f = _bold_italic(fs)
    tmp = ImageDraw.Draw(Image.new("L", (10, 10)))
    text = "DOLLAR TREE"
    b = tmp.textbbox((0, 0), text, font=f)
    tw, th = b[2] - b[0], b[3] - b[1]

    emblem = _tree_emblem(int(H * 0.92))
    gap = int(H * 0.10)
    reg_w = int(fs * 0.30)  # room for the ® superscript
    total_w = emblem.width + gap + tw + reg_w + int(fs * 0.1)
    canvas = Image.new("L", (total_w, H), 255)
    canvas.paste(emblem, (0, (H - emblem.height) // 2))
    d = ImageDraw.Draw(canvas)
    tx = emblem.width + gap
    ty = (H - th) // 2 - b[1]
    d.text((tx, ty), text, fill=0, font=f)
    # small ® after the wordmark
    fsm = _bold_italic(int(fs * 0.26))
    d.text(
        (tx + tw + int(fs * 0.04), ty + b[1]),
        "®",
        fill=0,
        font=fsm,
    )

    arr = np.asarray(canvas)
    ys, xs = np.where(arr < 250)
    canvas = canvas.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))
    canvas.save(out_png)
    print(f"wrote {out_png}  {canvas.size}")


if __name__ == "__main__":
    build(sys.argv[1] if len(sys.argv) > 1 else "dollartree_logo.png")
