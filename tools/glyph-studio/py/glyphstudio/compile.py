"""Compile a font dir -> .glyphs.npz, then self-verify with the REAL consumer.

Usage:
  python -m glyphstudio.compile <font_dir> <out.npz> [--sheet out.png]

Writes only c{cp}/o{cp} keys (the entire contract), then instantiates the
renderer's actual BitmapFont class and reports cap_h, advance_ratio,
cap-height deviations, clamp-width warnings, and ASCII coverage.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys

import numpy as np

from . import ADVANCE_REF_CHARS, CAP_REF_CHARS
from .raster import rasterize_glyph
from .schema import load_font, load_glyphs, merged_params

_WORKTREE = os.path.abspath(
    os.path.join(os.path.dirname(__file__), "..", "..", "..", "..")
)


def _import_bitmap_font():
    for pkg in ("receipt_agent", "receipt_upload", "receipt_dynamo"):
        path = os.path.join(_WORKTREE, pkg)
        if path not in sys.path:
            sys.path.insert(0, path)
    from receipt_agent.agents.label_evaluator.rendering.bitmap_font import (
        BitmapFont,
    )
    return BitmapFont


def compile_font(font_dir: str, out_path: str) -> dict:
    font = load_font(font_dir)
    glyphs = load_glyphs(font_dir)
    ref_cap = int(font.get("refCap", 60))

    arrays: dict[str, np.ndarray] = {}
    empty: list[str] = []
    for cp, glyph in sorted(glyphs.items()):
        params = merged_params(font, glyph)
        bitmap, offset = rasterize_glyph(glyph, params, ref_cap)
        if bitmap.sum() == 0:
            empty.append(chr(cp))
            continue
        arrays[f"c{cp}"] = bitmap.astype(np.uint8)
        arrays[f"o{cp}"] = np.int16(offset)

    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    np.savez(out_path, **arrays)

    report = self_check(out_path, ref_cap)
    report["empty"] = empty
    report["glyph_count"] = len(arrays) // 2
    return report


def self_check(npz_path: str, ref_cap: int) -> dict:
    BitmapFont = _import_bitmap_font()
    bmf = BitmapFont(npz_path)
    covered = sorted(bmf.glyphs.keys())
    missing = [chr(cp) for cp in range(33, 127) if chr(cp) not in covered]
    advance_ratio = float(bmf.advance(1.0))

    cap_devs = {}
    for ch in CAP_REF_CHARS:
        if ch in bmf.glyphs:
            h = bmf.glyphs[ch].shape[0]
            if abs(h - bmf.cap_h) > 2:
                cap_devs[ch] = int(h)

    clamp_warn = []
    cell_w = bmf.advance(ref_cap)
    scale = ref_cap / bmf.cap_h
    for ch, arr in bmf.glyphs.items():
        if arr.shape[1] * scale > 0.96 * cell_w:
            clamp_warn.append(ch)

    offsets = {ch: int(bmf.offsets.get(ch, 0)) for ch in ("A", "-", "p", "g")
               if ch in bmf.glyphs}
    return {
        "cap_h": float(bmf.cap_h),
        "advance_ratio": advance_ratio,
        "advance_ref_present": [c for c in ADVANCE_REF_CHARS
                                if c in bmf.glyphs],
        "coverage": len(covered),
        "missing": "".join(missing),
        "cap_height_deviations": cap_devs,
        "clamp_width_warnings": "".join(sorted(clamp_warn)),
        "sample_offsets": offsets,
    }


def render_sheet(npz_path: str, sheet_path: str) -> None:
    review = os.path.join(_WORKTREE, "synthesis_loop", "glyph_review.py")
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        [os.path.join(_WORKTREE, p) for p in
         ("receipt_agent", "receipt_dynamo", "receipt_upload")]
        + [env.get("PYTHONPATH", "")]
    )
    subprocess.run(
        [sys.executable, review, "sheet",
         os.path.abspath(npz_path), os.path.abspath(sheet_path)],
        check=True, env=env, cwd=_WORKTREE,
    )


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("font_dir")
    ap.add_argument("out")
    ap.add_argument("--sheet")
    args = ap.parse_args(argv)

    report = compile_font(args.font_dir, args.out)
    print(f"wrote {args.out}: {report['glyph_count']} glyphs")
    print(f"  cap_h={report['cap_h']:.1f} advance_ratio={report['advance_ratio']:.3f}")
    print(f"  coverage={report['coverage']}/94 missing: {report['missing'] or '-'}")
    if report["cap_height_deviations"]:
        print(f"  CAP-HEIGHT DEVIATIONS: {report['cap_height_deviations']}")
    if report["clamp_width_warnings"]:
        print(f"  CLAMP-WIDTH WARNINGS (squeezed at render): "
              f"{report['clamp_width_warnings']}")
    if report["empty"]:
        print(f"  EMPTY RASTERS: {''.join(report['empty'])}")
    print(f"  offsets: {report['sample_offsets']}")
    if args.sheet:
        render_sheet(args.out, args.sheet)
        print(f"  sheet -> {args.sheet}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
