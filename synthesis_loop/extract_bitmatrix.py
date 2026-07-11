#!/usr/bin/env python3
"""extract_bitmatrix.py -- build a clean glyph atlas from a receiptfont.com
specimen chart PNG (the bitMatrix / bitArray / pixCrog family).

The chart is a 17-column grid sweeping the printable ASCII table (`!` 0x21 ..
`~` 0x7E) row-major, right-aligned into a partial top row, then full rows.
Glyphs are pure black; per-cell labels are gray, and the grid/baseline guides
are a saturated color (BLUE on bitMatrix-C2 / pixCrog, GREEN on
bitMatrix-C1/D1/B2 and bitArray-A2). A black-only mask therefore isolates the
letterform on every chart in the family regardless of guide color.

Generalized from the Costco bitMatrix-C2 extractor (synthesis_loop history):
the C2 version hard-coded the codepoint of each cell as ``34 + i*17 + c`` with
a special case for `!`. That only holds for C2's left-aligned layout. The
green-guide charts are RIGHT-aligned (a fuller partial top row) and continue
past `~` into Latin-1 (`£ À Á ...`, even `€`). So this version assigns
codepoints by *reading order*: the first inked cell is `!` (0x21) and every
subsequent inked cell takes the next ASCII code, capping at `~` (0x7E). Empty
cells (the leading pad of the partial top row, trailing cells) are skipped
without consuming a codepoint; there are no interior gaps because every
printable ASCII glyph carries ink. The Latin-1 tail is intentionally ignored
-- receipt text is ASCII, so the atlas only needs 0x21..0x7E.

Output: <out>/<name>.glyphs.npz (char -> binary glyph + per-char baseline
offset), <out>/<name>.manifest.json (chart source, grid params, coverage), and
<out>/<name>.verify.png (labeled contact sheet).

Usage: extract_bitmatrix.py <chart.png> <out_dir> <atlas_name> [--cols 17]
"""
from __future__ import annotations

import argparse
import json
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

COLS = 17
FIRST_CODE = 0x21  # '!'
LAST_CODE = 0x7E  # '~'
MIN_ROW_HEIGHT = 20  # a glyph row band is taller than this (label strips are
#                      gray -> excluded from the black mask, so every black
#                      band is a glyph row; the threshold only drops specks)


def _row_bands(mask):
    """Y-bands (row ranges) that contain glyph ink, top->bottom."""
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


def _extract_cell(black, y0, y1, c, W, cols):
    """Return (trimmed glyph, glyph_bottom_y_in_band) or None.

    Removes any full-height vertical CELL BORDER (near either cell edge) BEFORE
    measuring, so the glyph's real bottom -- not a border's -- sets the
    baseline. (Colored guides are already dropped by the black mask; this only
    fires on the rare chart that inks its rules.)
    """
    cell_w = W / cols
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
    if g.size == 0 or g.sum() < 3:
        return None
    return g, int(ys.max())  # glyph bottom within the row band (baseline metric)


def extract(chart: str, cols: int = COLS):
    """Chart PNG -> (glyphs, offsets, meta). Pure; no disk writes."""
    im = Image.open(chart).convert("RGB")
    a = np.asarray(im).astype(int)
    W, H = im.size
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    black = (r < 70) & (g < 70) & (b < 70)

    bands = _row_bands(black)
    glyph_rows = [(y0, y1) for (y0, y1) in bands if (y1 - y0) > MIN_ROW_HEIGHT]

    glyphs: dict[str, np.ndarray] = {}
    offsets: dict[str, float] = {}
    next_code = FIRST_CODE
    row_report = []
    started = False  # first inked cell seen (the leading pad is over)
    for (y0, y1) in glyph_rows:
        if next_code > LAST_CODE:
            break
        row = {}  # col -> (char, glyph, bottom_y)
        for c in range(cols):
            if next_code > LAST_CODE:
                break
            res = _extract_cell(black, y0, y1, c, W, cols)
            if res is None:
                # Only the leading pad of the right-aligned partial top row
                # may be empty. Every printable ASCII glyph carries ink, so an
                # interior empty cell means the black-mask threshold missed a
                # glyph -- and silently skipping it would shift every
                # subsequent codepoint (the Latin-1 tail would then backfill
                # the count to a plausible-looking 94). Fail loudly instead.
                if started:
                    raise RuntimeError(
                        f"empty cell at row band ({y0},{y1}) col {c} while "
                        f"expecting {chr(next_code)!r} (0x{next_code:02x}): "
                        "codepoint mapping would shift; chart layout or "
                        "threshold mismatch"
                    )
                continue  # leading pad: skip WITHOUT consuming a codepoint
            started = True
            ch = chr(next_code)
            row[c] = (ch, res[0], res[1])
            next_code += 1
        if not row:
            continue
        # baseline = median bottom of the CAP letters in this row; every glyph's
        # offset = how far ITS bottom sits below the baseline (caps 0, hyphen
        # negative=above, descenders positive=below).
        cap_bottoms = [bt for (ch, _, bt) in row.values() if ch.isupper()]
        baseline = (
            float(np.median(cap_bottoms))
            if cap_bottoms
            else float(np.median([bt for (_, _, bt) in row.values()]))
        )
        for c, (ch, gl, bt) in sorted(row.items()):
            glyphs[ch] = gl
            offsets[ch] = bt - baseline
        row_report.append(
            {
                "y_band": [int(y0), int(y1)],
                "chars": "".join(ch for (ch, _, _) in (row[c] for c in sorted(row))),
            }
        )

    expected = LAST_CODE - FIRST_CODE + 1
    if len(glyphs) != expected:
        raise RuntimeError(
            f"incomplete extraction: {len(glyphs)}/{expected} glyphs "
            f"(missing {''.join(chr(c) for c in range(FIRST_CODE, LAST_CODE + 1) if chr(c) not in glyphs)!r}); "
            "refusing to emit a partial atlas"
        )
    meta = {
        "chart": os.path.abspath(chart),
        "image_size": [W, H],
        "cols": cols,
        "n_bands": len(bands),
        "n_glyph_rows": len(glyph_rows),
        "first_code": FIRST_CODE,
        "last_code": LAST_CODE,
        "glyph_count": len(glyphs),
        "coverage": "".join(sorted(glyphs, key=ord)),
        "rows": row_report,
    }
    return glyphs, offsets, meta


def write_outputs(glyphs, offsets, meta, out_dir, name):
    os.makedirs(out_dir, exist_ok=True)
    payload = {f"c{ord(k)}": v for k, v in glyphs.items()}
    # round-half-away instead of int16 truncation: an even cap count medians
    # to x.5 and truncation would bias those glyphs up a pixel
    payload.update(
        {f"o{ord(k)}": np.int16(round(v)) for k, v in offsets.items()}
    )
    npz_path = os.path.join(out_dir, f"{name}.glyphs.npz")
    np.savez_compressed(npz_path, **payload)

    manifest = dict(meta)
    manifest["name"] = name
    manifest["npz"] = os.path.abspath(npz_path)
    man_path = os.path.join(out_dir, f"{name}.manifest.json")
    with open(man_path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)

    # verification contact sheet
    order = sorted(glyphs, key=ord)
    cw, chh, per = 30, 40, 24
    rows = (len(order) + per - 1) // per
    sheet = Image.new("RGB", (per * cw, rows * (chh + 12)), (240, 240, 240))
    d = ImageDraw.Draw(sheet)
    for i, ch in enumerate(order):
        gl = glyphs[ch]
        tile = Image.fromarray((1 - gl) * 255).resize(
            (min(cw - 4, int(gl.shape[1] * chh / gl.shape[0]) or 1), chh - 4)
        )
        rr, cc = divmod(i, per)
        sheet.paste(tile, (cc * cw + 2, rr * (chh + 12) + 2))
        d.text((cc * cw + 2, rr * (chh + 12) + chh - 8), ch, fill=(180, 0, 0))
    verify_path = os.path.join(out_dir, f"{name}.verify.png")
    sheet.save(verify_path)
    return npz_path, man_path, verify_path


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("chart")
    ap.add_argument("out_dir")
    ap.add_argument("name")
    ap.add_argument("--cols", type=int, default=COLS)
    args = ap.parse_args(argv)

    glyphs, offsets, meta = extract(args.chart, args.cols)
    npz_path, man_path, verify_path = write_outputs(
        glyphs, offsets, meta, args.out_dir, args.name
    )
    print(
        f"{args.name}: {meta['image_size']}, {meta['n_bands']} bands, "
        f"{meta['n_glyph_rows']} glyph rows"
    )
    print(f"  extracted {meta['glyph_count']} glyphs: {meta['coverage']}")
    print(f"  npz     -> {npz_path}")
    print(f"  manifest-> {man_path}")
    print(f"  verify  -> {verify_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
