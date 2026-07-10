#!/usr/bin/env python3
"""Anti-copy gate for npz atlases: refuse a mint whose glyph bitmaps duplicate
another merchant's atlas (mirrors publish_merchant_font._reject_copied_letterforms,
which checks parametric skeleton JSON; here we compare the binary bitmaps).

Trivial glyphs (tiny ink) legitimately coincide; only substantial glyphs count.

Usage: anti_copy_npz.py CANDIDATE.npz [--fleet-dir /tmp/bitmatrix]
"""
import argparse, glob, os, sys
import numpy as np


def load(p):
    z = np.load(p)
    return {k: z[k] for k in z.files if k.startswith("c")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("candidate")
    ap.add_argument("--fleet-dir", default=os.environ.get("BITMATRIX_DIR", "/tmp/bitmatrix"))
    args = ap.parse_args()
    mine = {k: v for k, v in load(args.candidate).items() if v.sum() >= 40}
    if not mine:
        print("no substantial glyphs to check")
        return 0
    worst = None
    for other in sorted(glob.glob(os.path.join(args.fleet_dir, "*.glyphs.npz"))):
        if os.path.abspath(other) == os.path.abspath(args.candidate):
            continue
        theirs = load(other)
        same = [k for k, v in mine.items()
                if k in theirs and v.shape == theirs[k].shape and np.array_equal(v, theirs[k])]
        frac = len(same) / len(mine)
        if worst is None or frac > worst[1]:
            worst = (os.path.basename(other), frac, same)
        if len(same) > max(4, int(0.25 * len(mine))):
            print(f"ANTI-COPY FAIL: {len(same)}/{len(mine)} substantial glyphs byte-identical "
                  f"to {os.path.basename(other)} (e.g. {sorted(same)[:6]})")
            return 1
    if worst is None:
        # Fail closed: with no reference atlases the check proved nothing.
        print(f"ANTI-COPY INCONCLUSIVE: no reference *.glyphs.npz in {args.fleet_dir}")
        return 1
    print(f"anti-copy PASS: worst overlap {worst[1]:.0%} with {worst[0]} "
          f"({len(worst[2])}/{len(mine)} identical)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
