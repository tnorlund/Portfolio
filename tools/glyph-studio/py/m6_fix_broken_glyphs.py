"""M6 FIX 3 -- repair the 4 broken NATIVE Smith's glyph cells (e, u, E, V).

The cold-start mint (synthesis_loop/build_merchant_glyphs.py) minted these four
cells from Smith's ~8k crops, but the MISRENDER gate scored only 8 of 27 native
glyphs and never scored e/E/u/V (REVIEW D2/D3). In the v1 render they break:
  e -> raised superscript speck      ("cashi.r", ".arn.d")
  u -> raised superscript speck      ("Yo.r", "F..l")
  E -> missing bottom bar, reads F   ("BALANCE"->"BALANCF", "SPONGE"->"SPONGF")
  V -> no diagonals, reads L         ("VERIFIED"->"LFRIFIFD")

Root cause (confirmed by a cached-sample re-vote sweep):
  * e/E lose their bottom strokes because the global VOTE=0.45 consensus
    threshold cuts the higher-variance bottom rows; for e this also drags the
    baseline offset up (-19) so the survivor floats as a speck. Re-minting E at
    VOTE=0.30 recovers the full bottom bar (ink 411->619, h 55->60, offset
    -4->-1) and reads as a clean E in-word.
  * e re-vote yields an irregular blob (not a clean 'e'); u re-vote is unchanged
    (offset stays -20, still raised); V has only 10 samples and either reads as L
    (central exemplar) or shatters/blobs (vote). These three FAIL visual sanity
    at every parameter setting -> DEMOTE to the borrowed(vons) donor, which the
    other 75 glyphs already use and which renders cleanly.

This is a parameter-level, unforked repair (per-cell VOTE only) plus a demotion
of the un-salvageable cells. Anti-copy is mandatory on the re-minted E: it must
not be byte-identical to any fleet donor glyph (it is not -- it is minted from
Smith's own crops).

Usage:
  python m6_fix_broken_glyphs.py \
    --native   /private/tmp/m6_work/font/smiths.glyphs.npz \
    --samples  /private/tmp/m6_work/font/smiths.samples.npz \
    --v1-atlas /private/tmp/m6_work/font/smiths-borrowed.glyphs.npz \
    --donor    /private/tmp/bmx_vons/vons.glyphs.npz \
    --fleet-dir /private/tmp/bitmatrix_audit \
    --out-atlas  /private/tmp/m6_work/font/smiths-fixed.glyphs.npz \
    --out-labels /private/tmp/m6_work/borrow_labels_fixed.json
"""
import argparse
import glob
import json
import os
import sys

import numpy as np

# per-cell vote overrides and demotions decided by the cached-sample sweep
REMINT_VOTE = {"E": 0.30}          # recovers the missing bottom bar
DEMOTE_TO_DONOR = ["e", "u", "V"]  # re-vote fails visual sanity -> borrow vons


def _load_bmg():
    here = os.path.dirname(os.path.abspath(__file__))
    loop = os.path.normpath(os.path.join(here, "..", "..", "..", "synthesis_loop"))
    sys.path.insert(0, loop)
    import build_merchant_glyphs as bmg  # noqa: E402
    return bmg


def _remint(bmg, samples_npz, ch, vote):
    old = bmg.VOTE
    bmg.VOTE = vote
    try:
        binm, _n = bmg._vote([s for s in samples_npz[str(ord(ch))]], ch)
    finally:
        bmg.VOTE = old
    iy0, iy1, ix0, ix1 = bmg._ink_bbox(binm)
    g = binm[iy0:iy1 + 1, ix0:ix1 + 1].astype(np.uint8)
    off = int(iy1 - bmg.CANVAS_BASE)
    return g, off


def _anti_copy(g, off, fleet):
    """True if glyph g is NOT byte-identical (+ same baseline) to any donor."""
    for name, npz in fleet.items():
        for k in npz.files:
            if not k.startswith("c"):
                continue
            cp = int(k[1:])
            b = npz[k]
            if (b.shape == g.shape and np.array_equal(b, g)
                    and int(npz[f"o{cp}"]) == off):
                return False, f"{name}:{chr(cp)}"
    return True, ""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--native", required=True)
    ap.add_argument("--samples", required=True)
    ap.add_argument("--v1-atlas", required=True)
    ap.add_argument("--donor", required=True)
    ap.add_argument("--fleet-dir", default="")
    ap.add_argument("--out-atlas", required=True)
    ap.add_argument("--out-labels", required=True)
    args = ap.parse_args()

    bmg = _load_bmg()
    samples = np.load(args.samples)
    v1 = np.load(args.v1_atlas)
    donor = np.load(args.donor)
    donor_name = os.path.basename(args.donor).split(".")[0]

    fleet = {donor_name: donor}
    if args.fleet_dir:
        for p in sorted(glob.glob(os.path.join(args.fleet_dir, "*.glyphs.npz"))):
            if "heavy" in os.path.basename(p):
                continue
            fleet.setdefault(os.path.basename(p).split(".")[0], np.load(p))

    payload = {k: v1[k] for k in v1.files}          # start from v1 borrowed
    labels = json.load(open(os.path.join(os.path.dirname(args.out_labels),
                                          "borrow_labels.json")))

    # 1) re-mint (parameter-level) the salvageable cells
    for ch, vote in REMINT_VOTE.items():
        g, off = _remint(bmg, samples, ch, vote)
        ok, hit = _anti_copy(g, off, fleet)
        status = "PASS" if ok else f"FAIL({hit})"
        print(f"re-mint {ch!r} vote={vote}: ink={int(g.sum())} off={off} "
              f"shape={g.shape} anti-copy={status}")
        if not ok:
            print(f"  ABORT: re-minted {ch!r} is a copy of {hit}")
            return 3
        cp = ord(ch)
        payload[f"c{cp}"] = g
        payload[f"o{cp}"] = np.int16(off)
        labels[ch] = "native(re-minted vote=%.2f, recovered bottom bar)" % vote

    # 2) demote the un-salvageable cells to the donor
    for ch in DEMOTE_TO_DONOR:
        cp = ord(ch)
        payload[f"c{cp}"] = donor[f"c{cp}"]
        payload[f"o{cp}"] = donor[f"o{cp}"]
        labels[ch] = f"borrowed({donor_name}) replacing broken native"
        print(f"demote {ch!r} -> borrowed({donor_name}) "
              f"(ink={int(donor[f'c{cp}'].sum())} off={int(donor[f'o{cp}'])})")

    np.savez_compressed(args.out_atlas, **payload)
    json.dump(labels, open(args.out_labels, "w"), indent=1)
    n_nat = sum(1 for v in labels.values() if v.startswith("native"))
    n_bor = sum(1 for v in labels.values() if v.startswith("borrowed"))
    print(f"wrote {args.out_atlas}")
    print(f"wrote {args.out_labels}: {n_nat} native / {n_bor} borrowed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
