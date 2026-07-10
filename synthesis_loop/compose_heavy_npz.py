#!/usr/bin/env python3
"""Compose a render-usable HEAVY atlas from a (partial) section-conditioned
mint plus the merchant's regular atlas as backfill.

The renderer's per-char fallback for an atlas miss is the TTF face, NOT the
regular atlas -- so a sparse minted heavy face would draw bold rows in a mixed
alien face. Overlaying the minted bold glyphs on the full regular atlas keeps
every char in the merchant's own letterforms while the chars with real bold
evidence use it.

Writes ``OUT.npz`` plus ``OUT.manifest.json`` documenting the composition:
which chars are minted (true bold), which are backfilled (regular weight), and
which minted chars were dropped by ``--drop`` curation (e.g. a glyph the
identity gate flagged as MISRENDER).

Usage:
  compose_heavy_npz.py MINTED_HEAVY.npz REGULAR.npz OUT.npz [--drop KX...]

Nothing publishes; the compiled npz + manifest stay local (same policy as
every other atlas: JSON sources in-repo, npz artifacts out).
"""
from __future__ import annotations

import argparse
import json
import os

import numpy as np


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("minted")
    ap.add_argument("regular")
    ap.add_argument("out")
    ap.add_argument(
        "--drop",
        default="",
        help="chars to exclude from the minted overlay (curation), e.g. 'K'",
    )
    args = ap.parse_args(argv)

    zm, zr = np.load(args.minted), np.load(args.regular)
    dropped = set(args.drop)
    payload: dict[str, np.ndarray] = {k: zr[k] for k in zr.files}
    minted, backfilled = [], []
    for k in zm.files:
        if not k.startswith("c"):
            continue
        ch = chr(int(k[1:]))
        if ch in dropped:
            continue
        payload[k] = zm[k]
        ok = "o" + k[1:]
        if ok in zm.files:
            payload[ok] = zm[ok]
        minted.append(ch)
    minted_set = set(minted)
    for k in zr.files:
        if k.startswith("c") and chr(int(k[1:])) not in minted_set:
            backfilled.append(chr(int(k[1:])))
    np.savez_compressed(args.out, **payload)

    manifest = {
        "out": os.path.basename(args.out),
        "minted_source": os.path.basename(args.minted),
        "backfill_source": os.path.basename(args.regular),
        "minted_chars": "".join(sorted(minted)),
        "n_minted": len(minted),
        "backfilled_chars": "".join(sorted(backfilled)),
        "n_backfilled": len(backfilled),
        "dropped_from_mint": "".join(sorted(dropped & {
            chr(int(k[1:])) for k in zm.files if k.startswith("c")
        })),
        "note": (
            "minted chars carry true bold letterforms from the merchant's "
            "own bold rows (section-conditioned mint); backfilled chars are "
            "the REGULAR face at body weight -- rows mixing both will show a "
            "weight seam on backfilled chars."
        ),
    }
    mpath = os.path.splitext(args.out)[0] + ".manifest.json"
    # foo.glyphs.npz -> foo.glyphs.manifest.json (strip only the .npz)
    with open(mpath, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=1)
    print(
        f"wrote {args.out}: {len(minted)} minted "
        f"({manifest['minted_chars']}), {len(backfilled)} backfilled; "
        f"manifest -> {mpath}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
