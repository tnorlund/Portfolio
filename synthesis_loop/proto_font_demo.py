#!/usr/bin/env python3
"""proto_font_demo.py -- render text in our OWN font built from the real glyph prototypes.

The canonical per-character prototypes (body_protos.npz) are the merchant's actual
averaged letterforms -- a free, data-built stand-in for the paid bitMatrix-C2. This
renders a sample string by pasting each prototype glyph at a fixed monospace
advance, in two styles (soft = as-averaged thermal; crisp = thresholded), next to
PT Mono for comparison.

Usage: proto_font_demo.py <out_dir> ["SAMPLE TEXT"]
"""
from __future__ import annotations

import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

SAMPLE = "1757979 OG BAKER 8.99  SUBTOTAL 44.46  TOTAL"
H = 40          # glyph height px
ADV = 26        # monospace advance px
PTMONO = "/System/Library/Fonts/Supplemental/PTMono.ttc"


def _glyph_from_proto(proto, crisp):
    """A height-H glyph image (L, white-on-black ink) from a BOXxBOX prototype."""
    m = proto > 0.4
    ys, xs = np.where(m)
    if ys.size < 3:
        return None
    g = proto[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    if crisp:
        g = (g > 0.4).astype(np.float32)
    h, w = g.shape
    nw = max(1, int(round(w * H / h)))
    im = Image.fromarray((np.clip(g, 0, 1) * 255).astype(np.uint8)).resize((nw, H))
    return im


def _render_proto(text, protos, crisp):
    img = Image.new("L", (len(text) * ADV + 20, H + 16), 0)
    x = 10
    for ch in text:
        u = ch.upper()
        if ch != " " and u in protos:
            gl = _glyph_from_proto(protos[u], crisp)
            if gl is not None:
                img.paste(gl, (x + (ADV - gl.width) // 2, 8), gl)
        x += ADV
    # invert to dark-on-light
    return Image.eval(img, lambda v: 255 - v).convert("RGB")


def _render_ttf(text):
    img = Image.new("L", (len(text) * ADV + 20, H + 16), 255)
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype(PTMONO, H)
    x = 10
    for ch in text:
        if ch != " ":
            d.text((x, 8), ch, font=f, fill=0, anchor="lt")
        x += ADV
    return img.convert("RGB")


def main() -> int:
    out_dir = sys.argv[1]
    text = sys.argv[2] if len(sys.argv) > 2 else SAMPLE
    data = np.load(os.path.join(out_dir, "body_protos.npz"))
    protos = {k: data[k] for k in data.files}

    rows = [
        ("OUR FONT (soft / as-averaged thermal)", _render_proto(text, protos, crisp=False)),
        ("OUR FONT (crisp / thresholded)", _render_proto(text, protos, crisp=True)),
        ("PT Mono (current free stand-in)", _render_ttf(text)),
    ]
    pad = 22
    Wd = max(r[1].width for r in rows)
    sheet = Image.new("RGB", (Wd, sum(r[1].height + pad for r in rows)), (245, 245, 245))
    y = 0
    for label, im in rows:
        ImageDraw.Draw(sheet).text((4, y + 2), label, fill=(120, 0, 0))
        sheet.paste(im, (0, y + pad - 4))
        y += im.height + pad
    out = os.path.join(out_dir, "proto_font_demo.png")
    sheet.save(out)
    print("our-font demo ->", out, f"({len(protos)} glyphs available)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
