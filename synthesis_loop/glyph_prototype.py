#!/usr/bin/env python3
"""glyph_prototype.py -- canonical per-character glyphs for a merchant ZONE + font match.

The font-finding upgrade: instead of 2 scalars (aspect/weight), build a canonical
prototype of each LETTER by averaging every aligned instance across the merchant's
receipts (averaging cancels thermal noise / lighting / breakage), then match that
fingerprint against a font library by full-shape similarity.

Zones matter: a receipt mixes fonts (logo, bold headings, body, footer), so we
prototype ONE zone at a time -- here the BODY (glyphs whose height is near the
receipt's median; logo/headings/footer are excluded by their size).

Usage: glyph_prototype.py <merchant_dir> [zone_lo zone_hi]
  zone_lo/hi: keep glyphs whose height/median is in [lo,hi] (default 0.82 1.22 = body)
Output: <merchant_dir>/prototypes.png (canonical glyphs + best-font row) + font ranking.
"""
from __future__ import annotations

import glob
import json
import os
import sys
from collections import defaultdict

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from glyph_segment import glyph_boxes, sauvola_mask  # noqa: E402

BOX = 48                       # normalized glyph canvas
GH = 36                        # target glyph height inside the box
CHARS = list("ABCDEFGHIJKLMNOPRSTUVWXY0123456789")
FONTS = {
    "Andale": "/System/Library/Fonts/Supplemental/Andale Mono.ttf",
    "B612": os.path.join(os.path.dirname(__file__), "..", "receipt_agent",
                         "receipt_agent", "agents", "label_evaluator",
                         "rendering", "fonts", "B612Mono-Regular.ttf"),
    "Menlo": "/System/Library/Fonts/Menlo.ttc",
    "Monaco": "/System/Library/Fonts/Monaco.ttf",
    "Courier": "/System/Library/Fonts/Supplemental/Courier New.ttf",
    "PTMono": "/System/Library/Fonts/Supplemental/PTMono.ttc",
}


def _normalize(mask: np.ndarray) -> np.ndarray | None:
    """Center+scale a binary glyph mask into a fixed BOX (height-normalized)."""
    ys, xs = np.where(mask)
    if ys.size < 4:
        return None
    g = mask[ys.min():ys.max() + 1, xs.min():xs.max() + 1].astype(np.float32)
    h, w = g.shape
    scale = GH / h
    nw = max(1, int(round(w * scale)))
    im = Image.fromarray((g * 255).astype(np.uint8)).resize((nw, GH))
    a = np.asarray(im, dtype=np.float32) / 255.0
    out = np.zeros((BOX, BOX), np.float32)
    x0 = (BOX - min(nw, BOX)) // 2
    y0 = (BOX - GH) // 2
    out[y0:y0 + GH, x0:x0 + min(nw, BOX)] = a[:, :min(nw, BOX)]
    return out


def _real_glyphs(merchant_dir, lo, hi):
    """Collect normalized real glyphs per char from the BODY zone.

    Restricted to the BASE receipt -- the only one whose photo (original.jpg) we
    have here, so the only one whose boxes align. (Scaling to ALL of a merchant's
    receipts means fetching each photo from S3; the pipeline is identical.)
    """
    samples = defaultdict(list)
    bundle = json.load(open(os.path.join(merchant_dir, "bundle.json")))
    ex = bundle["synthetic_training_examples"][0]
    iid, _, rid = (ex.get("metadata") or {}).get("base_receipt_key", "").partition("#")
    rid = str(int(rid)) if rid.isdigit() else rid
    for path in [os.path.join(merchant_dir, "receipt_dir", f"{iid}.json")]:
        d = json.load(open(path))
        img_path = os.path.join(merchant_dir, "original.jpg")
        if not os.path.exists(img_path):
            continue
        im = Image.open(img_path).convert("L")
        W, H = im.size
        arr = np.asarray(im)
        words = [w for w in d.get("receipt_words", [])
                 if str(w.get("receipt_id")) == rid and str(w.get("text") or "").strip()]
        # body height reference: median glyph height across this receipt
        all_h = []
        per_word_boxes = []
        for w in words:
            chars = [c for c in str(w["text"]) if c != " "]
            tl, br = w["top_left"], w["bottom_right"]
            x0, x1 = tl["x"] * W, br["x"] * W
            y0, y1 = (1 - tl["y"]) * H, (1 - br["y"]) * H
            l, t, r, b = int(min(x0, x1)), int(min(y0, y1)), int(max(x0, x1)), int(max(y0, y1))
            if not chars or r - l < len(chars) * 3 or b - t < 8:
                per_word_boxes.append(None)
                continue
            crop = arr[t:b, l:r]
            bxs = glyph_boxes(crop, len(chars))
            per_word_boxes.append((crop, chars, bxs))
            for gx0, gy0, gx1, gy1 in bxs:
                if gy1 - gy0 >= 6:
                    all_h.append(gy1 - gy0)
        if not all_h:
            continue
        med = float(np.median(all_h))
        for pwb in per_word_boxes:
            if pwb is None:
                continue
            crop, chars, bxs = pwb
            mask = sauvola_mask(crop)
            for ch, (gx0, gy0, gx1, gy1) in zip(chars, bxs):
                if ch not in CHARS:
                    continue
                gh = gy1 - gy0
                if gh < 6 or not (lo <= gh / med <= hi):
                    continue
                norm = _normalize(mask[gy0:gy1, gx0:gx1])
                if norm is not None:
                    samples[ch].append(norm)
    return samples


def _font_glyph(font, ch, stroke=0):
    canvas = Image.new("L", (BOX * 2, BOX * 2), 0)
    ImageDraw.Draw(canvas).text(
        (BOX, BOX), ch, font=font, fill=255, anchor="mm",
        stroke_width=stroke, stroke_fill=255,
    )
    return _normalize(np.asarray(canvas) > 128)


FONT_DIR = os.environ.get("FONT_LIB", "/tmp/fonts_lib")


def _all_font_paths():
    paths = dict(FONTS)
    if os.path.isdir(FONT_DIR):
        for fn in sorted(os.listdir(FONT_DIR)):
            if fn.lower().endswith((".ttf", ".otf", ".ttc")):
                paths.setdefault(os.path.splitext(fn)[0], os.path.join(FONT_DIR, fn))
    return paths


def font_candidates(size=GH):
    """(label, FreeTypeFont, stroke) candidates incl. emboldened (double-strike) variants."""
    out = []
    for name, path in _all_font_paths().items():
        if not os.path.exists(path):
            continue
        try:
            f = ImageFont.truetype(path, size)
        except OSError:
            continue
        out.append((name, f, 0))
        out.append((f"{name}+bold", f, 1))  # double-strike emphasis
    return out


def _sim(a, b):
    """Zero-mean normalized cross-correlation of two BOXxBOX maps."""
    x, y = a.ravel() - a.mean(), b.ravel() - b.mean()
    nx, ny = np.linalg.norm(x), np.linalg.norm(y)
    return float(x @ y / (nx * ny)) if nx and ny else 0.0


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    merchant_dir = sys.argv[1]
    lo = float(sys.argv[2]) if len(sys.argv) > 2 else 0.82
    hi = float(sys.argv[3]) if len(sys.argv) > 3 else 1.22
    samples = _real_glyphs(merchant_dir, lo, hi)
    protos = {ch: np.mean(s, axis=0) for ch, s in samples.items() if len(s) >= 3}
    if not protos:
        print("no prototypes")
        return 1
    counts = {ch: len(samples[ch]) for ch in protos}
    print(f"=== {os.path.basename(merchant_dir)} BODY zone: {sum(counts.values())} glyphs, "
          f"{len(protos)} chars with a prototype ===")

    # font ranking against the prototype fingerprint
    ranking = []
    for name, path in FONTS.items():
        if not os.path.exists(path):
            continue
        font = ImageFont.truetype(path, GH)
        sims = []
        for ch, proto in protos.items():
            fg = _font_glyph(font, ch)
            if fg is not None:
                sims.append(_sim(proto, fg))
        if sims:
            ranking.append((name, float(np.mean(sims))))
    ranking.sort(key=lambda kv: -kv[1])
    print("font match (shape similarity, higher=closer):")
    for name, s in ranking:
        print(f"   {name:8} {s:.3f}")
    best = ranking[0][0] if ranking else None

    # contact sheet: prototype row + best-font row
    cols = [c for c in CHARS if c in protos]
    sheet = Image.new("RGB", (len(cols) * BOX, BOX * 2 + 16), (235, 235, 235))
    bf = ImageFont.truetype(FONTS[best], GH) if best else None
    for i, ch in enumerate(cols):
        pim = Image.fromarray((protos[ch] * 255).astype(np.uint8)).convert("RGB")
        sheet.paste(pim, (i * BOX, 0))
        if bf is not None:
            fg = _font_glyph(bf, ch)
            fim = Image.fromarray((fg * 255).astype(np.uint8)).convert("RGB")
            sheet.paste(fim, (i * BOX, BOX))
    d = ImageDraw.Draw(sheet)
    d.text((2, BOX * 2 + 2), f"top=Costco prototype  bottom=best font ({best})", fill=(0, 0, 0))
    out = os.path.join(merchant_dir, "prototypes.png")
    sheet.save(out)
    print("prototype sheet ->", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
