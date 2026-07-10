#!/usr/bin/env python3
"""Glyph identity gate for .npz atlases (data-mint fleet).

``glyphstudio.glyph_gate`` audits *parametric* font dirs (font.json + glyph
skeletons). The data mint (``synthesis_loop/build_merchant_glyphs.py``) emits
binary ``.glyphs.npz`` atlases instead, so this thin adapter shape-normalizes
npz glyphs the same way ``family_cluster.load_normalized_merchant`` normalizes
rasterized parametric glyphs, then runs the SAME ``audit_normalized`` gate.

Use it to check that a newly minted atlas (e.g. a section-conditioned HEAVY
face) still renders each character as its own letter, referenced against the
fleet of regular atlases — and to compare a candidate's finding count against a
baseline atlas (usually the merchant's own regular face).

Usage:
  glyph_gate_npz.py CANDIDATE.npz CANDIDATE_NAME \\
      [--fleet-dir DIR] [--baseline REG.npz REG_NAME]

``--fleet-dir`` defaults to $BITMATRIX_DIR (or /tmp/bitmatrix): every
``*.glyphs.npz`` there whose name has no ``-heavy`` suffix joins the fleet.
"""
from __future__ import annotations

import argparse
import glob
import os
import sys
from collections import Counter

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(_HERE, "py"))

from glyphstudio.family_cluster import normalize_glyph  # noqa: E402
from glyphstudio.glyph_gate import GATE_CHARS, audit_normalized  # noqa: E402


def load_npz_normalized(path: str, size: int = 32) -> dict[int, np.ndarray]:
    """Shape-normalize the atlas's binary glyphs for the gate's char set."""
    z = np.load(path)
    out: dict[int, np.ndarray] = {}
    for k in z.files:
        if not k.startswith("c"):
            continue
        cp = int(k[1:])
        if chr(cp) not in GATE_CHARS:
            continue
        out[cp] = normalize_glyph(z[k].astype(bool), size=size)
    return out


def build_fleet(fleet_dir: str) -> dict[str, dict[int, np.ndarray]]:
    fleet: dict[str, dict[int, np.ndarray]] = {}
    for p in sorted(glob.glob(os.path.join(fleet_dir, "*.glyphs.npz"))):
        base = os.path.basename(p)[: -len(".glyphs.npz")]
        if base.endswith("-heavy"):
            continue  # regular faces only in the reference fleet
        fleet[base] = load_npz_normalized(p)
    return fleet


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("candidate")
    ap.add_argument("candidate_name")
    ap.add_argument(
        "--fleet-dir",
        default=os.environ.get("BITMATRIX_DIR", "/tmp/bitmatrix"),
    )
    ap.add_argument("--baseline", nargs=2, metavar=("NPZ", "NAME"), default=None)
    args = ap.parse_args(argv)

    normalized = build_fleet(args.fleet_dir)
    normalized[args.candidate_name] = load_npz_normalized(args.candidate)
    focus = {args.candidate_name}
    if args.baseline:
        normalized[args.baseline[1]] = load_npz_normalized(args.baseline[0])
        focus.add(args.baseline[1])

    findings = audit_normalized(normalized)
    by_m: dict[str, Counter] = {}
    for f in findings:
        by_m.setdefault(f.merchant, Counter())[f.kind] += 1

    print(f"{'merchant':22} MISRENDER LOW_AGREE MISSING  total")
    for m in sorted(normalized):
        c = by_m.get(m, Counter())
        mark = "  <==" if m in focus else ""
        print(
            f"{m:22} {c['MISRENDER']:9} {c['LOW_AGREEMENT']:9} "
            f"{c['MISSING']:7}  {sum(c.values()):5}{mark}"
        )
    print("\n--- candidate / baseline detail ---")
    for f in findings:
        if f.merchant in focus:
            print(f"  [{f.merchant}] {f.char!r} {f.kind}: {f.detail}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
