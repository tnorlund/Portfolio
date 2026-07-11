#!/usr/bin/env python3
"""Final-artifact gates: OCR round-trip + absolute per-glyph quality floor.

These are the two "read the real output" gates the M6 cold-start review found
missing -- one reads the rendered pixels, one reads each minted glyph absolutely
(no fleet consensus).

Usage:
  # round-trip a render against its composed candidate
  python render_gates_cli.py roundtrip --png out.png --composed composed.json \
      --index 4
  # floor-audit a minted atlas (.glyphs.npz)
  python render_gates_cli.py floor --atlas smiths.glyphs.npz
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import numpy as np  # noqa: E402

from glyphstudio.glyph_floor import audit_atlas  # noqa: E402
from glyphstudio.render_roundtrip import roundtrip_gate  # noqa: E402


def _load_npz(path):
    z = np.load(path)
    return {int(k[1:]): z[k].astype(bool) for k in z.files if k.startswith("c")}


def cmd_roundtrip(args) -> int:
    data = json.load(open(args.composed))
    cand = data[args.index] if isinstance(data, list) else data
    rep = roundtrip_gate(args.png, cand)
    print(rep.summary())
    print(
        f"\nmatched={len(rep.matches)} missing={len(rep.missing)} "
        f"unexpected={len(rep.unexpected)}"
    )
    for m in rep.missing:
        print(f"  MISSING    {m.text!r}")
    for u in rep.unexpected:
        print(f"  UNEXPECTED {u.text!r}")
    return 0 if rep.passed else 1


def cmd_floor(args) -> int:
    defects = audit_atlas(_load_npz(args.atlas))
    print(f"{len(defects)} glyph-floor defect(s) in {os.path.basename(args.atlas)}")
    for d in defects:
        print(f"  {d.char!r:4s} {d.kind:12s} {d.detail}")
    return 0 if not defects else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    rt = sub.add_parser("roundtrip")
    rt.add_argument("--png", required=True)
    rt.add_argument("--composed", required=True)
    rt.add_argument("--index", type=int, default=0)
    rt.set_defaults(func=cmd_roundtrip)
    fl = sub.add_parser("floor")
    fl.add_argument("--atlas", required=True)
    fl.set_defaults(func=cmd_floor)
    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
