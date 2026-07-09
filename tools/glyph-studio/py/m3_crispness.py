#!/usr/bin/env python3
"""Consensus-crispness comparison: do pooled crops sharpen or blur glyphs?

crispness = mean over ink-ish pixels of 2*|consensus - 0.5| (0 = fuzzy,
1 = crisp). Each sample is centroid-aligned before averaging, so the metric
measures consensus decisiveness rather than misregistration. See
../M3_FINDINGS.md: cross-merchant pooling BLURRED every hard diagonal.

Usage:
  python m3_crispness.py SOLO_A.samples.npz [SOLO_B.samples.npz ...] POOLED.samples.npz \
      [--chars MWKVXYNAZ] [--controls OICL0]
The last path is treated as the pooled stack; the rest are solo baselines.
"""

from __future__ import annotations

import argparse
import os
import sys

import numpy as np

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)


def _center(mask: np.ndarray) -> np.ndarray:
    ys, xs = np.where(mask)
    if len(ys) == 0:
        return mask
    h, w = mask.shape
    dy = h // 2 - int(round(ys.mean()))
    dx = w // 2 - int(round(xs.mean()))
    return np.roll(np.roll(mask, dy, 0), dx, 1)


def consensus_crispness(path: str, cp: int):
    from glyphstudio.samples import load_stack

    stack = load_stack(path, cp)
    if stack is None or len(stack) == 0:
        return None, 0
    aligned = np.stack([_center(s) for s in stack]).astype(np.float32)
    cons = aligned.mean(0)
    ink = cons > 0.10
    if not ink.any():
        return 0.0, len(stack)
    return float((2 * np.abs(cons[ink] - 0.5)).mean()), len(stack)


def report(label: str, chars: str, solos: list[str], pooled: str) -> None:
    names = [os.path.basename(s).split(".")[0] for s in solos]
    header = " ".join(f"{n[:12]:>14s}" for n in names)
    print(f"\n=== {label} ===")
    print(f"  ch {header} {'pooled':>14s}   pooled_vs_best")
    for ch in chars:
        cp = ord(ch)
        solo_vals = [consensus_crispness(s, cp) for s in solos]
        pv, pn = consensus_crispness(pooled, cp)
        best = max((v for v, _ in solo_vals if v is not None), default=None)
        delta = f"{pv - best:+.3f}" if pv is not None and best is not None else "-"

        def fmt(v, n):
            return f"{v:.3f}(n={n})" if v is not None else f"  -  (n={n})"

        cols = " ".join(f"{fmt(v, n):>14s}" for v, n in solo_vals)
        print(f"  {ch:2s} {cols} {fmt(pv, pn):>14s}   {delta}")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("stacks", nargs="+", help="solo .samples.npz ..., pooled last")
    ap.add_argument("--chars", default="MWKVXYNAZ")
    ap.add_argument("--controls", default="OICL0")
    args = ap.parse_args(argv)
    if len(args.stacks) < 2:
        ap.error("need at least one solo stack and the pooled stack")
    *solos, pooled = args.stacks
    report("HARD DIAGONALS", args.chars, solos, pooled)
    if args.controls:
        report("EASY CONTROLS", args.controls, solos, pooled)
    return 0


if __name__ == "__main__":
    sys.exit(main())
