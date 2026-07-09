#!/usr/bin/env python3
"""Emit the (merchant, section) -> (family, face) map as JSON (M2 exit).

Combines family discovery (shape-normalized glyph IoU) with the per-section
faces measured in each merchant's stylemap.json.

Usage:
  python section_face_map_cli.py [--fonts DIR] [--threshold 0.60] [--out map.json]
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import asdict

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from glyphstudio.family_cluster import discover_families  # noqa: E402
from glyphstudio.section_face_map import (  # noqa: E402
    build_section_face_map,
    families_to_merchant_family,
)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    here = os.path.dirname(os.path.abspath(__file__))
    ap.add_argument("--fonts", default=os.path.abspath(os.path.join(here, "..", "fonts")))
    ap.add_argument("--threshold", type=float, default=0.60)
    ap.add_argument("--out", default=None)
    args = ap.parse_args(argv)

    font_dirs = {
        n: os.path.join(args.fonts, n)
        for n in sorted(os.listdir(args.fonts))
        if os.path.exists(os.path.join(args.fonts, n, "font.json"))
    }
    res = discover_families(font_dirs, threshold=args.threshold)
    mf = families_to_merchant_family(res.families)
    entries = build_section_face_map(font_dirs, mf)

    obj = {
        "families": res.families,
        "threshold": res.threshold,
        "map": [
            {
                "merchant": e.merchant,
                "section": e.section,
                "family": e.family,
                "face": asdict(e.face),
            }
            for e in entries
        ],
    }
    text = json.dumps(obj, indent=2)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as fh:
            fh.write(text)
        print(f"wrote {args.out} ({len(entries)} entries, {len(res.families)} families)",
              file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
