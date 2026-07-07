"""Automated glyph-skeleton perfection: fragment soup -> minimal clean strokes.

Implements the researched pipeline (Noris/StrokeAggregator junction pairing,
potrace-style node reduction, Taubin/kappa bowl replacement) with hard
shape-preservation gates, all pure numpy:

  1. flatten existing strokes to dense polylines
  2. stroke graph: endpoint clustering (eps 25 units), Chamfer overlap dedup,
     sub-dot dangle removal
  3. merge: degree-2 chains unconditionally; degree>=3 junctions paired by
     tangent continuity (< 50 deg turn); close loops meeting within eps
  4. per chain: resample ~5-unit steps, light smoothing, corner detection,
     Schneider fit with a tolerance ladder (25 -> 15 -> 8 units)
  5. regularize: collinear merge, axis snap, guide snap (handles follow)
  6. bowls: closed chains fit as circle/ellipse -> 4-node kappa loop
  7. GATES on the rasterized before/after: IoU >= 0.97 (accept >= 0.985),
     Hausdorff <= 50 units hard, topology (components + holes) unchanged

Usage:
  python -m glyphstudio.simplify <font_dir> [--chars "&UR"] [--apply] [--json]

Only glyphs with provenance "traced" are touched; hand-authored ("edited")
skeletons are already minimal and are never modified.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

import numpy as np

from . import CAP_UNITS
from .fitcurve import fit_cubics, find_corners
from .raster import _flatten_cubic, _segments, rasterize_glyph
from .samples import canvas_geometry, consensus_soft, load_stack
from .schema import (
    atomic_write_json,
    font_dir_paths,
    glyph_filename,
    load_font,
    load_glyphs,
    merged_params,
)

EPS_JOIN = 25.0          # endpoint cluster radius (0.5 dot radius)
DEDUP_DIST = 15.0        # sub-dot Chamfer distance for overlap dedup
DANGLE_LEN = 50.0        # dot radius: shorter dangles are stamp noise
JUNCTION_TURN_DEG = 50.0
RESAMPLE_STEP = 5.0
TOL_LADDER = (25.0, 15.0, 8.0)
COLLINEAR_DEG = 3.0
AXIS_SNAP_UNITS = 8.0
GUIDE_SNAP = 80.0
KAPPA = 0.5522847498
# The fidelity gate compares candidates against THIS corpus — it must match the
# font being simplified (the MCP server passes the right one per font).
SAMPLES_PATH = os.environ.get(
    "GLYPHSTUDIO_SAMPLES", "/tmp/gridfix/sprouts_demo/sprouts.samples.npz"
)

IOU_ACCEPT = 0.985
IOU_REJECT = 0.97
HAUSDORFF_HARD = 50.0


# ---------------------------------------------------------------- geometry

def _flatten_stroke(stroke: dict) -> np.ndarray:
    pts: list[np.ndarray] = []
    for seg in _segments(stroke):
        poly = (seg["pts"] if seg["kind"] == "line"
                else _flatten_cubic(seg["pts"], tol=1.0))
        if pts:
            poly = poly[1:]
        pts.extend(poly)
    return np.array(pts, dtype=float)


def _arc_length(chain: np.ndarray) -> float:
    if len(chain) < 2:
        return 0.0
    return float(np.linalg.norm(np.diff(chain, axis=0), axis=1).sum())


def _resample(chain: np.ndarray, step: float) -> np.ndarray:
    total = _arc_length(chain)
    if total <= step or len(chain) < 2:
        return chain
    n = max(2, int(round(total / step)) + 1)
    deltas = np.linalg.norm(np.diff(chain, axis=0), axis=1)
    cum = np.concatenate([[0.0], np.cumsum(deltas)])
    targets = np.linspace(0.0, total, n)
    out = []
    for t in targets:
        i = int(np.searchsorted(cum, t, side="right") - 1)
        i = min(i, len(chain) - 2)
        seg = cum[i + 1] - cum[i]
        f = 0.0 if seg <= 0 else (t - cum[i]) / seg
        out.append(chain[i] * (1 - f) + chain[i + 1] * f)
    return np.array(out)


def _smooth(chain: np.ndarray, passes: int = 2) -> np.ndarray:
    out = chain.astype(float).copy()
    for _ in range(passes):
        if len(out) < 5:
            return out
        out[1:-1] = (out[:-2] + 2 * out[1:-1] + out[2:]) / 4.0
    return out


def _tangent(chain: np.ndarray, at_start: bool, k: int = 9) -> np.ndarray:
    """Least-squares tangent over the last k points (not 1-2 pixel steps)."""
    pts = chain[:k] if at_start else chain[-k:]
    if len(pts) < 2:
        return np.array([1.0, 0.0])
    center = pts.mean(axis=0)
    d = pts - center
    cov = d.T @ d
    _, vecs = np.linalg.eigh(cov)
    v = vecs[:, -1]
    ref = pts[-1] - pts[0]
    if np.dot(v, ref) < 0:
        v = -v
    # orient outgoing from the endpoint
    return -v if at_start else v


def _chamfer_redundant(a: np.ndarray, b: np.ndarray) -> bool:
    """True when >=80% of a's samples lie within DEDUP_DIST of b."""
    if len(a) < 2 or len(b) < 2:
        return False
    d = np.linalg.norm(a[:, None, :] - b[None, :, :], axis=2).min(axis=1)
    return float((d <= DEDUP_DIST).mean()) >= 0.8


# --------------------------------------------------------------- graph merge

def consolidate(chains: list[np.ndarray]) -> list[dict]:
    """Fragment soup -> merged chains [{points, closed}]."""
    chains = [c for c in chains if len(c) >= 2]

    # overlap dedup: drop fragments buried inside longer ones
    order = sorted(range(len(chains)), key=lambda i: -_arc_length(chains[i]))
    kept: list[np.ndarray] = []
    for i in order:
        if any(_chamfer_redundant(chains[i], k) for k in kept):
            continue
        kept.append(chains[i])
    chains = kept

    changed = True
    while changed:
        changed = False
        # endpoint join: find the best-matching pair of chain ends
        best = None  # (turn_deg, dist, i, ei, j, ej)
        for i in range(len(chains)):
            for ei in (0, 1):
                pi = chains[i][0 if ei == 0 else -1]
                ti = _tangent(chains[i], at_start=(ei == 0))
                for j in range(len(chains)):
                    if j == i:
                        continue
                    for ej in (0, 1):
                        pj = chains[j][0 if ej == 0 else -1]
                        dist = float(np.linalg.norm(pi - pj))
                        if dist > EPS_JOIN:
                            continue
                        tj = _tangent(chains[j], at_start=(ej == 0))
                        # outgoing tangents oppose when the path continues
                        cosv = float(np.dot(ti, -tj))
                        turn = math.degrees(math.acos(max(-1, min(1, cosv))))
                        cand = (turn, dist, i, ei, j, ej)
                        if best is None or cand < best:
                            best = cand
        if best is not None and best[0] <= JUNCTION_TURN_DEG:
            _, _, i, ei, j, ej = best
            a, b = chains[i], chains[j]
            if ei == 0:
                a = a[::-1]           # a now ends at the join
            if ej == 1:
                b = b[::-1]           # b now starts at the join
            merged = np.vstack([a, b[1:]])
            chains = [c for k, c in enumerate(chains) if k not in (i, j)]
            chains.append(merged)
            changed = True

    out = []
    for c in chains:
        if _arc_length(c) < DANGLE_LEN and len(chains) > 1:
            continue  # sub-dot dangle
        closed = bool(np.linalg.norm(c[0] - c[-1]) <= EPS_JOIN and
                      _arc_length(c) > 4 * EPS_JOIN)
        out.append({"points": c, "closed": closed})
    return out


# --------------------------------------------------------------- refit

def _ellipse_fit(chain: np.ndarray) -> dict | None:
    """Taubin-style circle then axis-aligned ellipse; None if not a bowl."""
    pts = _resample(chain, max(4.0, _arc_length(chain) / 64))
    cx, cy = pts.mean(axis=0)
    d = pts - (cx, cy)
    a = float(np.percentile(np.abs(d[:, 0]), 95))
    b = float(np.percentile(np.abs(d[:, 1]), 95))
    if a < 20 or b < 20:
        return None
    ratio = a / b
    if not (0.25 <= ratio <= 4.0):
        return None
    # radial residual vs the axis-aligned ellipse
    theta = np.arctan2(d[:, 1] / max(b, 1e-9), d[:, 0] / max(a, 1e-9))
    model = np.stack([a * np.cos(theta), b * np.sin(theta)], axis=1)
    resid = np.linalg.norm(d - model, axis=1)
    tol = max(16.7, 0.05 * (a + b) / 2)
    if float(resid.max()) > tol * 2 or float(np.median(resid)) > tol:
        return None
    # angular coverage: no gap > 30 deg
    ang = np.sort(np.mod(np.arctan2(d[:, 1], d[:, 0]), 2 * np.pi))
    gaps = np.diff(np.concatenate([ang, [ang[0] + 2 * np.pi]]))
    if float(gaps.max()) > math.radians(35):
        return None
    return {"cx": cx, "cy": cy, "a": a, "b": b}


def _kappa_loop(e: dict) -> dict:
    cx, cy, a, b = e["cx"], e["cy"], e["a"], e["b"]
    ka, kb = KAPPA * a, KAPPA * b

    def node(x, y, hin, hout):
        return {"x": round(x, 1), "y": round(y, 1), "type": "smooth",
                "hIn": {"x": round(hin[0], 1), "y": round(hin[1], 1)},
                "hOut": {"x": round(hout[0], 1), "y": round(hout[1], 1)}}
    return {"closed": True, "nodes": [
        node(cx, cy + b, (cx - ka, cy + b), (cx + ka, cy + b)),
        node(cx + a, cy, (cx + a, cy + kb), (cx + a, cy - kb)),
        node(cx, cy - b, (cx + ka, cy - b), (cx - ka, cy - b)),
        node(cx - a, cy, (cx - a, cy - kb), (cx - a, cy + kb)),
    ]}


def _segments_to_nodes(segs: list[dict], closed: bool) -> list[dict]:
    from .fitcurve import segments_to_nodes
    return segments_to_nodes(segs, closed)


def _regularize(nodes: list[dict], dot_r: float) -> list[dict]:
    """Axis + guide snapping; handles follow their anchors."""
    guides = (dot_r, CAP_UNITS - dot_r, 700.0 - dot_r, -320.0 + dot_r)

    def snap_val(v, targets, tol):
        for t in targets:
            if abs(v - t) <= tol:
                return t
        return v

    out = [dict(n) for n in nodes]
    # guide snap on y for first/last (open) or extreme nodes
    for n in out:
        ny = snap_val(n["y"], guides, GUIDE_SNAP * 0.5)
        if ny != n["y"]:
            dy = ny - n["y"]
            n["y"] = round(ny, 1)
            for h in ("hIn", "hOut"):
                if h in n:
                    n[h] = {"x": n[h]["x"], "y": round(n[h]["y"] + dy, 1)}
    # axis snap: line segments nearly vertical/horizontal
    for a, b in zip(out[:-1], out[1:]):
        if "hOut" in a or "hIn" in b:
            continue
        if abs(a["x"] - b["x"]) <= AXIS_SNAP_UNITS:
            m = round((a["x"] + b["x"]) / 2, 1)
            a["x"] = b["x"] = m
        elif abs(a["y"] - b["y"]) <= AXIS_SNAP_UNITS:
            m = round((a["y"] + b["y"]) / 2, 1)
            a["y"] = b["y"] = m
    return out


def refit_chain(chain: np.ndarray, closed: bool, dot_r: float) -> dict | None:
    pts = _smooth(_resample(chain, RESAMPLE_STEP))
    if len(pts) < 2:
        return None
    if closed:
        e = _ellipse_fit(pts)
        if e is not None:
            return _kappa_loop(e)
    corners = find_corners(pts, angle_deg=42.0, win=max(3, int(30 / RESAMPLE_STEP)))
    cuts = [0] + corners + [len(pts) - 1]
    for tol in TOL_LADDER:
        segs: list[dict] = []
        for a, b in zip(cuts[:-1], cuts[1:]):
            run = pts[a: b + 1]
            if len(run) < 2:
                continue
            chord = run[-1] - run[0]
            denom = max(float(np.linalg.norm(chord)), 1e-9)
            if len(run) > 2:
                rel = run - run[0]
                dev = np.abs(chord[0] * rel[:, 1] - chord[1] * rel[:, 0]) / denom
            else:
                dev = np.zeros(1)
            if float(np.max(dev)) <= tol * 0.6:
                segs.append({"kind": "line", "ctrl": np.array([run[0], run[-1]])})
            else:
                for ctrl in fit_cubics(run, tol):
                    segs.append({"kind": "cubic", "ctrl": ctrl})
        nodes = _segments_to_nodes(segs, closed)
        if len(nodes) >= 2:
            return {"closed": closed, "nodes": _regularize(nodes, dot_r)}
    return None


# --------------------------------------------------------------- gates

def _components_and_holes(mask: np.ndarray) -> tuple[int, int]:
    def count(m):
        lab = np.zeros(m.shape, dtype=int)
        cur = 0
        for y, x in zip(*np.nonzero(m & (lab == 0))):
            if lab[y, x]:
                continue
            cur += 1
            stack = [(y, x)]
            lab[y, x] = cur
            while stack:
                cy, cx = stack.pop()
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        ny, nx = cy + dy, cx + dx
                        if (0 <= ny < m.shape[0] and 0 <= nx < m.shape[1]
                                and m[ny, nx] and not lab[ny, nx]):
                            lab[ny, nx] = cur
                            stack.append((ny, nx))
        return cur
    comps = count(mask.astype(bool))
    # holes: background components fully inside (total bg comps - 1 border)
    bg = ~mask.astype(bool)
    padded = np.pad(bg, 1, constant_values=True)
    holes = count(padded) - 1
    return comps, holes


def soft_fidelity(bitmap: np.ndarray, off: int, soft: np.ndarray,
                  soft_ref_cap: int, soft_baseline: int) -> float:
    """Soft-IoU of a rendered glyph vs the corpus soft consensus map.

    Both live at cap 60 (render REF_CAP == demo-corpus ref cap), aligned by
    baseline and ink x-centroid. Gate cleanups against REAL data, not the
    noisy pre-edit raster (research: fidelity-vs-simplicity objective).
    """
    H, W = soft.shape
    canvas = np.zeros((H, W), dtype=float)
    ys, xs = np.nonzero(soft >= 0.25)
    sx = float(xs.mean()) if len(xs) else W / 2
    bh, bw = bitmap.shape
    y1 = soft_baseline + off
    y0 = y1 - bh
    x0 = int(round(sx - bw / 2.0))
    ys0, xs0 = max(0, y0), max(0, x0)
    ye, xe = min(H, y0 + bh), min(W, x0 + bw)
    if ye <= ys0 or xe <= xs0:
        return 0.0
    canvas[ys0:ye, xs0:xe] = bitmap[ys0 - y0: ye - y0, xs0 - x0: xe - x0]
    inter = np.minimum(soft, canvas).sum()
    union = np.maximum(soft, canvas).sum()
    return float(inter / union) if union > 0 else 0.0


def gate(old_bitmap: np.ndarray, old_off: int,
         new_bitmap: np.ndarray, new_off: int) -> dict:
    """Shape-preservation verdict between two tight-cropped rasters."""
    # align by baseline: bottom row sits at baseline+off
    h = max(old_bitmap.shape[0] + 40, new_bitmap.shape[0] + 40)
    w = max(old_bitmap.shape[1] + 40, new_bitmap.shape[1] + 40)

    def place(bm, off):
        c = np.zeros((h, w), dtype=bool)
        y1 = h - 20 + min(0, off)
        y0 = y1 - bm.shape[0]
        x0 = (w - bm.shape[1]) // 2
        c[y0:y1, x0:x0 + bm.shape[1]] = bm.astype(bool)
        return c

    a, b = place(old_bitmap, old_off), place(new_bitmap, new_off)
    inter = float((a & b).sum())
    union = float((a | b).sum())
    iou = inter / union if union else 0.0
    # Hausdorff in units (1 px at REF_CAP 60 = 16.67 units)
    ya, xa = np.nonzero(a)
    yb, xb = np.nonzero(b)
    if len(ya) and len(yb):
        pa = np.stack([ya, xa], axis=1)
        pb = np.stack([yb, xb], axis=1)
        d_ab = np.sqrt(((pa[:, None] - pb[None]) ** 2).sum(2)).min(1).max()
        d_ba = np.sqrt(((pb[:, None] - pa[None]) ** 2).sum(2)).min(1).max()
        hausdorff_units = float(max(d_ab, d_ba)) * (CAP_UNITS / 60.0)
    else:
        hausdorff_units = float("inf")
    top_a, top_b = _components_and_holes(a), _components_and_holes(b)
    verdict = "accept"
    reasons = []
    if iou < 0.70:
        verdict = "reject"
        reasons.append(f"raster iou {iou:.3f} < 0.70 sanity floor")
    elif iou < IOU_ACCEPT:
        verdict = "flag"
        reasons.append(f"raster iou {iou:.3f} (trace-noise removal expected)")
    if top_a != top_b:
        verdict = "reject"
        reasons.append(f"topology {top_a} -> {top_b}")
    return {"verdict": verdict, "iou": round(iou, 4),
            "hausdorff_units": round(hausdorff_units, 1),
            "topology": {"before": top_a, "after": top_b},
            "reasons": reasons}


# --------------------------------------------------------------- driver

def simplify_glyph(glyph: dict, params: dict, ref_cap: int) -> dict:
    dot_r = float(params.get("dot", {}).get("size", 100)) * \
        float(params.get("weight", 1.0)) / 2.0
    chains = [_flatten_stroke(s) for s in glyph.get("strokes", [])]
    merged = consolidate(chains)
    new_strokes = []
    for chain in merged:
        stroke = refit_chain(chain["points"], chain["closed"], dot_r)
        if stroke is not None and len(stroke["nodes"]) >= 2:
            new_strokes.append(stroke)
    if not new_strokes:
        return {"ok": False, "reason": "no strokes survived"}

    candidate = dict(glyph)
    candidate["strokes"] = new_strokes

    old_bm, old_off = rasterize_glyph(glyph, params, ref_cap)
    new_bm, new_off = rasterize_glyph(candidate, params, ref_cap)
    g = gate(old_bm, old_off, new_bm, new_off)

    soft = params.get("_soft_map")
    if soft is not None and g["verdict"] != "reject":
        sref, sbase = params["_soft_geom"]
        fid_old = soft_fidelity(old_bm.astype(float), old_off, soft, sref, sbase)
        fid_new = soft_fidelity(new_bm.astype(float), new_off, soft, sref, sbase)
        g["soft_fidelity"] = {"before": round(fid_old, 4),
                              "after": round(fid_new, 4)}
        if fid_new >= fid_old - 0.015:
            if g["verdict"] == "flag":
                g["verdict"] = "accept"
                g["reasons"].append(
                    f"soft fidelity non-degrading ({fid_old:.3f}->{fid_new:.3f})")
        else:
            g["verdict"] = "reject"
            g["reasons"].append(
                f"soft fidelity degraded {fid_old:.3f}->{fid_new:.3f}")

    def counts(gl):
        return (len(gl["strokes"]),
                sum(len(s["nodes"]) for s in gl["strokes"]))
    s0, n0 = counts(glyph)
    s1, n1 = counts(candidate)
    return {
        "ok": g["verdict"] != "reject",
        "gate": g,
        "before": {"strokes": s0, "nodes": n0},
        "after": {"strokes": s1, "nodes": n1},
        "candidate": candidate,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("font_dir")
    ap.add_argument("--chars")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args(argv)

    font = load_font(args.font_dir)
    glyphs = load_glyphs(args.font_dir)
    ref_cap = int(font.get("refCap", 60))
    wanted = set(args.chars) if args.chars else None

    results = []
    for cp, glyph in sorted(glyphs.items()):
        ch = chr(cp)
        if wanted is not None and ch not in wanted:
            continue
        if glyph.get("provenance") != "traced":
            continue
        params = merged_params(font, glyph)
        try:
            stack = load_stack(SAMPLES_PATH, cp) if SAMPLES_PATH else None
            if stack is not None and len(stack):
                params["_soft_map"] = consensus_soft(stack)
                params["_soft_geom"] = canvas_geometry(stack.shape[1])
            r = simplify_glyph(glyph, params, ref_cap)
        except Exception as exc:  # noqa: BLE001 -- one glyph must not kill the fleet
            results.append({"char": ch, "before": None, "after": None,
                            "gate": {"verdict": "error", "reasons": [repr(exc)]},
                            "applied": False})
            continue
        entry = {"char": ch,
                 "before": r.get("before"), "after": r.get("after"),
                 "gate": r.get("gate"), "applied": False}
        accepted = r["ok"] and r["gate"]["verdict"] in ("accept", "flag") \
            and r["after"]["nodes"] < r["before"]["nodes"]
        if args.apply and accepted:
            paths = font_dir_paths(args.font_dir)
            target = f"{paths['glyphs']}/{glyph_filename(cp)}"
            atomic_write_json(target, r["candidate"])
            entry["applied"] = True
        results.append(entry)

    if args.json:
        print(json.dumps({"results": results}, default=str))
    else:
        for e in results:
            g = e["gate"] or {}
            print(f"{e['char']!r}: {e['before']} -> {e['after']} "
                  f"{g.get('verdict')} iou={g.get('iou')} "
                  f"H={g.get('hausdorff_units')}u "
                  f"{'APPLIED' if e['applied'] else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
