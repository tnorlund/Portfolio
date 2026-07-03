"""Real-letterform corpus access: consensus bitmaps + onion-skin PNG slices.

The corpus (``*.samples.npz`` from build_merchant_glyphs.py) stores, per
``str(codepoint)`` key, a stack of baseline-and-center-aligned boolean
canvases (H = 3*ref_cap, baseline row = int(ref_cap*2.3)).
"""
from __future__ import annotations

import numpy as np


def canvas_geometry(canvas_h: int) -> tuple[int, int]:
    """(sample_ref_cap, baseline_row) for a corpus canvas height."""
    ref_cap = canvas_h // 3
    return ref_cap, int(ref_cap * 2.3)


def load_stack(samples_path: str, codepoint: int) -> np.ndarray | None:
    with np.load(samples_path, allow_pickle=False) as data:
        key = str(codepoint)
        if key not in data:
            return None
        return data[key].astype(bool)


def list_codepoints(samples_path: str) -> list[int]:
    with np.load(samples_path, allow_pickle=False) as data:
        return sorted(int(k) for k in data.files if k.isdigit())


def _components(mask: np.ndarray) -> np.ndarray:
    """Connected-component labels (8-conn), pure numpy min-label diffusion."""
    labels = np.where(
        mask, np.arange(mask.size, dtype=np.int64).reshape(mask.shape) + 1, 0
    )
    while True:
        p = np.pad(labels, 1, constant_values=0)
        stacked = np.stack([
            p[:-2, :-2], p[:-2, 1:-1], p[:-2, 2:],
            p[1:-1, :-2], p[1:-1, 2:],
            p[2:, :-2], p[2:, 1:-1], p[2:, 2:],
        ])
        stacked = np.where(stacked == 0, np.iinfo(np.int64).max, stacked)
        neighbor_min = stacked.min(axis=0)
        new = np.where(
            mask, np.minimum(labels, np.minimum(neighbor_min, labels)), 0
        )
        new = np.where(
            mask & (neighbor_min != np.iinfo(np.int64).max),
            np.minimum(labels, neighbor_min), labels,
        )
        new = np.where(mask, new, 0)
        if np.array_equal(new, labels):
            return labels
        labels = new


def despeckle(mask: np.ndarray, min_px: int = 4) -> np.ndarray:
    labels = _components(mask)
    out = mask.copy()
    for lab in np.unique(labels):
        if lab == 0:
            continue
        sel = labels == lab
        if int(sel.sum()) < min_px:
            out[sel] = False
    return out


def fill_pinholes(mask: np.ndarray) -> np.ndarray:
    """Paper pixels with >= 7 of 8 ink neighbors become ink."""
    p = np.pad(mask.astype(np.uint8), 1)
    count = (
        p[:-2, :-2] + p[:-2, 1:-1] + p[:-2, 2:]
        + p[1:-1, :-2] + p[1:-1, 2:]
        + p[2:, :-2] + p[2:, 1:-1] + p[2:, 2:]
    )
    return mask | ((~mask) & (count >= 7))


def consensus(stack: np.ndarray, min_px: int = 4) -> np.ndarray:
    """Area-honest consensus of a sample stack.

    Threshold on the mean-fraction map is searched so the consensus ink area
    matches the median per-sample ink area — a fixed 0.5 threshold thins
    strokes wherever prints jitter, which is exactly the defect class
    (diagonals) we care most about.
    """
    frac = stack.mean(axis=0)
    target = float(np.median(stack.reshape(len(stack), -1).sum(axis=1)))
    best_t, best_diff = 0.5, float("inf")
    for t in np.linspace(0.35, 0.65, 13):
        area = float((frac >= t).sum())
        diff = abs(area - target)
        if diff < best_diff:
            best_t, best_diff = float(t), diff
    mask = frac >= best_t
    return fill_pinholes(despeckle(mask, min_px=min_px))


def upsample(mask: np.ndarray, factor: int = 3) -> np.ndarray:
    return np.repeat(np.repeat(mask, factor, axis=0), factor, axis=1)


def stroke_width_px(mask: np.ndarray, skeleton: np.ndarray) -> float:
    """Mean stroke width = ink area / skeleton length."""
    length = float(skeleton.sum())
    if length <= 0:
        return 0.0
    return float(mask.sum()) / length
