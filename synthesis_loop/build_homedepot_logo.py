#!/usr/bin/env python3
"""Build the Home Depot header lockup PNG (black-on-white, thermal ink model).

The Home Depot thermal header prints the tilted-square "THE HOME DEPOT" wordmark
(orange in brand, solid black on thermal) with the slogan "How doers get more
done." set to its right in Helvetica Neue Bold. `_merchant_logo` in
render_synthetic_receipts loads this as grayscale where dark == ink, so we emit a
black square with white knockout letters + black slogan text on a white ground.

Source: a clean brand PNG of the square wordmark (orange square, white letters,
transparent outside). Ink = orange pixels; white letters and outside = paper.

    build_homedepot_logo.py <source_png> <out_png> [slogan_line1] [slogan_line2]
"""

from __future__ import annotations

import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

_HELV = "/System/Library/Fonts/HelveticaNeue.ttc"
_ARIAL_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"


def _bold_font(size: int):
    # HelveticaNeue.ttc bold face; fall back to Arial Bold.
    for path, idx in ((_HELV, 1), (_ARIAL_BOLD, 0)):
        try:
            return ImageFont.truetype(path, size, index=idx)
        except OSError:
            continue
    return ImageFont.load_default()


def _square_ink(src_png: str) -> Image.Image:
    """Orange-square wordmark -> black-on-white grayscale, trimmed to bbox."""
    a = np.asarray(Image.open(src_png).convert("RGBA"))
    alpha, blue = a[..., 3], a[..., 2]
    ink = (alpha > 128) & (blue < 128)  # orange, not white, not transparent
    g = np.where(ink, 0, 255).astype(
        np.uint8
    )  # ink -> black, else white paper
    ys, xs = np.where(ink)
    g = g[ys.min() : ys.max() + 1, xs.min() : xs.max() + 1]
    return Image.fromarray(g, "L")


def build(src_png: str, out_png: str, line1: str, line2: str) -> None:
    sq = _square_ink(src_png)
    H = 240  # lockup / square height in px
    sq = sq.resize((round(sq.width * H / sq.height), H), Image.LANCZOS)

    # Typeset the two slogan lines to ~0.80*H total, black on white.
    fs = 78
    f = _bold_font(fs)
    tmp = ImageDraw.Draw(Image.new("L", (10, 10)))

    def line_wh(s, fnt):
        b = tmp.textbbox((0, 0), s, font=fnt)
        return b[2] - b[0], b[3] - b[1]

    tm = "™"  # trademark superscript on line 2
    tw = max(line_wh(line1, f)[0], line_wh(line2 + tm, f)[0])
    lh = int(fs * 1.02)
    gap = int(H * 0.14)
    text_w = tw + int(fs * 0.55)  # room for the ™
    total_w = sq.width + gap + text_w
    canvas = Image.new("L", (total_w, H), 255)
    canvas.paste(sq, (0, 0))
    d = ImageDraw.Draw(canvas)
    ty = (H - 2 * lh) // 2 + int(fs * 0.06)
    tx = sq.width + gap
    d.text((tx, ty), line1, fill=0, font=f)
    d.text((tx, ty + lh), line2, fill=0, font=f)
    # small superscript ™
    w2 = line_wh(line2, f)[0]
    fsm = _bold_font(int(fs * 0.34))
    d.text(
        (tx + w2 + int(fs * 0.04), ty + lh + int(fs * 0.02)),
        tm,
        fill=0,
        font=fsm,
    )

    # trim any all-white margin
    arr = np.asarray(canvas)
    ys, xs = np.where(arr < 250)
    canvas = canvas.crop((xs.min(), ys.min(), xs.max() + 1, ys.max() + 1))
    canvas.save(out_png)
    print(f"wrote {out_png}  {canvas.size}")


if __name__ == "__main__":
    src = sys.argv[1]
    out = sys.argv[2]
    l1 = sys.argv[3] if len(sys.argv) > 3 else "How doers"
    l2 = sys.argv[4] if len(sys.argv) > 4 else "get more done."
    build(src, out, l1, l2)
