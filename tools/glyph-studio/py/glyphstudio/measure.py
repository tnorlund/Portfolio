"""Numerically measure a character's real-print consensus geometry.

Generalizes the hand analysis that produced the authored 'A': convert the
soft consensus map into the numbers an author needs — ink bbox in cap units,
per-height ink spans, horizontal-bar runs (crossbars), vertical-stem columns,
stroke width, hole count. Authors place centerlines from these measurements
(remember: ink extends dot/2 past the centerline).

Usage:
  python -m glyphstudio.measure <samples.npz> <char> [--threshold 0.45]
Prints one JSON object; {"available": false} when the corpus lacks the char.
"""

from __future__ import annotations

import argparse
import json
import sys

import numpy as np

from .samples import canvas_geometry, consensus_soft, load_stack


def measure_char(samples_path: str, ch: str, threshold: float = 0.45) -> dict:
    stack = load_stack(samples_path, ord(ch))
    if stack is None or not len(stack):
        return {"char": ch, "available": False}
    soft = consensus_soft(stack)
    ref_cap, base_row = canvas_geometry(stack.shape[1])
    U = 1000.0 / ref_cap
    ink = soft >= threshold
    ys, xs = np.nonzero(ink)
    if not len(ys):
        return {"char": ch, "available": False}
    x0 = int(xs.min())

    def yu(row: float) -> float:
        return round((base_row - row) * U, 1)

    def xu(col: float) -> float:
        return round((col - x0) * U, 1)

    top, bot = int(ys.min()), int(ys.max())
    out: dict = {
        "char": ch,
        "available": True,
        "samples": int(len(stack)),
        "units_note": "cap units, y-up, baseline 0; ink extends dot/2 (50u) past centerlines",
        "ink_bbox": {
            "y_top": yu(top),
            "y_bottom": yu(bot),
            "x_left": 0.0,
            "x_right": xu(int(xs.max())),
        },
    }

    # ink spans at reference heights (top -> bottom)
    spans = {}
    for frac in (0.05, 0.25, 0.5, 0.75, 0.95):
        r = min(bot, int(top + frac * (bot - top)))
        cols = np.nonzero(ink[r])[0]
        if len(cols):
            runs = []
            start = prev = int(cols[0])
            for c in cols[1:]:
                if c == prev + 1:
                    prev = int(c)
                else:
                    runs.append([xu(start), xu(prev)])
                    start = prev = int(c)
            runs.append([xu(start), xu(prev)])
            spans[f"{int(frac*100)}%_from_top"] = {"y": yu(r), "runs": runs}
    out["row_spans"] = spans

    # horizontal bars: rows where the middle third is nearly solid
    w = int(xs.max()) - x0
    mid = ink[:, x0 + w // 3 : x0 + 2 * w // 3]
    solid = [r for r in range(top, bot + 1) if mid[r].mean() > 0.85]
    bars = []
    if solid:
        start = prev = solid[0]
        for r in solid[1:]:
            if r == prev + 1:
                prev = r
            else:
                bars.append({"y_from": yu(prev), "y_to": yu(start)})
                start = prev = r
        bars.append({"y_from": yu(prev), "y_to": yu(start)})
    out["horizontal_bars"] = bars

    # vertical stems: columns inked for >60% of the glyph's height
    col_fill = ink[top : bot + 1].mean(axis=0)
    stem_cols = [c for c in range(x0, int(xs.max()) + 1) if col_fill[c] > 0.6]
    stems = []
    if stem_cols:
        start = prev = stem_cols[0]
        for c in stem_cols[1:]:
            if c == prev + 1:
                prev = c
            else:
                stems.append({"x_from": xu(start), "x_to": xu(prev)})
                start = prev = c
        stems.append({"x_from": xu(start), "x_to": xu(prev)})
    out["vertical_stems"] = stems

    # stroke width: median horizontal run length across all rows (in units)
    runs_px: list[int] = []
    for r in range(top, bot + 1):
        cols = np.nonzero(ink[r])[0]
        if not len(cols):
            continue
        start = prev = int(cols[0])
        for c in cols[1:]:
            if c == prev + 1:
                prev = int(c)
            else:
                if prev - start + 1 <= 20:
                    runs_px.append(prev - start + 1)
                start = prev = int(c)
        if prev - start + 1 <= 20:
            runs_px.append(prev - start + 1)
    if runs_px:
        out["stroke_width_units"] = round(float(np.median(runs_px)) * U, 1)

    # topology: holes via flood fill from padded background
    bg = ~ink
    padded = np.pad(bg, 1, constant_values=True)
    lab = np.zeros(padded.shape, dtype=bool)
    stack_px = [(0, 0)]
    lab[0, 0] = True
    while stack_px:
        y, x = stack_px.pop()
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            ny, nx = y + dy, x + dx
            if (
                0 <= ny < padded.shape[0]
                and 0 <= nx < padded.shape[1]
                and padded[ny, nx]
                and not lab[ny, nx]
            ):
                lab[ny, nx] = True
                stack_px.append((ny, nx))
    out["holes"] = int(padded.sum() - lab.sum() > 0) and int(
        _count_components(padded & ~lab)
    )
    return out


def _count_components(mask: np.ndarray) -> int:
    lab = np.zeros(mask.shape, dtype=int)
    cur = 0
    for y, x in zip(*np.nonzero(mask)):
        if lab[y, x]:
            continue
        cur += 1
        stk = [(y, x)]
        lab[y, x] = cur
        while stk:
            cy, cx = stk.pop()
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    ny, nx = cy + dy, cx + dx
                    if (
                        0 <= ny < mask.shape[0]
                        and 0 <= nx < mask.shape[1]
                        and mask[ny, nx]
                        and not lab[ny, nx]
                    ):
                        lab[ny, nx] = cur
                        stk.append((ny, nx))
    return cur


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("samples")
    ap.add_argument("char")
    ap.add_argument("--threshold", type=float, default=0.45)
    args = ap.parse_args(argv)
    print(json.dumps(measure_char(args.samples, args.char, args.threshold)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
