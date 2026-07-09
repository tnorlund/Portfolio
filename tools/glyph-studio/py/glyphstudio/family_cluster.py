"""M2: font-family discovery by shape-normalized glyph IoU across merchants.

The corpus prints ~2-4 typeface families, not 9 unique fonts (epic sec. 2). This
measures that directly: rasterize each merchant's glyphs, shape-normalize them
(crop-to-ink + resize to a fixed grid, so size/weight cancel), and compute the
mean IoU over shared letterforms for every merchant pair. Merchants whose glyphs
overlap above a threshold are one family; the result is the seed of the
``(merchant, section) -> (family, face)`` map.

Measured on the current 9 refined fonts (aspect-preserving shape IoU): the
tightest real pair is **cvs~vons** (reproducing the epic's strongest same-font
signal, CVS<->Vons); the corpus collapses to one family at t~0.55 and splits
into a few sub-families by t~0.60. **HomeDepot** is the consistent outlier on
the refined fonts (it isolates at every threshold >= 0.56) — note this differs
from the M0 *atlases*, where Costco was the outlier; Costco's refined font has
converged toward the group. Absolute IoU is method-dependent, so the threshold
is chosen from this method's own distribution, not the epic's atlas numbers.

Pure numpy — the raster/schema helpers already exist; this only adds the
normalization, IoU, and clustering on top.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

import numpy as np

# letterforms to compare on (shared across merchants; skip punctuation/rare)
DEFAULT_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"


def normalize_glyph(bitmap: np.ndarray, size: int = 32) -> np.ndarray:
    """Crop a glyph to its ink and fit into a ``size``x``size`` canvas
    **preserving aspect ratio**, centered.

    Size-invariant but aspect-PRESERVING: for the same character, width/height
    proportion is a font signal (condensed vs wide), so a square resample would
    throw away discrimination. Scale-to-fit + center keeps it. Dependency-free
    nearest-neighbor resample.
    """
    b = np.asarray(bitmap) > 0
    if not b.any():
        return np.zeros((size, size), dtype=bool)
    ys, xs = np.where(b)
    crop = b[ys.min(): ys.max() + 1, xs.min(): xs.max() + 1]
    h, w = crop.shape
    scale = size / max(h, w)                       # fit the longer side
    th, tw = max(1, round(h * scale)), max(1, round(w * scale))
    yi = np.minimum((np.arange(th) * h) // th, h - 1)
    xi = np.minimum((np.arange(tw) * w) // tw, w - 1)
    resized = crop[np.ix_(yi, xi)]
    out = np.zeros((size, size), dtype=bool)
    y0, x0 = (size - th) // 2, (size - tw) // 2    # center
    out[y0: y0 + th, x0: x0 + tw] = resized
    return out


def glyph_iou(a_norm: np.ndarray, b_norm: np.ndarray) -> float:
    """Intersection-over-union of two normalized binary glyphs."""
    inter = np.logical_and(a_norm, b_norm).sum()
    union = np.logical_or(a_norm, b_norm).sum()
    return float(inter / union) if union else 0.0


def merchant_iou(
    a: dict[int, np.ndarray],
    b: dict[int, np.ndarray],
    chars: str = DEFAULT_CHARS,
) -> tuple[float, int]:
    """Mean glyph IoU over the letterforms shared by two merchants.

    ``a``/``b`` map codepoint -> normalized glyph. Returns (mean_iou, n_shared).
    """
    ious = []
    for ch in chars:
        cp = ord(ch)
        if cp in a and cp in b:
            ious.append(glyph_iou(a[cp], b[cp]))
    if not ious:
        return 0.0, 0
    return float(np.mean(ious)), len(ious)


@dataclass
class FamilyResult:
    merchants: list[str]
    iou: np.ndarray                 # (n, n) symmetric mean-IoU matrix
    shared: np.ndarray              # (n, n) shared-letterform counts
    families: list[list[str]]       # clustered merchant groups
    threshold: float


def pairwise_iou(
    normalized: dict[str, dict[int, np.ndarray]],
    chars: str = DEFAULT_CHARS,
) -> tuple[list[str], np.ndarray, np.ndarray]:
    """Full pairwise merchant IoU matrix from normalized glyph dicts."""
    merchants = sorted(normalized)
    n = len(merchants)
    iou = np.eye(n, dtype=float)
    shared = np.zeros((n, n), dtype=int)
    for i in range(n):
        for j in range(i + 1, n):
            m, k = merchant_iou(
                normalized[merchants[i]], normalized[merchants[j]], chars
            )
            iou[i, j] = iou[j, i] = m
            shared[i, j] = shared[j, i] = k
    return merchants, iou, shared


def cluster_families(
    merchants: Sequence[str], iou: np.ndarray, threshold: float = 0.65
) -> list[list[str]]:
    """Single-linkage agglomeration: merchants linked when IoU >= threshold.

    Union-find over pairs above threshold. Same-font pairs (~0.7) merge;
    different-font pairs (~0.55) and the Costco outlier stay separate.
    """
    n = len(merchants)
    parent = list(range(n))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for i in range(n):
        for j in range(i + 1, n):
            if iou[i, j] >= threshold:
                parent[find(i)] = find(j)

    groups: dict[int, list[str]] = {}
    for i in range(n):
        groups.setdefault(find(i), []).append(merchants[i])
    return sorted((sorted(g) for g in groups.values()), key=lambda g: -len(g))


# --- loading (uses the existing raster/schema toolchain) ------------------


def load_normalized_merchant(
    font_dir: str, chars: str = DEFAULT_CHARS, size: int = 32
) -> dict[int, np.ndarray]:
    """Rasterize + shape-normalize a merchant's glyphs for the given chars."""
    from glyphstudio import REF_CAP
    from glyphstudio.raster import rasterize_glyph
    from glyphstudio.schema import load_font, load_glyphs, merged_params

    font = load_font(font_dir)
    glyphs = load_glyphs(font_dir)
    out: dict[int, np.ndarray] = {}
    wanted = {ord(c) for c in chars}
    for cp, glyph in glyphs.items():
        if cp not in wanted:
            continue
        params = merged_params(font, glyph)
        bitmap, _ = rasterize_glyph(glyph, params, REF_CAP)
        out[cp] = normalize_glyph(bitmap, size=size)
    return out


def discover_families(
    font_dirs: dict[str, str],
    chars: str = DEFAULT_CHARS,
    threshold: float = 0.65,
) -> FamilyResult:
    """End-to-end: merchant font dirs -> IoU matrix + families."""
    normalized = {
        name: load_normalized_merchant(d, chars) for name, d in font_dirs.items()
    }
    merchants, iou, shared = pairwise_iou(normalized, chars)
    families = cluster_families(merchants, iou, threshold)
    return FamilyResult(merchants, iou, shared, families, threshold)
