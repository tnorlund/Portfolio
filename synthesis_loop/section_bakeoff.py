#!/usr/bin/env python3
"""section_bakeoff.py -- render ONE candidate across PER-SECTION typography variants.

Real thermal receipts print the header/address and sometimes payment/footer in a
smaller (or differently-condensed) size than the line-item body -- Epson "Font A"
vs "Font B". Our grid is font-parametric, so we can apply a per-section size scale
(and optional per-section font) and see which matches the real photo.

Fetches the merchant atlas/profile ONCE, renders each named variant via
``_render_cached_hybrid(section_scale=..., section_font=...)``, and stitches a
labeled comparison strip with the REAL photo first.

Usage: section_bakeoff.py <merchant_dir> <merchant_name> <op>
Output: <merchant_dir>/sectionbakeoff.png  (+ per-variant PNGs in <merchant_dir>/_sections/)
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
for p in (HERE, SCRIPTS):
    if p not in sys.path:
        sys.path.insert(0, p)

import render_synthetic_receipts as rsr  # noqa: E402
import thermal_variants as TV  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

TABLE = os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22")
REGION = os.environ.get("AWS_REGION", "us-east-1")
W, H = 460, 1100
ANDALE = "/System/Library/Fonts/Supplemental/Andale Mono.ttf"

# Per-section typography variants to try. Each maps to (section_scale, section_font).
# Body/Totals stay at 1.0 so the amount column still aligns; we vary HEADER (the
# dominant measured difference) and PAYMENT.
VARIANTS = {
    "uniform": ({}, {}),
    "hdr0.85": ({"HEADER": 0.85}, {}),
    "hdr0.78": ({"HEADER": 0.78}, {}),
    "hdr0.70": ({"HEADER": 0.70}, {}),
    "hdr0.78_pay0.90": ({"HEADER": 0.78, "PAYMENT": 0.90}, {}),
}
CROP_BAND = (0, 130, 460, 760)  # header + items + summary (where size diffs show)


def _op(ex):
    return (ex.get("metadata") or {}).get("operation") or ex.get("operation") or "?"


def _labeled(path: str, label: str) -> Image.Image | None:
    if not os.path.exists(path):
        return None
    im = Image.open(path).convert("RGB")
    if im.width != W:
        im = im.resize((W, int(im.height * W / im.width)))
    c = im.crop(CROP_BAND)
    lab = Image.new("RGB", (c.width, 24), (255, 255, 255))
    ImageDraw.Draw(lab).text((6, 6), label, fill=(0, 0, 0))
    out = Image.new("RGB", (c.width, c.height + 24), (255, 255, 255))
    out.paste(lab, (0, 0)); out.paste(c, (0, 24))
    return out


def main() -> int:
    if len(sys.argv) < 4:
        print(__doc__); return 2
    merchant_dir, merchant, op = sys.argv[1:4]
    bundle = json.load(open(os.path.join(merchant_dir, "bundle.json")))
    ex = next((e for e in bundle.get("synthetic_training_examples", []) if _op(e) == op), None)
    if ex is None:
        print("no candidate for", op); return 1

    atlas = rsr.cached_glyph_atlas(TABLE, merchant, region=REGION, max_receipts=8)
    profile = rsr.cached_font_profile(TABLE, merchant, region=REGION, max_receipts=12)
    synth = rsr._synthetic_receipt_dict(ex, TV._id_to_label(bundle))

    out_dir = os.path.join(merchant_dir, "_sections")
    os.makedirs(out_dir, exist_ok=True)
    crops = []
    real_crop = _labeled(os.path.join(merchant_dir, "original.jpg"), "REAL")
    if real_crop is not None:
        crops.append(real_crop)

    for name, (scale, font) in VARIANTS.items():
        png = os.path.join(out_dir, f"{op}__{name}.png")
        rsr._render_cached_hybrid(
            synth, atlas, profile=profile, width=W, height=H, path=png,
            section_scale=scale or None, section_font=font or None,
        )
        c = _labeled(png, name)
        if c is not None:
            crops.append(c)
        print("rendered", name)

    gap = 8
    strip = Image.new("RGB", (sum(c.width for c in crops) + gap * (len(crops) - 1),
                              max(c.height for c in crops)), (200, 200, 200))
    x = 0
    for c in crops:
        strip.paste(c, (x, 0)); x += c.width + gap
    out = os.path.join(merchant_dir, "sectionbakeoff.png")
    strip.save(out)
    print("STRIP ->", out, strip.size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
