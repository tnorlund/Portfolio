#!/usr/bin/env python3
"""bitmap_font_demo.py -- render a sample in our extracted bitMatrix-C2 glyph atlas.

Loads the clean glyph atlas (from extract_bitmatrix), renders a sample receipt
string monospaced (uniform cap-height scale, baseline = glyph bottom), next to the
HEAVY variant and PT Mono for comparison.

Usage: bitmap_font_demo.py <atlas_dir> ["SAMPLE"]
"""
from __future__ import annotations

import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

SAMPLE = "1757979 OG BAKER 8.99  SUBTOTAL 44.46  *** TOTAL"
CAP = 30          # target cap height px
PTMONO = "/System/Library/Fonts/Supplemental/PTMono.ttc"


def _load(path):
    d = np.load(path)
    return {chr(int(k[1:])): d[k] for k in d.files}


def _cap_ref(glyphs):
    hs = [glyphs[c].shape[0] for c in "ABCDEFGHKLMNPRSTUVXZ" if c in glyphs]
    return float(np.median(hs)) if hs else 30.0


def render(text, glyphs):
    cap_ref = _cap_ref(glyphs)
    scale = CAP / cap_ref
    adv = int(round(cap_ref * scale * 1.15))   # monospace advance ~1.15x cap
    pad = 12
    base_y = pad + CAP                          # baseline
    img = Image.new("L", (len(text) * adv + 2 * pad, CAP + 2 * pad + 8), 0)
    x = pad
    for ch in text:
        g = glyphs.get(ch)
        if ch != " " and g is not None:
            h = max(1, int(round(g.shape[0] * scale)))
            w = max(1, int(round(g.shape[1] * scale)))
            gi = Image.fromarray((g * 255).astype(np.uint8)).resize((w, h))
            img.paste(gi, (x + (adv - w) // 2, base_y - h), gi)
        x += adv
    return Image.eval(img, lambda v: 255 - v).convert("RGB"), adv


def render_ttf(text, adv):
    img = Image.new("L", (len(text) * adv + 24, CAP + 32), 255)
    d = ImageDraw.Draw(img)
    f = ImageFont.truetype(PTMONO, CAP + 6)
    x = 12
    for ch in text:
        if ch != " ":
            d.text((x, 12), ch, font=f, fill=0, anchor="lt")
        x += adv
    return img.convert("RGB")


def main() -> int:
    d = sys.argv[1]
    text = sys.argv[2] if len(sys.argv) > 2 else SAMPLE
    reg = _load(os.path.join(d, "bitMatrix-C2.glyphs.npz"))
    heavy = _load(os.path.join(d, "bitMatrix-C2-heavy.glyphs.npz"))
    r1, adv = render(text, reg)
    r2, _ = render(text, heavy)
    r3 = render_ttf(text, adv)
    rows = [("OUR bitMatrix-C2 (extracted) -- BODY", r1),
            ("OUR bitMatrix-C2-heavy (extracted) -- EMPHASIS", r2),
            ("PT Mono (previous stand-in)", r3)]
    pad = 22
    Wd = max(r[1].width for r in rows)
    sheet = Image.new("RGB", (Wd, sum(r[1].height + pad for r in rows)), (245, 245, 245))
    y = 0
    for label, im in rows:
        ImageDraw.Draw(sheet).text((4, y + 3), label, fill=(140, 0, 0))
        sheet.paste(im, (0, y + pad - 2))
        y += im.height + pad
    out = os.path.join(d, "bitmatrix_demo.png")
    sheet.save(out)
    print("demo ->", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
