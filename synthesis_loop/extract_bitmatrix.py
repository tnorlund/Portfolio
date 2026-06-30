#!/usr/bin/env python3
"""extract_bitmatrix.py -- build a clean glyph atlas from a bitMatrix-C2 chart PNG.

The receiptfont.com chart is a 17-column ASCII grid (`!` 0x21 .. `~` 0x7E): a thin
partial top row holding only `!`, then full rows of 17. Glyphs are pure black;
cell labels are gray and grid/baseline guides are blue -- so a black-only mask
isolates the letterform. Output: <out>/<name>.glyphs.npz (char -> binary glyph) +
a verification contact sheet.

Usage: extract_bitmatrix.py <chart.png> <out_dir> <atlas_name>
"""
from __future__ import annotations

import os
import sys

import numpy as np
from PIL import Image, ImageDraw

COLS = 17


def _row_bands(mask):
    """Y-bands (row ranges) that contain glyphs, top->bottom."""
    proj = mask.sum(axis=1)
    on = proj > proj.max() * 0.03
    bands, i, n = [], 0, len(on)
    while i < n:
        if on[i]:
            j = i
            while j < n and on[j]:
                j += 1
            if j - i >= 4:
                bands.append((i, j))
            i = j
        else:
            i += 1
    return bands


def _trim_borders(g):
    """Drop isolated full-height edge columns (cell-border slivers), keep '|'/'I'."""
    h = g.shape[0]
    full = lambda c: g[:, c].sum() >= 0.95 * h
    empty = lambda c: g[:, c].sum() <= 0.1 * h
    L, R = 0, g.shape[1]
    while R - L > 3 and full(L) and empty(L + 1):
        L += 1
    while R - L > 3 and full(R - 1) and empty(R - 2):
        R -= 1
    g = g[:, L:R]
    cols = np.where(g.sum(0) > 0)[0]
    if cols.size:
        g = g[:, cols.min():cols.max() + 1]
    rows = np.where(g.sum(1) > 0)[0]
    if rows.size:
        g = g[rows.min():rows.max() + 1, :]
    return g


def _extract_cell(black, y0, y1, c, W):
    """Return (trimmed glyph, glyph_bottom_y_in_band) or None.

    Removes the full-height vertical CELL BORDER (near either cell edge) BEFORE
    measuring, so the glyph's real bottom -- not the border's -- sets the baseline.
    """
    cell_w = W / COLS
    x0, x1 = int(round(c * cell_w)), int(round((c + 1) * cell_w))
    cell = black[y0:y1, x0:x1].copy()
    bh, bw = cell.shape
    colsum = cell.sum(axis=0)
    for x in range(bw):
        if colsum[x] >= 0.9 * bh and (x < 4 or x > bw - 5):
            cell[:, x] = 0  # drop edge border column
    ys, xs = np.where(cell)
    if ys.size < 3:
        return None
    g = cell[ys.min():ys.max() + 1, xs.min():xs.max() + 1].astype(np.uint8)
    g = _trim_borders(g)
    return g, int(ys.max())  # glyph bottom within the row band (baseline metric)


def main() -> int:
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    chart, out_dir, name = sys.argv[1], sys.argv[2], sys.argv[3]
    os.makedirs(out_dir, exist_ok=True)
    im = Image.open(chart).convert("RGB")
    a = np.asarray(im).astype(int)
    W, H = im.size
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    black = (r < 70) & (g < 70) & (b < 70)

    bands = _row_bands(black)
    # Each printed row = a thin label strip + a TALL glyph band. The 6 content
    # glyph rows are the tall bands (height > 25); '!' lives in the topmost band.
    tall = [(y0, y1) for (y0, y1) in bands if (y1 - y0) > 25]
    top = bands[0] if bands else None
    print(f"{name}: {im.size}, {len(bands)} bands, {len(tall)} glyph rows")
    glyphs, offsets = {}, {}
    if top is not None:
        res = _extract_cell(black, top[0], top[1], COLS - 1, W)
        if res is not None:
            glyphs["!"], _ = res
    # content rows: row i, col c -> code 34 + i*COLS + c  ('"' .. '~')
    for i, (y0, y1) in enumerate(tall):
        row = {}
        for c in range(COLS):
            code = 34 + i * COLS + c
            if not (34 <= code <= 126):
                continue
            res = _extract_cell(black, y0, y1, c, W)
            if res is not None:
                row[chr(code)] = res  # (glyph, bottom_y)
        # baseline = median bottom of the CAP letters in this row; every glyph's
        # offset = how far ITS bottom sits below the baseline (caps 0, hyphen
        # negative=above, descenders positive=below).
        cap_bottoms = [b for ch, (_, b) in row.items() if ch.isupper()]
        baseline = float(np.median(cap_bottoms)) if cap_bottoms else \
            float(np.median([b for _, (_, b) in row.items()]))
        for ch, (g, b) in row.items():
            glyphs[ch] = g
            offsets[ch] = b - baseline
    print(f"  extracted {len(glyphs)} glyphs: {''.join(sorted(glyphs))}")

    payload = {f"c{ord(k)}": v for k, v in glyphs.items()}
    payload.update({f"o{ord(k)}": np.int16(v) for k, v in offsets.items()})
    np.savez_compressed(os.path.join(out_dir, f"{name}.glyphs.npz"), **payload)

    # verification contact sheet
    order = sorted(glyphs, key=ord)
    cw, chh = 30, 40
    per = 24
    rows = (len(order) + per - 1) // per
    sheet = Image.new("RGB", (per * cw, rows * (chh + 12)), (240, 240, 240))
    d = ImageDraw.Draw(sheet)
    for i, ch in enumerate(order):
        gl = glyphs[ch]
        g = Image.fromarray((1 - gl) * 255).resize((min(cw - 4, int(gl.shape[1] * chh / gl.shape[0]) or 1), chh - 4))
        rr, cc = divmod(i, per)
        sheet.paste(g, (cc * cw + 2, rr * (chh + 12) + 2))
        d.text((cc * cw + 2, rr * (chh + 12) + chh - 8), ch, fill=(180, 0, 0))
    out = os.path.join(out_dir, f"{name}.verify.png")
    sheet.save(out)
    print(f"  verify sheet -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
