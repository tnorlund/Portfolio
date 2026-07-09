#!/usr/bin/env python3
"""Discover font families across the merchant atlases (M2).

Prints the pairwise shape-normalized glyph-IoU matrix and the clustered
families. Known answer: Costco isolates; same-font merchants group ~0.7.

Usage:
  python family_discover.py [--fonts DIR] [--threshold 0.65]
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from glyphstudio.family_cluster import discover_families  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument(
        "--fonts",
        default=os.path.abspath(os.path.join(here, "..", "fonts")),
    )
    ap.add_argument("--threshold", type=float, default=0.60)
    args = ap.parse_args(argv)

    font_dirs = {
        name: os.path.join(args.fonts, name)
        for name in sorted(os.listdir(args.fonts))
        if os.path.isdir(os.path.join(args.fonts, name))
        and os.path.exists(os.path.join(args.fonts, name, "font.json"))
    }
    print(f"merchants: {sorted(font_dirs)}", file=sys.stderr)
    res = discover_families(font_dirs, threshold=args.threshold)

    m = res.merchants
    print("\npairwise mean glyph-IoU (shared letterforms):")
    print("            " + " ".join(f"{x[:5]:>6s}" for x in m))
    for i, mi in enumerate(m):
        row = " ".join(f"{res.iou[i, j]:6.2f}" for j in range(len(m)))
        print(f"  {mi:10s} {row}")
    # threshold sweep (family count is method-scale dependent)
    from glyphstudio.family_cluster import cluster_families
    print("\nthreshold sweep (multi-merchant families):")
    for t in (0.54, 0.56, 0.58, 0.60, 0.62):
        fams = cluster_families(m, res.iou, t)
        multi = [f for f in fams if len(f) > 1]
        print(f"  t={t}: {len(fams)} families  multi={multi}")

    print(f"\nfamilies (threshold {res.threshold}):")
    for k, fam in enumerate(res.families, 1):
        print(f"  family {k}: {fam}")

    # known-answer: cvs & vons are the epic's tightest same-font pair
    cvs_vons_together = any(
        "cvs" in f and "vons" in f for f in res.families
    )
    i, j = m.index("cvs"), m.index("vons")
    print(f"\nknown-answer cvs~vons IoU={res.iou[i, j]:.3f}, "
          f"same family: {cvs_vons_together}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
