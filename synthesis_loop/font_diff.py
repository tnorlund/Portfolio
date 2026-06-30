#!/usr/bin/env python3
"""font_diff.py -- measure how our render font differs from a merchant's REAL font.

Uses the real receipt photo + its OCR word boxes (which align tightly, verified):
splits each word box into per-letter cells (monospace), crops the printed glyph,
renders the SAME character with our candidate font, and compares them by:

* aspect (glyph width / height) -- are our glyphs too wide / too condensed?
* ink density (dark pixels / glyph-bbox area) -- is our font too bold / too thin?

Aggregated over hundreds of letters the systematic bias emerges (e.g. "ours is
1.18x wider and 1.30x heavier than real -> condense + thin"). Also writes a
per-character contact sheet (real | ours) for the most common glyphs.

Usage: font_diff.py <merchant_dir> [font_path]
Output: <merchant_dir>/fontdiff.png + printed metrics.
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from glyph_segment import glyph_boxes  # noqa: E402

ANDALE = "/System/Library/Fonts/Supplemental/Andale Mono.ttf"
# Characters worth a contact sheet (skip space).
SHEET_CHARS = list("ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789$.#/-")


def _load_real_words(merchant_dir):
    """Return (PIL image, [words]) for the receipt behind original.jpg."""
    bundle = json.load(open(os.path.join(merchant_dir, "bundle.json")))
    ex = bundle["synthetic_training_examples"][0]
    bk = (ex.get("metadata") or {}).get("base_receipt_key", "")
    iid, _, rid = bk.partition("#")
    rid = str(int(rid)) if rid.isdigit() else rid
    d = json.load(open(os.path.join(merchant_dir, "receipt_dir", f"{iid}.json")))
    words = [
        w for w in d.get("receipt_words", [])
        if str(w.get("receipt_id")) == rid and str(w.get("text") or "").strip()
    ]
    im = Image.open(os.path.join(merchant_dir, "original.jpg")).convert("L")
    return im, words


def _cell_boxes(word, W, H):
    """Per-character pixel cells of a word box (monospace split), y-flipped."""
    tl, br = word["top_left"], word["bottom_right"]
    x0, x1 = tl["x"] * W, br["x"] * W
    y0, y1 = (1 - tl["y"]) * H, (1 - br["y"]) * H
    top, bot = min(y0, y1), max(y0, y1)
    text = str(word["text"])
    n = len(text)
    cw = (x1 - x0) / n if n else 0
    for i, ch in enumerate(text):
        if ch == " ":
            continue
        cx0 = x0 + i * cw
        yield ch, (cx0, top, cx0 + cw, bot)


def _ink_metrics(gray: np.ndarray):
    """(aspect=w/h, density=ink/area) of the dark-ink glyph in a grayscale patch.

    Otsu threshold; ink = darker-than-threshold pixels. Returns None if ~empty.
    """
    if gray.size == 0:
        return None
    a = gray.astype(np.float64)
    # Otsu
    hist, _ = np.histogram(a, bins=256, range=(0, 255))
    tot = a.size
    sumall = (np.arange(256) * hist).sum()
    wB = sB = 0.0
    best_t, best_var = 128, -1.0
    for t in range(256):
        wB += hist[t]
        if wB == 0:
            continue
        wF = tot - wB
        if wF == 0:
            break
        sB += t * hist[t]
        mB = sB / wB
        mF = (sumall - sB) / wF
        var = wB * wF * (mB - mF) ** 2
        if var > best_var:
            best_var, best_t = var, t
    ink = a < best_t
    if ink.sum() < 3:
        return None
    ys, xs = np.where(ink)
    h = ys.max() - ys.min() + 1
    w = xs.max() - xs.min() + 1
    if h < 2:
        return None
    density = ink.sum() / float(w * h)
    return w / h, density


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    merchant_dir = sys.argv[1]
    font_path = sys.argv[2] if len(sys.argv) > 2 else ANDALE
    im, words = _load_real_words(merchant_dir)
    W, H = im.size
    arr = np.asarray(im)

    # Render our glyphs once at a reference size, measure their metrics.
    font = ImageFont.truetype(font_path, 64)
    our_metric = {}
    for ch in set("".join(SHEET_CHARS)):
        canvas = Image.new("L", (96, 96), 255)
        ImageDraw.Draw(canvas).text((48, 48), ch, font=font, fill=0, anchor="mm")
        m = _ink_metrics(np.asarray(canvas))
        if m:
            our_metric[ch] = m

    real_samples = defaultdict(list)  # ch -> [(aspect, density, crop_arr)]
    for w in words:
        chars = [c for c in str(w["text"]) if c != " "]
        if not chars:
            continue
        tl, br = w["top_left"], w["bottom_right"]
        x0, x1 = tl["x"] * W, br["x"] * W
        y0, y1 = (1 - tl["y"]) * H, (1 - br["y"]) * H
        l, t, r, b = int(min(x0, x1)), int(min(y0, y1)), int(max(x0, x1)), int(max(y0, y1))
        if r - l < len(chars) * 3 or b - t < 8:
            continue
        word_gray = arr[t:b, l:r]
        boxes = glyph_boxes(word_gray, len(chars))
        if len(boxes) != len(chars):
            continue
        for ch, (gx0, gy0, gx1, gy1) in zip(chars, boxes):
            if ch not in our_metric or gx1 - gx0 < 2 or gy1 - gy0 < 4:
                continue
            patch = word_gray[gy0:gy1, gx0:gx1]
            # patch is already tight-cropped to the glyph's ink by segmentation.
            aspect = (gx1 - gx0) / (gy1 - gy0)
            m = _ink_metrics(patch)
            density = m[1] if m else None
            if density is not None:
                real_samples[ch].append((aspect, density, patch))

    # Aggregate ratios ours/real over all sampled letters.
    asp_ratios, den_ratios, n = [], [], 0
    per_char = {}
    for ch, samples in real_samples.items():
        if not samples or ch not in our_metric:
            continue
        ra = np.median([s[0] for s in samples])
        rd = np.median([s[1] for s in samples])
        oa, od = our_metric[ch]
        if ra > 0 and rd > 0:
            per_char[ch] = (oa / ra, od / rd, len(samples))
            asp_ratios.append(oa / ra)
            den_ratios.append(od / rd)
            n += len(samples)

    print(f"=== font_diff: {os.path.basename(merchant_dir)} vs {os.path.basename(font_path)} ===")
    print(f"letters sampled: {n}  chars: {len(per_char)}")
    if asp_ratios:
        print(f"ASPECT  ours/real  median={np.median(asp_ratios):.3f}  "
              f"(>1 = our glyphs too WIDE, <1 = too condensed)")
        print(f"INK     ours/real  median={np.median(den_ratios):.3f}  "
              f"(>1 = our font too BOLD/heavy, <1 = too thin)")
        worst = sorted(per_char.items(), key=lambda kv: abs(kv[1][1] - 1), reverse=True)[:8]
        print("worst ink mismatch chars (ch: aspect_r, ink_r, n):")
        for ch, (ar, dr, cnt) in worst:
            print(f"   {ch!r}: aspect={ar:.2f} ink={dr:.2f} n={cnt}")

    # Contact sheet: real (a representative crop) vs our glyph, per char.
    cell = 56
    cols = [c for c in SHEET_CHARS if c in real_samples and c in our_metric]
    sheet = Image.new("RGB", (len(cols) * cell, cell * 2 + 20), (235, 235, 235))
    dr = ImageDraw.Draw(sheet)
    for i, ch in enumerate(cols):
        # median-density real sample
        samples = sorted(real_samples[ch], key=lambda s: s[1])
        _, _, patch = samples[len(samples) // 2]
        rg = Image.fromarray(patch).convert("L").resize((cell - 8, cell - 8))
        sheet.paste(rg, (i * cell + 4, 4))
        og = Image.new("L", (cell - 8, cell - 8), 255)
        ImageDraw.Draw(og).text(((cell - 8) // 2, (cell - 8) // 2), ch,
                                font=ImageFont.truetype(font_path, cell - 18),
                                fill=0, anchor="mm")
        sheet.paste(og, (i * cell + 4, cell + 4))
        dr.text((i * cell + 4, cell * 2 + 4), ch, fill=(0, 0, 0))
    dr.text((4, 0), "real", fill=(180, 0, 0))
    out = os.path.join(merchant_dir, "fontdiff.png")
    sheet.save(out)
    print("contact sheet (top row=REAL, bottom=OURS) ->", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
