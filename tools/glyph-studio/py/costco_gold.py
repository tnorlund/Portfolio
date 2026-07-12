#!/usr/bin/env python3
"""costco_gold.py -- the standing "how close to 1:1 are we" instrument.

ONE command that takes a render + the Costco gold reference set and emits the
full L0-L4 JSON scorecard (current values, 1:1 targets, pass/fail vs
regression thresholds) defined by ``GOLD_STANDARD.md`` Part 3. It wires the
existing machinery rather than reinventing it:

- GlyphScore (worktree ``/private/tmp/glyphscore``, PR #1111) supplies L1
  per-glyph fidelity and the render segmentation used by L0/L2. Its path is
  configurable via ``--glyphscore-root`` / ``$GLYPHSCORE_ROOT``.
- ``glyphstudio.gold_metrics`` supplies the analytic L0/L2/L3/L4 metrics
  (chamfer, boundary-IoU, Kanungo two-sample texture test, MS-SSIM, OCR
  error-set Jaccard, alignment warp).
- Apple Vision (``ocr_vision.swift``, compiled on demand) supplies the OCR
  used for alignment and the L4 OCR-parity metrics.

Metrics that need a model we do not have (forgery-detector AUC, TSTR gap) are
emitted as explicit ``NOT_IMPLEMENTED`` entries -- never a fabricated number.

Pass/fail is vs the 1:1 target bands in ``--config``. Pass ``--baseline`` with a
prior scorecard to additionally run the CI regression gate (no metric may
worsen by more than its ``reg`` threshold-width); the run exits 2 on regression.

Usage:
    costco_gold.py --render final.webp --real real.webp \\
        --labels final.labels.json --refpack costco.refpack.npz \\
        --chart bitMatrix-C2-chart.glyphs.npz --config costco.thresholds.json \\
        --out costco_gold.scorecard.json

Read-only vs dev/prod; deterministic; merchant-parameterized (Costco is the
reference config).
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
from collections import defaultdict

import numpy as np
from PIL import Image

_HERE = os.path.dirname(os.path.abspath(__file__))

NOT_IMPLEMENTED = "NOT_IMPLEMENTED"

# gold_metrics is loaded standalone (by file path) so that the ``glyphstudio``
# package name can resolve entirely to the glyphscore worktree (whose
# family_cluster / typography / glyph_score are mutually consistent). Importing
# our repo's glyphstudio would shadow it and hide glyph_score.py.
gm = None


def _load_gold_metrics():
    global gm
    if gm is not None:
        return gm
    path = os.path.join(_HERE, "glyphstudio", "gold_metrics.py")
    spec = importlib.util.spec_from_file_location("gold_metrics", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    gm = mod
    return gm


def _log(msg: str) -> None:
    print(f"[costco_gold {time.strftime('%H:%M:%S')}] {msg}", file=sys.stderr, flush=True)


def _repo_rel(path: str) -> str:
    """Path relative to the enclosing git repo root (portable in the committed
    record), or the absolute path if not under a repo."""
    ap = os.path.abspath(path)
    d = os.path.dirname(ap)
    while d and d != os.path.dirname(d):
        if os.path.isdir(os.path.join(d, ".git")) or os.path.isfile(os.path.join(d, ".git")):
            return os.path.relpath(ap, d)
        d = os.path.dirname(d)
    return ap


def _sanitize(obj):
    """Recursively replace non-finite floats with None so the scorecard is
    strict-JSON valid (json.dumps allow_nan=False)."""
    if isinstance(obj, dict):
        return {k: _sanitize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_sanitize(v) for v in obj]
    if isinstance(obj, float) and not np.isfinite(obj):
        return None
    if isinstance(obj, (np.floating,)):
        f = float(obj)
        return f if np.isfinite(f) else None
    if isinstance(obj, (np.integer,)):
        return int(obj)
    return obj


# --- GlyphScore machinery (imported from the glyphscore worktree) --------------


def _import_glyphscore(root: str):
    """Wire GlyphScore's CLI + scoring primitives from its worktree.

    PR #1111 is not on origin/main, so -- like the gap analysts'
    ``decompose.py`` -- we add the worktree to ``sys.path`` instead of
    duplicating not-yet-merged code.
    """
    for p in (os.path.join(root, "tools/glyph-studio/py"),
              os.path.join(root, "synthesis_loop")):
        if os.path.isdir(p) and p not in sys.path:
            sys.path.insert(0, p)
    import glyph_score_cli as gsc  # noqa: E402
    from glyphstudio.family_cluster import normalize_glyph  # noqa: E402
    from glyphstudio.glyph_score import (  # noqa: E402
        MIN_REF, MAX_REF, _cap_stack, crop_exemplar, load_refpack,
        self_similarity, shifted_iou_stack,
    )
    return {
        "gsc": gsc, "normalize_glyph": normalize_glyph, "MIN_REF": MIN_REF,
        "MAX_REF": MAX_REF, "_cap_stack": _cap_stack,
        "crop_exemplar": crop_exemplar, "load_refpack": load_refpack,
        "self_similarity": self_similarity, "shifted_iou_stack": shifted_iou_stack,
    }


# --- OCR (Apple Vision via a compiled swift helper) ----------------------------


def _ensure_ocr_binary(cache_dir: str):
    """Compile ``ocr_vision.swift`` once into ``cache_dir``; return path or None.

    Returns None (OCR-dependent metrics become NOT_IMPLEMENTED) when swiftc or
    the Vision framework is unavailable, rather than faking OCR output.
    """
    src = os.path.join(_HERE, "ocr_vision.swift")
    if not os.path.exists(src):
        return None
    os.makedirs(cache_dir, exist_ok=True)
    binp = os.path.join(cache_dir, "ocr_vision")
    if os.path.exists(binp) and os.path.getmtime(binp) >= os.path.getmtime(src):
        return binp
    swiftc = subprocess.run(["which", "swiftc"], capture_output=True, text=True)
    if swiftc.returncode != 0:
        _log("swiftc not found -> OCR metrics NOT_IMPLEMENTED")
        return None
    r = subprocess.run(["swiftc", "-O", src, "-o", binp],
                       capture_output=True, text=True)
    if r.returncode != 0:
        _log(f"swiftc failed -> OCR NOT_IMPLEMENTED: {r.stderr[:200]}")
        return None
    return binp


def _run_ocr(binp: str, img: Image.Image, cache_dir: str, tag: str):
    """Run Vision OCR on a PIL image; return list of {text,x,y,w,h} lines."""
    png = os.path.join(cache_dir, f"_ocr_{tag}.png")
    img.convert("RGB").save(png)
    r = subprocess.run([binp, png], capture_output=True, text=True)
    if r.returncode != 0:
        _log(f"ocr failed for {tag}: {r.stderr[:200]}")
        return None
    try:
        return json.loads(r.stdout)["lines"]
    except Exception as e:  # noqa: BLE001
        _log(f"ocr parse failed for {tag}: {e}")
        return None


# --- gate evaluation -----------------------------------------------------------


def _gate(value, spec: dict) -> dict:
    """Evaluate one metric against its threshold spec.

    spec: {target, tol, dir}  where dir in {band, min, max, p_min}. Returns a
    dict with value, target, and pass (None when value is nan/None/not-live).
    """
    out = {"value": value, "target": spec.get("target"), "tol": spec.get("tol"),
           "dir": spec.get("dir")}
    if value is None or (isinstance(value, float) and not np.isfinite(value)):
        out["pass"] = None
        return out
    t = spec.get("target")
    tol = spec.get("tol", 0.0)
    d = spec.get("dir", "band")
    if d == "band":
        out["pass"] = bool(abs(value - t) <= tol)
    elif d == "min":
        out["pass"] = bool(value >= t - tol)
    elif d == "max":
        out["pass"] = bool(value <= t + tol)
    elif d == "p_min":  # fail-to-reject: p-value >= target
        out["pass"] = bool(value >= t)
    else:
        out["pass"] = None
    return out


def _level_pass(gates: dict):
    """A level passes only when EVERY required gate passes. A required gate
    that could not be evaluated (value nan/None -- e.g. OCR unavailable)
    yields ``None`` (INCOMPLETE), never a silent pass on the survivors.

    Only required gates are passed in here; intentionally-unsupported metrics
    (forgery AUC, TSTR) are trained-model stubs kept OUT of the gate set.
    """
    passes = [g.get("pass") for g in gates.values()
              if isinstance(g, dict) and "pass" in g]
    if not passes:
        return None
    if any(p is None for p in passes):
        return None  # required metric unavailable -> level incomplete
    return all(passes)


# --- L1 IoU decomposition (render vs design/real, with I3 hygiene) -------------


def _iou_decomposition(GS, render_path, labels_path, refpack_path, chart_path,
                       exclude_lines):
    """Usage-weighted IoU(render->design)=rc, IoU(render->real)=re, and
    IoU(design->real)=ce, computed on the SAME normalized masks GlyphScore
    uses -- both with and without the I3 metric-hygiene line mask.

    Mirrors the gap analysts' ``decompose.py`` so the harness reproduces the
    published rc/re/ce numbers.
    """
    gsc = GS["gsc"]
    normalize_glyph = GS["normalize_glyph"]
    shifted_iou_stack = GS["shifted_iou_stack"]
    crop_exemplar = GS["crop_exemplar"]
    _cap_stack = GS["_cap_stack"]
    MIN_REF, MAX_REF = GS["MIN_REF"], GS["MAX_REF"]

    pack = GS["load_refpack"](refpack_path)
    atlas = gsc._load_atlas(chart_path)
    chart = {c: normalize_glyph(g) for c, g in atlas.items()}

    words = gsc._words_from_json(labels_path)
    instances, seg = gsc.segment_render(render_path, words)

    def line_idx(key):  # "line043" -> 43
        try:
            return int(str(key).replace("line", ""))
        except ValueError:
            return -1

    r_by_char = defaultdict(list)
    for ch, m, line in instances:
        r_by_char[ch].append((np.asarray(m, bool), line_idx(line)))

    def aggregate(drop_lines):
        rows = []
        for ch, stack in pack.items():
            st = np.asarray(stack, bool)
            if st.shape[0] < MIN_REF:
                continue
            ex = crop_exemplar(_cap_stack(st, MAX_REF))
            g = chart.get(ch)
            ce = (float(shifted_iou_stack(g, ex[None], 2)[0])
                  if g is not None else float("nan"))
            rins = [(m, li) for (m, li) in r_by_char.get(ch, [])
                    if li not in drop_lines]
            if not rins:
                continue
            re_vals, rc_vals = [], []
            for m, _li in rins:
                re_vals.append(float(shifted_iou_stack(m, ex[None], 2)[0]))
                if g is not None:
                    rc_vals.append(float(shifted_iou_stack(m, g[None], 2)[0]))
            rows.append({
                "char": ch, "n_real": int(st.shape[0]),
                "re": float(np.median(re_vals)),
                "rc": float(np.median(rc_vals)) if rc_vals else float("nan"),
                "ce": ce,
            })
        return rows

    def wmean(rows, key):
        num = den = 0.0
        for r in rows:
            v = r[key]
            if v != v:
                continue
            num += r["n_real"] * v
            den += r["n_real"]
        return num / den if den else float("nan")

    rows_raw = aggregate(set())
    rows_hyg = aggregate(set(exclude_lines))
    worst = sorted(
        [r for r in rows_hyg if r["rc"] == r["rc"]],
        key=lambda r: r["rc"])[:8]
    return {
        "seg": seg,
        "raw": {"iou_rc": wmean(rows_raw, "rc"), "iou_re": wmean(rows_raw, "re"),
                "iou_ce": wmean(rows_raw, "ce")},
        "hygiene": {"iou_rc": wmean(rows_hyg, "rc"), "iou_re": wmean(rows_hyg, "re"),
                    "iou_ce": wmean(rows_hyg, "ce")},
        "worst_after_hygiene": [
            {"char": r["char"], "rc": round(r["rc"], 3), "ce": round(r["ce"], 3),
             "n": r["n_real"]} for r in worst],
    }


# --- L2 chamfer / boundary-IoU per glyph (clean glyphs) ------------------------


def _l2_shape_metrics(GS, render_path, labels_path, refpack_path, exclude_lines):
    """Usage-weighted chamfer (dots on the 32-grid) + boundary-IoU of render
    glyphs vs the real median-vote exemplar, hygiene applied."""
    gsc = GS["gsc"]
    crop_exemplar = GS["crop_exemplar"]
    _cap_stack = GS["_cap_stack"]
    MIN_REF, MAX_REF = GS["MIN_REF"], GS["MAX_REF"]
    pack = GS["load_refpack"](refpack_path)
    words = gsc._words_from_json(labels_path)
    instances, _seg = gsc.segment_render(render_path, words)

    exemplars = {}
    counts = {}
    for ch, stack in pack.items():
        st = np.asarray(stack, bool)
        if st.shape[0] < MIN_REF:
            continue
        exemplars[ch] = crop_exemplar(_cap_stack(st, MAX_REF))
        counts[ch] = int(st.shape[0])

    def line_idx(key):
        try:
            return int(str(key).replace("line", ""))
        except ValueError:
            return -1

    ch_chamfer = defaultdict(list)
    ch_biou = defaultdict(list)
    for ch, m, line in instances:
        if line_idx(line) in exclude_lines or ch not in exemplars:
            continue
        ex = exemplars[ch]
        ch_chamfer[ch].append(gm.chamfer_distance(m, ex))
        ch_biou[ch].append(gm.boundary_iou(m, ex, band=2))

    def wmed(perchar):
        num = den = 0.0
        for ch, vals in perchar.items():
            v = np.nanmedian(vals)
            if not np.isfinite(v):
                continue
            num += counts[ch] * v
            den += counts[ch]
        return num / den if den else float("nan")

    return {"chamfer_dots": wmed(ch_chamfer), "boundary_iou": wmed(ch_biou)}


# --- geometry from OCR/labels (advance, price column, caption) -----------------


def _render_word_boxes(labels_path, W, H):
    """(width_px, text, x_right_px, y_center_px) for each render label token."""
    with open(labels_path) as fh:
        d = json.load(fh)
    out = []
    for t, b in zip(d["tokens"], d["bboxes"]):
        x0, y0, x1, y1 = (float(v) for v in b[:4])
        left = min(x0, x1) / 1000 * W
        right = max(x0, x1) / 1000 * W
        top = (1 - max(y0, y1) / 1000) * H
        bottom = (1 - min(y0, y1) / 1000) * H
        out.append((right - left, t, right, (top + bottom) / 2, bottom - top))
    return out


import re as _re

PRICE_RE = _re.compile(r"^\d+\.\d\d-?$")


def _clean(s):
    return _re.sub(r"[^A-Za-z0-9]", "", s).upper()


def _price_column_std_px(render_gray, render_boxes, dark_thresh):
    """Std of rendered price-token right ink edges (column raggedness).

    Measures the ACTUAL rightmost ink column per price token in the render
    pixels (not the intended label box), reproducing the pixel raggedness the
    gap report saw (label boxes are grid-perfect and hide it)."""
    H, W = render_gray.shape
    rights = []
    for w, t, xr, yc, h in render_boxes:
        if not PRICE_RE.match(t.strip()):
            continue
        y_lo = max(0, int(yc - h / 2) - 2)
        y_hi = min(H, int(yc + h / 2) + 2)
        # search a window around the intended right edge
        x_lo = max(0, int(xr - w) - 4)
        x_hi = min(W, int(xr) + 8)
        band = render_gray[y_lo:y_hi, x_lo:x_hi]
        ink_cols = np.where((band < dark_thresh).any(axis=0))[0]
        if ink_cols.size:
            rights.append(x_lo + int(ink_cols[-1]))
    return gm.right_edge_std(rights)


def _caption_ratio(render_boxes, real_lines, body):
    """Barcode-caption height ratio: render footer numeric caption height vs
    the real OCR footer numeric caption height (both below the body)."""
    y1 = body[1]

    def render_caption_h():
        num = [(yc, h) for (_w, t, _xr, yc, h) in render_boxes
               if t.strip().isdigit() and len(t.strip()) >= 2 and yc > y1]
        if not num:
            return float("nan")
        num.sort()
        return float(np.median([h for _yc, h in num[-4:]]))

    def real_caption_h():
        num = [(l["y"] + l["h"] / 2, l["h"]) for l in real_lines
               if _clean(l["text"]).isdigit() and len(_clean(l["text"])) >= 2
               and (l["y"] + l["h"] / 2) > y1]
        if not num:
            return float("nan")
        num.sort()
        return float(np.median([h for _yc, h in num[-4:]]))

    return gm.caption_height_ratio(render_caption_h(), real_caption_h())


# --- L3 texture (real vs render on aligned body region) ------------------------


def _ink_mask(gray_region, dark_thresh):
    return np.asarray(gray_region, np.float64) < dark_thresh


def _l3_texture(real_gray, render_warp, body, dark_thresh, GS, refpack_path,
                render_path, labels_path, exclude_lines):
    y0, y1 = body
    r_body = real_gray[y0:y1]
    f_body = render_warp[y0:y1]
    r_ink = _ink_mask(r_body, dark_thresh)
    f_ink = _ink_mask(f_body, dark_thresh)
    r_stats = gm.stroke_core_stats(r_body, r_ink, erode=1)
    f_stats = gm.stroke_core_stats(f_body, f_ink, erode=1)
    # edge transition fraction near ink (dilate ink to capture the ramp)
    from scipy import ndimage
    r_zone = ndimage.binary_dilation(r_ink, iterations=2)
    f_zone = ndimage.binary_dilation(f_ink, iterations=2)
    out = {
        "real_stroke_core_L": r_stats["median_L"],
        "render_stroke_core_L": f_stats["median_L"],
        "real_stroke_cov": r_stats["cov"],
        "render_stroke_cov": f_stats["cov"],
        "real_edge_transition_frac": gm.edge_transition_frac(r_body, r_zone),
        "render_edge_transition_frac": gm.edge_transition_frac(f_body, f_zone),
        "real_paper_clip_frac": gm.paper_clip_frac(r_body),
        "render_paper_clip_frac": gm.paper_clip_frac(f_body),
        "ink_tone_emd": gm.tone_emd(r_body, f_body),
    }
    # Kanungo two-sample: render glyph crops vs real refpack crops (binary).
    # Stratified & character-MATCHED so the test measures texture, not glyph
    # composition: for each char present on both sides (outside the hygiene
    # mask) take an equal number of render and real crops, then pool.
    pack = GS["load_refpack"](refpack_path)
    gsc = GS["gsc"]
    words = gsc._words_from_json(labels_path)
    instances, _ = gsc.segment_render(render_path, words)

    def line_idx(key):
        try:
            return int(str(key).replace("line", ""))
        except ValueError:
            return -1

    render_by_char = defaultdict(list)
    for ch, m, line in instances:
        if line_idx(line) in exclude_lines:
            continue
        render_by_char[ch].append(np.asarray(m, bool))
    per_char_cap = 8  # bound total crops; equal counts per char both sides
    render_crops, real_crops = [], []
    for ch, r_list in render_by_char.items():
        st = pack.get(ch)
        if st is None:
            continue
        st = np.asarray(st, bool)
        k = min(per_char_cap, len(r_list), st.shape[0])
        if k < 1:
            continue
        idx = np.linspace(0, st.shape[0] - 1, k).round().astype(int)
        render_crops.extend(r_list[:k])
        real_crops.extend(st[i] for i in np.unique(idx))
    kg = gm.kanungo_two_sample(render_crops, real_crops, n_perm=100)
    out["kanungo_distance"] = kg["distance"]
    out["kanungo_p"] = kg["p_value"]
    out["kanungo_n"] = {"render": len(render_crops), "real": len(real_crops)}
    return out


# --- main ----------------------------------------------------------------------


def build_scorecard(args) -> dict:
    t_start = time.time()
    GS = _import_glyphscore(args.glyphscore_root)
    _load_gold_metrics()
    cfg = json.load(open(args.config))
    regions = cfg["regions"]
    exclude_lines = set(cfg.get("hygiene", {}).get("exclude_lines", []))

    real_img = Image.open(args.real)
    render_img = Image.open(args.render)
    real_gray = np.asarray(real_img.convert("L"), np.float64)
    render_gray = np.asarray(render_img.convert("L"), np.float64)
    if real_gray.shape != render_gray.shape:
        raise SystemExit(
            f"render {render_gray.shape[::-1]} and real {real_gray.shape[::-1]} "
            "must share one canvas (the pipeline composites both at the same "
            "size); resize before scoring."
        )
    H, W = real_gray.shape
    body = (int(regions["body"][0] * H), int(regions["body"][1] * H))
    dark_thresh = cfg.get("dark_thresh", 160.0)

    scorecard = {
        "merchant": cfg.get("merchant", "costco"),
        "date": time.strftime("%Y-%m-%d"),
        "tool": "costco_gold.py",
        "render": _repo_rel(args.render),
        "real": _repo_rel(args.real),
        "canvas": [W, H],
        "glyphscore_root": args.glyphscore_root,
    }

    # --- Alignment (OCR-anchored piecewise-linear warp) ---
    _log("stage: OCR + alignment")
    ocr_bin = _ensure_ocr_binary(args.cache_dir)
    real_lines = render_lines = None
    render_warp = render_gray
    align_resid = None
    if ocr_bin:
        real_lines = _run_ocr(ocr_bin, real_img, args.cache_dir, "real")
        render_lines = _run_ocr(ocr_bin, render_img, args.cache_dir, "render")
    if real_lines and render_lines:
        anchors = gm.ocr_anchors(real_lines, render_lines)
        render_warp, align_resid = gm.piecewise_row_warp(render_gray, anchors, H)
        scorecard["aligned"] = bool(align_resid is not None and align_resid <= 30)
        scorecard["align_residual_px"] = round(align_resid, 1)
        scorecard["n_anchors"] = len(anchors)
    else:
        scorecard["aligned"] = False
        scorecard["align_residual_px"] = None
        scorecard["align_note"] = "OCR unavailable; L2+ geometry raw-only, MS-SSIM raw"

    # render token geometry (labels) + clustered rows, used by L0 and L2
    render_boxes = _render_word_boxes(args.labels, W, H)

    # --- L0 ---
    _log("stage: L0 bulk gates")
    gsc = GS["gsc"]
    words = gsc._words_from_json(args.labels)
    instances, seg = gsc.segment_render(args.render, words)
    render_fills = [np.asarray(m, bool) for _c, m, _l in instances]
    real_pack = GS["load_refpack"](args.refpack)
    real_crop_masks = [st_i for st in real_pack.values()
                       for st_i in np.asarray(st, bool)]
    ink_r = gm.ink_ratio(real_gray, render_gray)
    fill_render = gm.normalized_fill(render_fills)
    fill_real = gm.normalized_fill(real_crop_masks)
    # Vertical geometry from the body row-ink profile (pixel autocorrelation +
    # duty cycle), measured identically on real and render so the ratio is
    # method-fair and robust to the render's crammed leading (band-splitting
    # merges adjacent crammed lines; autocorrelation reads the true period).
    pitch_render = gm.line_pitch_autocorr(render_gray, *body)
    pitch_real = gm.line_pitch_autocorr(real_gray, *body)
    duty_render = gm.interline_duty(render_gray, *body)
    duty_real = gm.interline_duty(real_gray, *body)
    cap_render = duty_render * pitch_render if np.isfinite(pitch_render) else float("nan")
    cap_real = duty_real * pitch_real if np.isfinite(pitch_real) else float("nan")
    cap_ratio = cap_render / cap_real if cap_real and np.isfinite(cap_real) else float("nan")
    pitch_ratio = pitch_render / pitch_real if pitch_real and np.isfinite(pitch_real) else float("nan")
    # Fill is gated DYNAMICALLY against the refpack-measured real fill (same
    # 32x32-normalized-mask method as the render side). The documented Part-1
    # value (0.271, a different hand-crop method) rides along as spec_target;
    # gating on it would fail the real reference itself (measures ~0.254).
    fill_gate = _gate(fill_render, {"target": fill_real,
                                    "tol": cfg["L0"]["fill"]["tol"],
                                    "dir": "band"})
    fill_gate["real_measured"] = fill_real
    fill_gate["spec_target"] = cfg["L0"]["fill"].get("target")
    L0 = {
        "ink_ratio": _gate(ink_r, cfg["L0"]["ink_ratio"]),
        "fill_render": fill_gate,
        "fill_real_ref": fill_real,
        "cap_h_ratio": _gate(cap_ratio, cfg["L0"]["cap_h_ratio"]),
        "pitch_ratio": _gate(pitch_ratio, cfg["L0"]["pitch_ratio"]),
        "cap_h_px": {"render": cap_render, "real": cap_real},
        "pitch_px": {"render": pitch_render, "real": pitch_real},
        "interline_fill": {"render": duty_render, "real": duty_real},
    }
    L0["pass"] = _level_pass({k: v for k, v in L0.items()
                              if k in ("ink_ratio", "fill_render", "cap_h_ratio", "pitch_ratio")})

    # --- L1 GlyphScore ---
    _log("stage: L1 GlyphScore (self + anchor) + IoU decomposition")
    d_crops = gsc.score_render_report(args.render, words, args.refpack)
    d_chart = gsc.score_render_report(args.render, words, args.refpack, anchor_path=args.chart)
    iou = _iou_decomposition(GS, args.render, args.labels, args.refpack, args.chart,
                             exclude_lines)
    L1 = {
        "gs_vs_crops": _gate(d_crops["glyphscore"], cfg["L1"]["gs_vs_crops"]),
        "gs_vs_chart": d_chart["glyphscore"],
        "iou_re": iou["raw"]["iou_re"],
        "iou_rc": iou["raw"]["iou_rc"],
        "iou_ce": iou["raw"]["iou_ce"],
        "iou_rc_hygiene": _gate(iou["hygiene"]["iou_rc"], cfg["L1"]["iou_rc"]),
        "iou_re_hygiene": iou["hygiene"]["iou_re"],
        "self_agreement_ref": cfg["L1"].get("self_agreement_ref", 0.87),
        "worst_after_hygiene": iou["worst_after_hygiene"],
        "segmentation": f"{seg['n_words_segmented']}/{seg['n_words']}",
    }
    L1["pass"] = _level_pass({"gs": L1["gs_vs_crops"], "rc": L1["iou_rc_hygiene"]})

    # --- L2 geometry ---
    _log("stage: L2 chamfer + boundary-IoU + column geometry")
    shape = _l2_shape_metrics(GS, args.render, args.labels, args.refpack, exclude_lines)
    adv_render = gm.char_advance_autocorr(render_gray, *body)
    adv_real = gm.char_advance_autocorr(real_gray, *body)
    advance_ratio = adv_render / adv_real if adv_real and np.isfinite(adv_real) else float("nan")
    price_std = _price_column_std_px(render_gray, render_boxes, dark_thresh)
    caption_ratio = _caption_ratio(render_boxes, real_lines or [], body)
    L2 = {
        "chamfer_dots": shape["chamfer_dots"],
        "boundary_iou": shape["boundary_iou"],
        "advance_ratio": _gate(advance_ratio, cfg["L2"]["advance_ratio"]),
        "advance_px": {"render": adv_render, "real": adv_real},
        "price_std_px": _gate(price_std, cfg["L2"]["price_std_px"]),
        "barcode_caption_ratio": _gate(caption_ratio, cfg["L2"]["barcode_caption_ratio"]),
        "cap_h_ratio": _gate(cap_ratio, cfg["L2"]["cap_h_ratio"]),
        "interline_fill": {"render": duty_render, "real": duty_real},
    }
    L2["pass"] = _level_pass({k: v for k, v in L2.items()
                              if k in ("advance_ratio", "price_std_px",
                                       "barcode_caption_ratio", "cap_h_ratio")})

    # --- L3 texture ---
    # 1:1 for texture = "indistinguishable from real measured the SAME way", so
    # each channel's pass band centers on the harness-measured real value
    # (dynamic target), not the reports' hand-crop numbers (a different method).
    # The documented Part-1 targets ride along as ``spec_target`` annotations.
    _log("stage: L3 texture / degradation parity")
    tex = _l3_texture(real_gray, render_warp, body, dark_thresh, GS, args.refpack,
                      args.render, args.labels, exclude_lines)

    def _gate_dyn(value, real_value, key):
        spec = cfg["L3"][key]
        g = _gate(value, {"target": real_value, "tol": spec["tol"], "dir": "band"})
        g["real_measured"] = real_value
        g["spec_target"] = spec.get("target")
        return g

    L3 = {
        "ink_darkness": _gate_dyn(tex["render_stroke_core_L"], tex["real_stroke_core_L"], "ink_darkness"),
        "stroke_cov": _gate_dyn(tex["render_stroke_cov"], tex["real_stroke_cov"], "stroke_cov"),
        "edge_transition_frac": _gate_dyn(tex["render_edge_transition_frac"],
                                          tex["real_edge_transition_frac"], "edge_transition_frac"),
        "paper_clip_frac": _gate_dyn(tex["render_paper_clip_frac"],
                                     tex["real_paper_clip_frac"], "paper_clip_frac"),
        "ink_tone_emd": _gate(tex["ink_tone_emd"], cfg["L3"]["ink_tone_emd"]),
        "kanungo_p": _gate(tex["kanungo_p"], cfg["L3"]["kanungo_p"]),
        "kanungo_distance": tex["kanungo_distance"],
    }
    L3["pass"] = _level_pass({k: v for k, v in L3.items()
                              if k in ("ink_darkness", "stroke_cov",
                                       "edge_transition_frac", "paper_clip_frac",
                                       "ink_tone_emd", "kanungo_p")})

    # --- L4 perceptual + task ---
    _log("stage: L4 MS-SSIM + OCR parity (+ forgery/TSTR stubs)")
    ms = gm.ms_ssim(real_gray, render_warp)
    ms_raw = gm.ms_ssim(real_gray, render_gray)
    L4 = {
        "ms_ssim": _gate(ms, cfg["L4"]["ms_ssim"]),
        "ms_ssim_raw": ms_raw,
        "glcm_contrast": {"real": gm.glcm_contrast(real_gray[body[0]:body[1]]),
                          "render": gm.glcm_contrast(render_warp[body[0]:body[1]])},
        "forgery_auc": NOT_IMPLEMENTED,
        "tstr_gap": NOT_IMPLEMENTED,
    }
    if real_lines and render_lines:
        gt = json.load(open(args.labels))["tokens"]
        ocr = gm.ocr_error_sets(gt, real_lines, render_lines)
        L4["ocr_jaccard"] = _gate(ocr["jaccard"], cfg["L4"]["ocr_jaccard"])
        L4["ocr_recall"] = {"real": ocr["real_recall"], "render": ocr["render_recall"]}
        L4["ocr_cer"] = {"real": ocr["real_cer"], "render": ocr["render_cer"]}
        L4["ocr_error_classes"] = {"shared": len(ocr["shared"]),
                                   "render_only": len(ocr["render_only"]),
                                   "real_only": len(ocr["real_only"])}
        # spacing jitter needs render OCR line-grouped glyph centers -> approx
        # from token x-centers per OCR line row
        by_row = defaultdict(list)
        for l in render_lines:
            by_row[round(l["y"] / 25.0)].append(l["x"] + l["w"] / 2.0)
        L4["spacing_jitter_render"] = gm.spacing_jitter(list(by_row.values()))
    else:
        L4["ocr_jaccard"] = {"value": None, "pass": None, "note": NOT_IMPLEMENTED}
        L4["ocr_recall"] = NOT_IMPLEMENTED
        L4["ocr_cer"] = NOT_IMPLEMENTED
    L4["pass"] = _level_pass({k: v for k, v in L4.items()
                              if k in ("ms_ssim", "ocr_jaccard")})

    scorecard["L0"] = L0
    scorecard["L1"] = L1
    scorecard["L2"] = L2
    scorecard["L3"] = L3
    scorecard["L4"] = L4

    # overall rung cleared: first level that does not pass (fail vs incomplete)
    cleared = "L4-pass"
    for lvl in ("L0", "L1", "L2", "L3", "L4"):
        p = scorecard[lvl].get("pass")
        if p is True:
            continue
        cleared = f"{lvl}-{'incomplete' if p is None else 'fail'}"
        break
    scorecard["overall_rung_cleared"] = cleared
    scorecard["elapsed_sec"] = round(time.time() - t_start, 1)
    return scorecard


def _iter_gate_values(sc: dict):
    """Yield (path, value) for every gated metric (dicts carrying a 'dir')."""
    for lvl in ("L0", "L1", "L2", "L3", "L4"):
        block = sc.get(lvl, {})
        for name, g in block.items():
            if isinstance(g, dict) and "dir" in g and isinstance(g.get("value"), (int, float)):
                yield f"{lvl}.{name}", float(g["value"])


def regression_report(current: dict, baseline: dict, cfg: dict) -> dict:
    """CI gate: flag any metric that worsened by more than its ``reg``
    threshold-width vs the committed baseline scorecard (GOLD_STANDARD Part 3).

    "Worse" is direction-aware: for ``min`` gates a drop is worse, for ``max``
    a rise is worse, for ``band`` any move away from target is worse.
    """
    base_vals = dict(_iter_gate_values(baseline))
    regressions = []
    for path, cur in _iter_gate_values(current):
        if path not in base_vals:
            continue
        lvl, name = path.split(".", 1)
        spec = cfg.get(lvl, {}).get(name, {})
        reg = spec.get("reg")
        if reg is None:
            continue
        d = spec.get("dir", "band")
        base = base_vals[path]
        if d == "min":
            worse = base - cur
        elif d == "max":
            worse = cur - base
        else:  # band: distance from target
            t = spec.get("target", 0.0)
            worse = abs(cur - t) - abs(base - t)
        if worse > reg:
            regressions.append({"metric": path, "baseline": base, "current": cur,
                                "worsened_by": round(worse, 4), "reg_width": reg})
    return {"regressions": regressions, "regressed": bool(regressions)}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--render", required=True)
    ap.add_argument("--real", required=True)
    ap.add_argument("--labels", required=True)
    ap.add_argument("--refpack", required=True)
    ap.add_argument("--chart", required=True)
    ap.add_argument("--config", required=True)
    ap.add_argument("--out")
    ap.add_argument("--glyphscore-root",
                    default=os.environ.get("GLYPHSCORE_ROOT", "/private/tmp/glyphscore"))
    ap.add_argument("--cache-dir",
                    default=os.path.join(tempfile.gettempdir(), "costco_gold_cache"))
    ap.add_argument("--baseline",
                    help="prior scorecard JSON; flag metrics that regressed by "
                         "more than their reg threshold-width (CI gate). Exit 2 "
                         "on regression.")
    args = ap.parse_args(argv)

    sc = build_scorecard(args)
    regressed = False
    if args.baseline:
        cfg = json.load(open(args.config))
        baseline = json.load(open(args.baseline))
        rep = regression_report(sc, baseline, cfg)
        sc["regression"] = rep
        regressed = rep["regressed"]
        _log(f"regression check vs {args.baseline}: "
             f"{len(rep['regressions'])} metric(s) regressed")

    sc = _sanitize(sc)
    text = json.dumps(sc, indent=2, sort_keys=False, allow_nan=False)
    if args.out:
        with open(args.out, "w") as fh:
            fh.write(text + "\n")
        _log(f"wrote {args.out}")
    print(text)
    _log(f"overall: {sc['overall_rung_cleared']} in {sc['elapsed_sec']}s")
    return 2 if regressed else 0


if __name__ == "__main__":
    sys.exit(main())
