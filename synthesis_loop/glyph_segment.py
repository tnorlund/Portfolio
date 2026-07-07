"""Glyph segmentation from a receipt photo (numpy-only).

Replaces equal-cell splitting (which cuts glyphs mid-stroke) with the standard
document-analysis pipeline -- adaptive binarization + projection-profile
segmentation -- specialized with one advantage generic OCR lacks: we already
KNOW the character count of each word, so segmentation becomes a constrained
"split this ink into exactly N glyphs" problem.

* :func:`sauvola_mask` -- local (per-window mean+std) threshold; robust to the
  thermal fade / lighting gradients that defeat a global Otsu on receipt photos.
* :func:`segment_to_n` -- vertical projection profile -> ink runs, then merge
  over-split dot-matrix fragments / cut fused pairs at the deepest valley until
  the run count equals N.
* :func:`glyph_boxes` -- ties them together and tight-crops each glyph to its
  own ink bbox.
"""

from __future__ import annotations

import numpy as np


def auto_polarity(gray: np.ndarray, dark_border: float = 110.0):
    """Normalize a glyph crop to dark-ink-on-light. Returns (crop, was_reverse).

    Reverse-video cells (white text in a black box) have a DARK border; we invert
    them so the downstream ink detector still finds the glyph. The flag lets the
    renderer reproduce the black box as a treatment.
    """
    if gray.size == 0:
        return gray, False
    border = np.concatenate([gray[0, :], gray[-1, :], gray[:, 0], gray[:, -1]])
    if float(np.median(border)) < dark_border:
        return (255 - gray.astype(np.int16)).clip(0, 255).astype(
            gray.dtype
        ), True
    return gray, False


def sauvola_mask(
    gray: np.ndarray, window: int = 25, k: float = 0.18, R: float = 128.0
) -> np.ndarray:
    """Boolean ink mask (True = ink) via Sauvola adaptive thresholding."""
    g = gray.astype(np.float64)
    H, W = g.shape
    win = max(3, min(window, H, W) | 1)  # odd, within bounds
    r = win // 2
    ii = np.pad(np.cumsum(np.cumsum(g, 0), 1), ((1, 0), (1, 0)))
    ii2 = np.pad(np.cumsum(np.cumsum(g * g, 0), 1), ((1, 0), (1, 0)))
    ys = np.arange(H)
    xs = np.arange(W)
    y0 = np.clip(ys - r, 0, H)[:, None]
    y1 = np.clip(ys + r + 1, 0, H)[:, None]
    x0 = np.clip(xs - r, 0, W)[None, :]
    x1 = np.clip(xs + r + 1, 0, W)[None, :]
    area = (y1 - y0) * (x1 - x0)
    s = ii[y1, x1] - ii[y0, x1] - ii[y1, x0] + ii[y0, x0]
    s2 = ii2[y1, x1] - ii2[y0, x1] - ii2[y1, x0] + ii2[y0, x0]
    mean = s / area
    var = np.clip(s2 / area - mean * mean, 0, None)
    std = np.sqrt(var)
    thresh = mean * (1.0 + k * (std / R - 1.0))
    return g < thresh


def _ink_runs(col_ink: np.ndarray) -> list[list[int]]:
    """Contiguous column spans [x0, x1) that carry ink."""
    on = col_ink > 0
    runs: list[list[int]] = []
    i = 0
    n = len(on)
    while i < n:
        if on[i]:
            j = i
            while j < n and on[j]:
                j += 1
            runs.append([i, j])
            i = j
        else:
            i += 1
    return runs


def segment_to_n(mask: np.ndarray, n: int) -> list[tuple[int, int]]:
    """Column spans for exactly ``n`` glyphs (merge fragments / split fusions)."""
    col = mask.sum(axis=0)
    runs = _ink_runs(col)
    if not runs or n <= 0:
        return []
    # Merge the smallest inter-run gaps until at most n runs (dot-matrix breaks).
    while len(runs) > n:
        gi = min(
            range(len(runs) - 1), key=lambda i: runs[i + 1][0] - runs[i][1]
        )
        runs[gi][1] = runs[gi + 1][1]
        del runs[gi + 1]
    # Split the widest run at its deepest interior projection valley (fused pair).
    while len(runs) < n:
        wi = max(range(len(runs)), key=lambda i: runs[i][1] - runs[i][0])
        a, b = runs[wi]
        if b - a <= 1:
            break
        sub = col[a:b].copy()
        sub[0] = sub[-1] = sub.max() + 1  # never cut at the very edges
        cut = a + int(np.argmin(sub))
        cut = min(max(cut, a + 1), b - 1)
        runs[wi] = [a, cut]
        runs.insert(wi + 1, [cut, b])
    return [(a, b) for a, b in runs]


def glyph_boxes(
    gray: np.ndarray, n: int, **kw
) -> list[tuple[int, int, int, int]]:
    """Per-glyph tight ink bboxes (x0, y0, x1, y1) for an N-character word crop."""
    mask = sauvola_mask(gray, **kw)
    boxes: list[tuple[int, int, int, int]] = []
    for x0, x1 in segment_to_n(mask, n):
        sub = mask[:, x0:x1]
        rows = np.where(sub.any(axis=1))[0]
        cols = np.where(sub.any(axis=0))[0]
        if rows.size == 0 or cols.size == 0:
            boxes.append((x0, 0, x1, gray.shape[0]))
            continue
        boxes.append(
            (
                x0 + int(cols.min()),
                int(rows.min()),
                x0 + int(cols.max()) + 1,
                int(rows.max()) + 1,
            )
        )
    return boxes
