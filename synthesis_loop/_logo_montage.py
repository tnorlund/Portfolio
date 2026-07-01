#!/usr/bin/env python3
"""Assemble a review montage of all merchants' logo masters (compare sheets)."""
import glob
import os

from PIL import Image, ImageDraw

MERCH = [
    ("costco_wholesale", "Costco"),
    ("vons", "Vons"),
    ("smiths", "Smith's"),
    ("sprouts_farmers_market", "Sprouts"),
    ("amazon_fresh", "Amazon Fresh"),
    ("target", "Target"),
    ("gelsons_westlake_village", "Gelson's"),
]
W = 700
rows = []
for d, name in MERCH:
    # costco master lives in costco_logo_review; others under <m>/logo
    cand = [f"/tmp/gridfix/{d}/logo/logo_compare.png",
            "/tmp/gridfix/costco_logo_review/logo_compare.png" if d == "costco_wholesale" else None]
    path = next((c for c in cand if c and os.path.exists(c)), None)
    label = Image.new("RGB", (W, 22), (255, 255, 255))
    ImageDraw.Draw(label).text((4, 5), f"{name}   [old mean | aligned crisp | aligned soft]", fill=(0, 0, 0))
    rows.append(label)
    if path:
        im = Image.open(path).convert("RGB")
        im = im.resize((W, int(im.height * W / im.width)))
        rows.append(im)
    else:
        miss = Image.new("RGB", (W, 40), (235, 220, 220))
        ImageDraw.Draw(miss).text((4, 12), "  (no master yet)", fill=(120, 0, 0))
        rows.append(miss)

gap = 8
H = sum(r.height for r in rows) + gap * (len(rows) + 1)
sheet = Image.new("RGB", (W, H), (245, 245, 245))
y = gap
for r in rows:
    sheet.paste(r, (0, y))
    y += r.height + gap
out = "/tmp/gridfix/LOGO_masters_review.png"
sheet.save(out)
print("wrote", out, sheet.size)
