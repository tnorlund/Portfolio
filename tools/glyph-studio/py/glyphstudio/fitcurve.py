"""Corner detection + Schneider cubic fitting (Graphics Gems), pure numpy.

Polylines from the skeleton walk are noisy pixel chains; this converts them
to the editor's node model: corner-split runs, each emitted as a straight
line (when the chord fits) or a chain of least-squares cubics.
"""
from __future__ import annotations

import numpy as np


def smooth_polyline(pts: np.ndarray, passes: int = 1) -> np.ndarray:
    """3-tap moving average, endpoints pinned."""
    out = pts.astype(float).copy()
    for _ in range(passes):
        if len(out) < 3:
            return out
        inner = (out[:-2] + out[1:-1] + out[2:]) / 3.0
        out[1:-1] = inner
    return out


def find_corners(pts: np.ndarray, angle_deg: float = 40.0, win: int = 3) -> list[int]:
    """Indices where the turning angle over a +/-win window exceeds threshold."""
    n = len(pts)
    if n < 2 * win + 1:
        return []
    corners = []
    cos_thresh = np.cos(np.radians(angle_deg))
    for i in range(win, n - win):
        v1 = pts[i] - pts[i - win]
        v2 = pts[i + win] - pts[i]
        n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
        if n1 < 1e-9 or n2 < 1e-9:
            continue
        if np.dot(v1, v2) / (n1 * n2) < cos_thresh:
            corners.append(i)
    # non-maximum suppression: keep sharpest per cluster of adjacent indices
    if not corners:
        return []
    merged = [corners[0]]
    for idx in corners[1:]:
        if idx - merged[-1] <= win:
            continue
        merged.append(idx)
    return merged


def _chord_deviation(pts: np.ndarray) -> float:
    a, b = pts[0], pts[-1]
    ab = b - a
    denom = np.linalg.norm(ab)
    if denom < 1e-9:
        return float(np.max(np.linalg.norm(pts - a, axis=1)))
    cross = np.abs(ab[0] * (pts[:, 1] - a[1]) - ab[1] * (pts[:, 0] - a[0]))
    return float(np.max(cross / denom))


def _bezier_point(ctrl: np.ndarray, t: np.ndarray) -> np.ndarray:
    mt = 1 - t
    return (
        (mt ** 3)[:, None] * ctrl[0]
        + 3 * (mt ** 2 * t)[:, None] * ctrl[1]
        + 3 * (mt * t ** 2)[:, None] * ctrl[2]
        + (t ** 3)[:, None] * ctrl[3]
    )


def _chord_params(pts: np.ndarray) -> np.ndarray:
    d = np.linalg.norm(np.diff(pts, axis=0), axis=1)
    u = np.concatenate([[0.0], np.cumsum(d)])
    total = u[-1]
    return u / total if total > 0 else np.linspace(0, 1, len(pts))


def _generate_bezier(pts, u, t_left, t_right):
    """Least-squares inner control points given end tangents (Schneider)."""
    n = len(pts)
    a1 = (3 * (1 - u) ** 2 * u)[:, None] * t_left
    a2 = (3 * (1 - u) * u ** 2)[:, None] * t_right
    c11 = np.sum(a1 * a1)
    c12 = np.sum(a1 * a2)
    c22 = np.sum(a2 * a2)
    base = (
        ((1 - u) ** 3 + 3 * (1 - u) ** 2 * u)[:, None] * pts[0]
        + (u ** 3 + 3 * (1 - u) * u ** 2)[:, None] * pts[-1]
    )
    tmp = pts - base
    x1 = np.sum(a1 * tmp)
    x2 = np.sum(a2 * tmp)
    det = c11 * c22 - c12 * c12
    if abs(det) < 1e-12:
        alpha1 = alpha2 = np.linalg.norm(pts[-1] - pts[0]) / 3.0
    else:
        alpha1 = (x1 * c22 - x2 * c12) / det
        alpha2 = (c11 * x2 - c12 * x1) / det
        seg = np.linalg.norm(pts[-1] - pts[0])
        eps = 1e-6 * seg
        if alpha1 < eps or alpha2 < eps:
            alpha1 = alpha2 = seg / 3.0
    return np.array([
        pts[0],
        pts[0] + t_left * alpha1,
        pts[-1] + t_right * alpha2,
        pts[-1],
    ])


def _reparameterize(pts, u, ctrl):
    """One Newton-Raphson step per parameter."""
    p = _bezier_point(ctrl, u)
    d1c = 3 * np.diff(ctrl, axis=0)
    d2c = 2 * np.diff(d1c, axis=0)
    mt = 1 - u
    d1 = (
        (mt ** 2)[:, None] * d1c[0]
        + (2 * mt * u)[:, None] * d1c[1]
        + (u ** 2)[:, None] * d1c[2]
    )
    d2 = mt[:, None] * d2c[0] + u[:, None] * d2c[1]
    diff = p - pts
    num = np.sum(diff * d1, axis=1)
    den = np.sum(d1 * d1, axis=1) + np.sum(diff * d2, axis=1)
    safe = np.abs(den) > 1e-12
    unew = u.copy()
    unew[safe] = u[safe] - num[safe] / den[safe]
    return np.clip(unew, 0.0, 1.0)


def _max_error(pts, u, ctrl):
    p = _bezier_point(ctrl, u)
    errs = np.linalg.norm(p - pts, axis=1)
    idx = int(np.argmax(errs))
    return float(errs[idx]), idx


def fit_cubics(pts: np.ndarray, tol: float, depth: int = 0) -> list[np.ndarray]:
    """Schneider FitCurve: list of cubic control-point arrays (4x2)."""
    pts = np.asarray(pts, dtype=float)
    if len(pts) < 2:
        return []
    if len(pts) == 2:
        third = (pts[1] - pts[0]) / 3.0
        return [np.array([pts[0], pts[0] + third, pts[1] - third, pts[1]])]

    t_left = pts[1] - pts[0]
    t_right = pts[-2] - pts[-1]
    for v in (t_left, t_right):
        n = np.linalg.norm(v)
        if n > 1e-9:
            v /= n
    u = _chord_params(pts)
    ctrl = _generate_bezier(pts, u, t_left, t_right)
    err, split = _max_error(pts, u, ctrl)
    if err <= tol:
        return [ctrl]
    if err <= tol * 4:
        for _ in range(4):
            u = _reparameterize(pts, u, ctrl)
            ctrl = _generate_bezier(pts, u, t_left, t_right)
            err, split = _max_error(pts, u, ctrl)
            if err <= tol:
                return [ctrl]
    if depth >= 12 or len(pts) <= 4:
        return [ctrl]
    split = max(1, min(len(pts) - 2, split))
    return (
        fit_cubics(pts[: split + 1], tol, depth + 1)
        + fit_cubics(pts[split:], tol, depth + 1)
    )


def polyline_to_segments(pts: np.ndarray, tol: float, line_tol: float,
                         corner_deg: float = 40.0) -> list[dict]:
    """Polyline -> [{"kind": "line"|"cubic", "ctrl": ...}] split at corners."""
    pts = smooth_polyline(np.asarray(pts, dtype=float))
    corners = find_corners(pts, angle_deg=corner_deg)
    cuts = [0] + corners + [len(pts) - 1]
    segments: list[dict] = []
    for a, b in zip(cuts[:-1], cuts[1:]):
        run = pts[a: b + 1]
        if len(run) < 2:
            continue
        if _chord_deviation(run) <= line_tol:
            segments.append({"kind": "line",
                             "ctrl": np.array([run[0], run[-1]])})
        else:
            for ctrl in fit_cubics(run, tol):
                segments.append({"kind": "cubic", "ctrl": ctrl})
    return segments


def segments_to_nodes(segments: list[dict], closed: bool) -> list[dict]:
    """Segments -> editor node model (anchors with optional hIn/hOut)."""
    if not segments:
        return []
    nodes: list[dict] = []

    def add_node(pt, h_in=None, h_out=None):
        node = {"x": float(pt[0]), "y": float(pt[1]), "type": "corner"}
        if h_in is not None:
            node["hIn"] = {"x": float(h_in[0]), "y": float(h_in[1])}
        if h_out is not None:
            node["hOut"] = {"x": float(h_out[0]), "y": float(h_out[1])}
        return node

    first = segments[0]
    h_out = first["ctrl"][1] if first["kind"] == "cubic" else None
    nodes.append(add_node(first["ctrl"][0], h_out=h_out))
    for i, seg in enumerate(segments):
        end = seg["ctrl"][-1]
        h_in = seg["ctrl"][2] if seg["kind"] == "cubic" else None
        nxt = segments[i + 1] if i + 1 < len(segments) else None
        h_out = None
        if nxt is not None and nxt["kind"] == "cubic":
            h_out = nxt["ctrl"][1]
        if nxt is None and closed:
            # closing segment returns to the first node; fold handles there
            if h_in is not None:
                nodes[0]["hIn"] = {"x": float(h_in[0]), "y": float(h_in[1])}
            break
        nodes.append(add_node(end, h_in=h_in, h_out=h_out))

    # mark smooth nodes: collinear-ish in/out handles
    for node in nodes:
        if "hIn" in node and "hOut" in node:
            v1 = np.array([node["x"] - node["hIn"]["x"],
                           node["y"] - node["hIn"]["y"]])
            v2 = np.array([node["hOut"]["x"] - node["x"],
                           node["hOut"]["y"] - node["y"]])
            n1, n2 = np.linalg.norm(v1), np.linalg.norm(v2)
            if n1 > 1e-9 and n2 > 1e-9 and np.dot(v1, v2) / (n1 * n2) > 0.9:
                node["type"] = "smooth"
    return nodes
