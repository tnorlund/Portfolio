#!/usr/bin/env python3
"""section_cluster.py -- discover a merchant's typographic zones from glyph metrics.

A receipt's sections are a TYPOGRAPHIC fact (Epson Font A vs Font B, bold vs
regular), so let the typography define them instead of sparse labels. For each
printed LINE we measure median glyph height (size) and ink density (weight) with
the glyph segmenter, then cluster lines in (size, weight) space. The clusters are
the receipt's natural zones; the renderer can then size/weight each zone from the
learned profile.

Usage: section_cluster.py <merchant_dir> [k]
Output: <merchant_dir>/sectioncluster.png (real receipt colored by zone) + profile.
"""
from __future__ import annotations

import json
import os
import sys
from collections import defaultdict

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from glyph_segment import glyph_boxes, sauvola_mask  # noqa: E402

CLUSTER_COLORS = [
    (220, 40, 40), (40, 120, 220), (30, 170, 80),
    (210, 140, 20), (150, 60, 200), (0, 160, 170),
]


def _load(merchant_dir):
    bundle = json.load(open(os.path.join(merchant_dir, "bundle.json")))
    ex = bundle["synthetic_training_examples"][0]
    iid, _, rid = (ex.get("metadata") or {}).get("base_receipt_key", "").partition("#")
    rid = str(int(rid)) if rid.isdigit() else rid
    d = json.load(open(os.path.join(merchant_dir, "receipt_dir", f"{iid}.json")))
    words = [w for w in d.get("receipt_words", [])
             if str(w.get("receipt_id")) == rid and str(w.get("text") or "").strip()]
    im = Image.open(os.path.join(merchant_dir, "original.jpg")).convert("L")
    return im, words


def _visual_lines(words, H):
    """Group words into VISUAL rows by y-position (not OCR line_id).

    OCR line_ids split/merge visual rows; the typography measurement needs the
    rows as a reader sees them. Sort by vertical center, open a new row when the
    gap to the previous word exceeds ~0.6x the typical word height (overlap-aware).
    """
    items = []
    for w in words:
        tl, br = w["top_left"], w["bottom_right"]
        yc = (1 - tl["y"]) * H * 0.5 + (1 - br["y"]) * H * 0.5
        hgt = abs((tl["y"] - br["y"]) * H)
        items.append((yc, hgt, w))
    if not items:
        return []
    items.sort(key=lambda it: it[0])
    typ_h = float(np.median([it[1] for it in items])) or 1.0
    band = 0.6 * typ_h
    rows = []
    cur = [items[0]]
    anchor = items[0][0]
    for yc, hgt, w in items[1:]:
        if abs(yc - anchor) <= band:
            cur.append((yc, hgt, w))
            anchor = float(np.median([c[0] for c in cur]))
        else:
            rows.append([c[2] for c in cur])
            cur = [(yc, hgt, w)]
            anchor = yc
    rows.append([c[2] for c in cur])
    return rows


def _line_features(im, words):
    """Per VISUAL line: (idx, y_norm, height_px, ink_density)."""
    W, H = im.size
    arr = np.asarray(im)
    feats = []
    for lid, ws in enumerate(_visual_lines(words, H)):
        heights, densities, ys = [], [], []
        for w in ws:
            chars = [c for c in str(w["text"]) if c != " "]
            if not chars:
                continue
            tl, br = w["top_left"], w["bottom_right"]
            x0, x1 = tl["x"] * W, br["x"] * W
            y0, y1 = (1 - tl["y"]) * H, (1 - br["y"]) * H
            l, t, r, b = int(min(x0, x1)), int(min(y0, y1)), int(max(x0, x1)), int(max(y0, y1))
            if r - l < len(chars) * 3 or b - t < 8:
                continue
            crop = arr[t:b, l:r]
            mask = sauvola_mask(crop)
            for gx0, gy0, gx1, gy1 in glyph_boxes(crop, len(chars)):
                if gy1 - gy0 >= 4 and gx1 - gx0 >= 2:
                    heights.append(gy1 - gy0)
                    sub = mask[gy0:gy1, gx0:gx1]
                    densities.append(sub.mean())
            ys.append((t + b) / 2.0)
        if heights:
            feats.append({
                "line_id": lid,
                "words": ws,
                "y": float(np.median(ys)) / H,
                "height": float(np.median(heights)),
                "ink": float(np.median(densities)),
            })
    return feats


def _kmeans(X, k, iters=40, seed=0):
    """Tiny numpy k-means (no sklearn)."""
    n = len(X)
    k = min(k, n)
    # deterministic spread-out init by sorting on the first feature
    order = np.argsort(X[:, 0])
    cen = X[order[np.linspace(0, n - 1, k).astype(int)]].copy()
    lab = np.zeros(n, dtype=int)
    for _ in range(iters):
        d = ((X[:, None, :] - cen[None, :, :]) ** 2).sum(2)
        new = d.argmin(1)
        if np.array_equal(new, lab):
            break
        lab = new
        for j in range(k):
            if (lab == j).any():
                cen[j] = X[lab == j].mean(0)
    return lab, cen


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    merchant_dir = sys.argv[1]
    k = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    im, words = _load(merchant_dir)
    feats = _line_features(im, words)
    if len(feats) < k:
        print("too few lines")
        return 1

    h = np.array([f["height"] for f in feats])
    ink = np.array([f["ink"] for f in feats])
    body_h = np.median(h)
    body_ink = np.median(ink)
    # cluster on size-ratio and weight-ratio relative to the body
    X = np.column_stack([h / body_h, ink / body_ink])
    lab, cen = _kmeans(X, k)

    # order clusters by size (largest first) for readable naming
    order = np.argsort(-cen[:, 0])
    remap = {old: new for new, old in enumerate(order)}
    lab = np.array([remap[l] for l in lab])
    cen = cen[order]

    print(f"=== {os.path.basename(merchant_dir)}: {len(feats)} lines -> {k} typographic zones ===")
    print(f"(body glyph height ~{body_h:.0f}px)")
    for j in range(len(cen)):
        members = [feats[i] for i in range(len(feats)) if lab[i] == j]
        sizes = f"size x{cen[j, 0]:.2f}  weight x{cen[j, 1]:.2f}"
        yspan = f"y {min(m['y'] for m in members):.2f}-{max(m['y'] for m in members):.2f}"
        print(f"  zone {j} [{CLUSTER_COLORS[j]}]: {sizes}  {len(members)} lines  {yspan}")

    # visualize: color each line's words by zone
    W, H = im.size
    vis = im.convert("RGB")
    dr = ImageDraw.Draw(vis)
    for i, f in enumerate(feats):
        z = lab[i]
        for w in f["words"]:
            tl, br = w["top_left"], w["bottom_right"]
            x0, x1 = tl["x"] * W, br["x"] * W
            y0, y1 = (1 - tl["y"]) * H, (1 - br["y"]) * H
            dr.rectangle([min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)],
                         outline=CLUSTER_COLORS[z], width=3)
    out = os.path.join(merchant_dir, "sectioncluster.png")
    vis.save(out)
    print("colored overlay ->", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
