"""Stroke skeleton + params -> binary glyph bitmap (the only rasterizer).

Dots are stamped along each stroke's arc length at ``pitch × diameter``
spacing on a supersampled canvas, then block-mean downsampled and
thresholded — overlapping dots make natural joins, and supersampling turns
curve edges into believable pixel stairsteps at REF_CAP=60 without scipy.
"""

from __future__ import annotations

import numpy as np

from . import CAP_UNITS

# Canvas layout in downsampled (REF_CAP) pixels: tall enough for ascender +
# descender, baseline deliberately mirrors build_merchant_glyphs semantics
# (offsets = ink bottom row - baseline row).
CANVAS_H_CAPS = 4.0
CANVAS_W_CAPS = 3.0
BASELINE_CAPS = 2.5


def _segments(stroke: dict) -> list[dict]:
    """Stroke nodes -> [{"kind","p0","p1","p2","p3"}] (lines/cubics)."""
    nodes = stroke["nodes"]
    closed = bool(stroke.get("closed"))
    pairs = list(zip(nodes[:-1], nodes[1:]))
    if closed and len(nodes) >= 2:
        pairs.append((nodes[-1], nodes[0]))
    segs = []
    for a, b in pairs:
        h_out = a.get("hOut")
        h_in = b.get("hIn")
        p0 = np.array([a["x"], a["y"]], dtype=float)
        p3 = np.array([b["x"], b["y"]], dtype=float)
        if h_out is None and h_in is None:
            segs.append({"kind": "line", "pts": np.array([p0, p3])})
        else:
            p1 = np.array([h_out["x"], h_out["y"]]) if h_out else p0.copy()
            p2 = np.array([h_in["x"], h_in["y"]]) if h_in else p3.copy()
            segs.append({"kind": "cubic", "pts": np.array([p0, p1, p2, p3])})
    return segs


def _flatten_cubic(ctrl: np.ndarray, tol: float) -> np.ndarray:
    """Adaptive De Casteljau subdivision until control-polygon flatness < tol."""
    p0, p1, p2, p3 = ctrl

    def _cross2(a, b):
        return abs(a[0] * b[1] - a[1] * b[0])

    chord = max(np.linalg.norm(p3 - p0), 1e-9)
    d1 = _cross2(p3 - p0, p1 - p0) / chord
    d2 = _cross2(p3 - p0, p2 - p0) / chord
    if max(d1, d2) <= tol:
        return np.array([p0, p3])
    mid = lambda a, b: (a + b) / 2.0  # noqa: E731
    p01, p12, p23 = mid(p0, p1), mid(p1, p2), mid(p2, p3)
    p012, p123 = mid(p01, p12), mid(p12, p23)
    p0123 = mid(p012, p123)
    left = _flatten_cubic(np.array([p0, p01, p012, p0123]), tol)
    right = _flatten_cubic(np.array([p0123, p123, p23, p3]), tol)
    return np.concatenate([left[:-1], right])


def _dot_mask(radius_px: float, shape: str) -> np.ndarray:
    r = max(1, int(round(radius_px)))
    if shape == "square":
        return np.ones((2 * r, 2 * r), dtype=bool)
    yy, xx = np.mgrid[-r : r + 1, -r : r + 1]
    return (xx * xx + yy * yy) <= radius_px * radius_px


def _stamp(canvas: np.ndarray, mask: np.ndarray, cy: float, cx: float) -> None:
    mh, mw = mask.shape
    top = int(round(cy - mh / 2.0))
    left = int(round(cx - mw / 2.0))
    y0, x0 = max(0, top), max(0, left)
    y1 = min(canvas.shape[0], top + mh)
    x1 = min(canvas.shape[1], left + mw)
    if y1 <= y0 or x1 <= x0:
        return
    canvas[y0:y1, x0:x1] |= mask[y0 - top : y1 - top, x0 - left : x1 - left]


def rasterize_glyph(
    glyph: dict, params: dict, ref_cap: int
) -> tuple[np.ndarray, int]:
    """Rasterize one glyph -> (tight-cropped uint8 bitmap, baseline offset).

    Matches the npz contract exactly: bitmap is binary {0,1}, offset is
    ink-bottom-row minus baseline-row (caps ≈ 0, descenders > 0).
    """
    s = int(params.get("supersample", 4))
    px_per_unit = ref_cap * s / CAP_UNITS
    canvas_h = int(round(CANVAS_H_CAPS * ref_cap * s))
    canvas_w = int(round(CANVAS_W_CAPS * ref_cap * s))
    baseline_row = int(round(BASELINE_CAPS * ref_cap * s))
    canvas = np.zeros((canvas_h, canvas_w), dtype=bool)

    tracking = float(params.get("trackingScale", 1.0))
    slant = float(params.get("slant", 0.0))
    weight = float(params.get("weight", 1.0))
    dot = params.get("dot", {})
    dot_units = float(dot.get("size", 110)) * weight
    dot_px = dot_units * px_per_unit
    pitch = float(dot.get("pitch", 0.4))
    mask = _dot_mask(dot_px / 2.0, str(dot.get("shape", "round")))

    # x margin so left-most ink lands on-canvas
    x_margin = dot_px

    def to_px(pt: np.ndarray) -> np.ndarray:
        x = (pt[0] * tracking + slant * pt[1]) * px_per_unit + x_margin
        y = baseline_row - pt[1] * px_per_unit
        return np.array([y, x])

    any_ink = False
    for stroke in glyph.get("strokes", []):
        for seg in _segments(stroke):
            if seg["kind"] == "line":
                poly = seg["pts"]
            else:
                poly = _flatten_cubic(seg["pts"], tol=0.25 / px_per_unit)
            poly_px = np.array([to_px(p) for p in poly])
            # arc-length stepping
            deltas = np.linalg.norm(np.diff(poly_px, axis=0), axis=1)
            total = float(deltas.sum())
            step = max(1.0, pitch * dot_px)
            n_steps = max(1, int(np.ceil(total / step)))
            targets = np.linspace(0.0, total, n_steps + 1)
            cum = np.concatenate([[0.0], np.cumsum(deltas)])
            for t in targets:
                idx = int(np.searchsorted(cum, t, side="right") - 1)
                idx = min(idx, len(poly_px) - 2)
                seg_len = cum[idx + 1] - cum[idx]
                frac = 0.0 if seg_len <= 0 else (t - cum[idx]) / seg_len
                pt = poly_px[idx] * (1 - frac) + poly_px[idx + 1] * frac
                _stamp(canvas, mask, pt[0], pt[1])
                any_ink = True
    if not any_ink:
        return np.zeros((1, 1), dtype=np.uint8), 0

    # Downsample by block mean, threshold 0.5
    down = canvas.reshape(canvas_h // s, s, canvas_w // s, s).mean(axis=(1, 3))
    binary = (down >= 0.5).astype(np.uint8)

    ys, xs = np.nonzero(binary)
    if len(ys) == 0:
        return np.zeros((1, 1), dtype=np.uint8), 0
    y0, y1 = int(ys.min()), int(ys.max())
    x0, x1 = int(xs.min()), int(xs.max())
    cropped = binary[y0 : y1 + 1, x0 : x1 + 1]
    baseline_down = baseline_row // s
    offset = int(y1 - baseline_down) + int(glyph.get("baselineNudgePx", 0))
    return cropped, offset
