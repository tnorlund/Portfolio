"""Seed glyph skeletons from the real-letterform corpus.

Usage:
  python -m glyphstudio.trace <samples.npz> <font_dir> [--chars ABC] [--force]

Per char: consensus -> 3x upsample -> Zhang-Suen thin -> spur prune ->
path extraction -> corner-split Schneider fit -> cap-unit glyph JSON.
Hand-edited glyphs are never clobbered (they divert to _traced/).
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import os
import sys

import numpy as np

from . import ADVANCE_REF_CHARS, CAP_UNITS
from .fitcurve import polyline_to_segments, segments_to_nodes
from .paths import extract_paths
from .samples import (
    canvas_geometry,
    consensus,
    list_codepoints,
    load_stack,
    stroke_width_px,
    upsample,
)
from .schema import (
    DEFAULT_FONT,
    atomic_write_json,
    font_dir_paths,
    glyph_filename,
    write_glyph,
)
from .thin import prune_spurs, zhang_suen

UPSAMPLE = 3


def trace_char(stack: np.ndarray, codepoint: int) -> tuple[dict | None, float]:
    """Trace one char's sample stack -> (glyph JSON, stroke_width_units)."""
    sample_ref_cap, baseline_row = canvas_geometry(stack.shape[1])
    mask = consensus(stack)
    if not mask.any():
        return None, 0.0
    big = upsample(mask, UPSAMPLE)
    skel = zhang_suen(big)
    width_px = stroke_width_px(big, skel)
    skel = prune_spurs(skel, min_len=max(2.0, 0.6 * width_px))
    if not skel.any():
        return None, 0.0
    raw_paths = extract_paths(skel)
    if not raw_paths:
        return None, 0.0

    scale_units = CAP_UNITS / (sample_ref_cap * UPSAMPLE)
    baseline_ss = baseline_row * UPSAMPLE

    strokes = []
    all_x: list[float] = []
    for path in raw_paths:
        pts = np.array(path["points"])  # (y, x) in upsampled px
        if len(pts) < 2:
            continue
        segments = polyline_to_segments(
            pts, tol=1.5, line_tol=0.9, corner_deg=40.0
        )
        if not segments:
            continue
        # convert control points (y,x px) -> cap units (x, y-up)
        for seg in segments:
            seg["ctrl"] = np.array([
                [
                    p[1] * scale_units,
                    (baseline_ss - p[0]) * scale_units,
                ]
                for p in seg["ctrl"]
            ])
        nodes = segments_to_nodes(segments, closed=path["closed"])
        if len(nodes) < 2:
            continue
        for node in nodes:
            all_x.append(node["x"])
            for h in ("hIn", "hOut"):
                if h in node:
                    all_x.append(node[h]["x"])
        strokes.append({"closed": bool(path["closed"]), "nodes": nodes})

    if not strokes:
        return None, 0.0

    width_units = width_px * scale_units

    # Snap stroke ENDPOINTS to the guide lines. Thermal glyphs align flat to
    # baseline/cap/x-height, but thinning eats a few pixels off stroke tips
    # (worst on diagonals: A M N V W), so endpoints that nearly reach a guide
    # centerline are pulled exactly onto it.
    r = width_units / 2.0
    guides = [r, CAP_UNITS - r, 700.0 - r]  # baseline, cap, x-height (ink)
    snap_tol = 80.0
    for stroke in strokes:
        if stroke["closed"]:
            continue
        for node in (stroke["nodes"][0], stroke["nodes"][-1]):
            for gy in guides:
                if abs(node["y"] - gy) <= snap_tol:
                    node["y"] = round(gy, 1)
                    break

    # normalize: left-most centerline point sits at x = stroke radius
    x_shift = (width_units / 2.0) - min(all_x)
    for stroke in strokes:
        for node in stroke["nodes"]:
            node["x"] = round(node["x"] + x_shift, 1)
            node["y"] = round(node["y"], 1)
            for h in ("hIn", "hOut"):
                if h in node:
                    node[h]["x"] = round(node[h]["x"] + x_shift, 1)
                    node[h]["y"] = round(node[h]["y"], 1)

    ink_width = (max(all_x) - min(all_x)) + width_units
    glyph = {
        "version": 1,
        "char": chr(codepoint),
        "codepoint": codepoint,
        "provenance": "traced",
        "trace": {
            "corpus": "sprouts.samples.npz",
            "samples": int(len(stack)),
            "consensusHash": hashlib.sha1(mask.tobytes()).hexdigest()[:12],
            "date": datetime.date.today().isoformat(),
        },
        "width": round(ink_width, 1),
        "baselineNudgePx": 0,
        "overrides": {},
        "strokes": strokes,
    }
    return glyph, width_units


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("samples")
    ap.add_argument("font_dir")
    ap.add_argument("--chars", help="only these characters")
    ap.add_argument("--force", action="store_true",
                    help="overwrite even hand-edited glyphs")
    args = ap.parse_args(argv)

    codepoints = list_codepoints(args.samples)
    if args.chars:
        wanted = {ord(c) for c in args.chars}
        codepoints = [cp for cp in codepoints if cp in wanted]

    paths = font_dir_paths(args.font_dir)
    os.makedirs(paths["glyphs"], exist_ok=True)
    os.makedirs(paths["traced"], exist_ok=True)

    written, diverted, failed = [], [], []
    dot_sizes = []
    for cp in codepoints:
        stack = load_stack(args.samples, cp)
        if stack is None or len(stack) == 0:
            failed.append(chr(cp))
            continue
        glyph, width_units = trace_char(stack, cp)
        if glyph is None:
            failed.append(chr(cp))
            continue
        if chr(cp) in ADVANCE_REF_CHARS and width_units > 0:
            dot_sizes.append(width_units)
        target = write_glyph(args.font_dir, glyph,
                             respect_edited=not args.force)
        if os.sep + "_traced" + os.sep in target:
            diverted.append(chr(cp))
        else:
            written.append(chr(cp))

    # Seed font.json once (never overwrite an existing one).
    font_path = paths["font"]
    if not os.path.exists(font_path):
        font = json.loads(json.dumps(DEFAULT_FONT))
        font["name"] = os.path.basename(os.path.normpath(args.font_dir))
        if dot_sizes:
            font["params"]["dot"]["size"] = round(
                float(np.median(dot_sizes)), 1
            )
        atomic_write_json(font_path, font)
        print(f"seeded font.json (dot.size={font['params']['dot']['size']})")

    print(f"traced: {len(written)} glyphs -> {paths['glyphs']}")
    if diverted:
        print(f"diverted (hand-edited, see _traced/): {''.join(diverted)}")
    if failed:
        print(f"failed: {''.join(failed)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
