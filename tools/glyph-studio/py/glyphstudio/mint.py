"""One-shot font mint: trace -> simplify -> handcraft -> donor fill -> normalize
-> compile -> specimen + compare strips.

Collapses ADD_MERCHANT.md steps 3-5 into one idempotent command so a thin
corpus (Speedway had 6 receipts) still yields a 94/94 face in one pass:

* traced glyphs come from the refined corpus (``glyphstudio.trace``);
* diagonals and sampleless glyphs that ``glyphstudio.handcraft`` knows are
  authored parametrically;
* whatever is still missing, rejected by the simplify gate, thinner than
  ``--min-samples`` or named in ``--fix`` adopts the skeleton of a sibling
  face (``--donor vons``), y-remapped onto this font's cap band and marked
  ``provenance: "edited"`` with a note, so a re-trace never clobbers it;
* every non-traced glyph is squeezed into the traced cell width;
* the result compiles and a specimen sheet + compare strips land in
  ``--report-dir`` for the human triage pass (``--fix`` is how that pass
  feeds back).

Usage:
    python -m glyphstudio.mint <refined.npz> <font_dir> [--donor vons]
        [--fix "BEGP"] [--min-samples 6] [--out-npz X] [--report-dir D]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any

import numpy as np

from . import handcraft as _handcraft
from . import simplify as _simplify
from . import trace as _trace
from .compile import compile_font
from .samples import list_codepoints, load_stack
from .schema import (
    atomic_write_json,
    font_dir_paths,
    glyph_filename,
    load_font,
    load_glyphs,
)

ASCII = [chr(cp) for cp in range(33, 127)]
FONTS_DIR = os.path.join(
    os.path.dirname(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ),
    "fonts",
)
# Diagonals and symbols that average into a jitter cloud: parametric
# hand-authoring beats any trace or donor for these (ADD_MERCHANT.md §5).
HANDCRAFT_PREFERRED = set('KMNVWXYvwxyzk%#"^`;?&{}!+')
# Flat-topped, flat-bottomed caps: their node extents ARE the cap band.
CAP_BAND_CHARS = "HITELFN"
DEFAULT_TARGET_BAND = (50.0, 950.0)


def _walk(glyph: dict, fn) -> None:
    for stroke in glyph["strokes"]:
        for node in stroke["nodes"]:
            node["x"], node["y"] = fn(node["x"], node["y"])
            for handle in ("hIn", "hOut"):
                if node.get(handle):
                    node[handle]["x"], node[handle]["y"] = fn(
                        node[handle]["x"], node[handle]["y"]
                    )


def _extents(glyph: dict) -> tuple[float, float, float, float]:
    xs = [n["x"] for s in glyph["strokes"] for n in s["nodes"]]
    ys = [n["y"] for s in glyph["strokes"] for n in s["nodes"]]
    return min(xs), max(xs), min(ys), max(ys)


def cap_band(
    glyphs: dict[int, dict], only_traced: bool
) -> tuple[float, float] | None:
    """(baseline_y, cap_y) of the skeleton centerlines, from flat caps."""
    lows, highs = [], []
    for ch in CAP_BAND_CHARS:
        g = glyphs.get(ord(ch))
        if g is None or (only_traced and g.get("provenance") != "traced"):
            continue
        _, _, y0, y1 = _extents(g)
        lows.append(y0)
        highs.append(y1)
    if not lows:
        return None
    return float(np.median(lows)), float(np.median(highs))


def cell_extent(glyphs: dict[int, dict]) -> float | None:
    """Widest traced cap ink extent: the monospace cell every glyph must fit."""
    widths = [
        _extents(g)[1] - _extents(g)[0]
        for cp, g in glyphs.items()
        if chr(cp) in "MWHNUABDOR" and g.get("provenance") == "traced"
    ]
    return float(max(widths)) if widths else None


def adopt_donor(
    donor_dir: str,
    font_dir: str,
    chars: str,
    *,
    target_band: tuple[float, float],
    note: str,
) -> list[str]:
    donor_glyphs = load_glyphs(donor_dir)
    band = cap_band(donor_glyphs, only_traced=False) or DEFAULT_TARGET_BAND
    d0, d1 = band
    t0, t1 = target_band
    k = (t1 - t0) / (d1 - d0)
    paths = font_dir_paths(font_dir)
    adopted = []
    for ch in chars:
        g = donor_glyphs.get(ord(ch))
        if g is None:
            continue
        g = json.loads(json.dumps(g))
        g["provenance"] = "edited"
        g.pop("trace", None)
        g["note"] = note
        _walk(g, lambda x, y: (x, round(t0 + (y - d0) * k, 1)))
        g["normalized"] = (
            f"y: donor({d0:.0f}..{d1:.0f}) -> ({t0:.0f}..{t1:.0f})"
        )
        atomic_write_json(
            os.path.join(paths["glyphs"], glyph_filename(ord(ch))), g
        )
        adopted.append(ch)
    return adopted


def squeeze_wide(
    font_dir: str, max_extent: float, skip: set[str]
) -> list[str]:
    """Squeeze non-traced glyphs wider than the traced cell into it."""
    glyphs = load_glyphs(font_dir)
    paths = font_dir_paths(font_dir)
    squeezed = []
    for cp, g in glyphs.items():
        ch = chr(cp)
        if ch in skip or g.get("provenance") == "traced":
            continue
        x0, x1, _, _ = _extents(g)
        extent = x1 - x0
        if extent <= max_extent * 1.03:
            continue
        kx = max_extent / extent
        _walk(g, lambda x, y, x0=x0, kx=kx: (round(x0 + (x - x0) * kx, 1), y))
        g["width"] = round(x0 + max_extent + 60, 1)
        g["normalized"] = (
            g.get("normalized", "") + f"; x {extent:.0f} -> {max_extent:.0f}"
        ).strip("; ")
        atomic_write_json(os.path.join(paths["glyphs"], glyph_filename(cp)), g)
        squeezed.append(ch)
    return squeezed


def specimen(npz_path: str, out_png: str, cap: int = 40) -> None:
    """Every ASCII glyph through the renderer's BitmapFont, x2 NEAREST."""
    from PIL import Image

    from .compile import _import_bitmap_font

    BitmapFont = _import_bitmap_font()
    bmf = BitmapFont(npz_path)
    cell = int(bmf.advance(cap)) + 6
    rows = [
        "ABCDEFGHIJKLM",
        "NOPQRSTUVWXYZ",
        "abcdefghijklm",
        "nopqrstuvwxyz",
        "0123456789$.,",
        "!\"#%&'()*+-/:",
        ";<=>?@[\\]^_`{",
        "|}~",
    ]
    height = len(rows) * cap * 2 + 20
    width = 13 * cell + 20
    img = Image.new("L", (width, height), 255)
    for r, row in enumerate(rows):
        base = 20 + r * cap * 2 + cap
        for c, ch in enumerate(row):
            g = bmf.glyph(ch, cap)
            if g is None:
                continue
            pil, h, off = g[0], g[1], g[2]
            arr = np.asarray(pil)
            mask = Image.fromarray(((arr > 0) * 255).astype(np.uint8))
            img.paste(0, (10 + c * cell, base - h + int(off)), mask)
    img.resize((width * 2, height * 2), Image.NEAREST).save(out_png)


def strips(
    samples: str, font_dir: str, report_dir: str, scale: int = 2
) -> list[str]:
    """Compare strips in 12-char batches named by codepoint range (case-safe)."""
    from . import compare as _compare

    out = []
    for i in range(0, len(ASCII), 12):
        batch = ASCII[i : i + 12]
        path = os.path.join(
            report_dir, f"strip_{ord(batch[0]):03d}-{ord(batch[-1]):03d}.png"
        )
        _compare.main(
            [
                samples,
                font_dir,
                path,
                f"--chars={''.join(batch)}",
                "--scale",
                str(scale),
            ]
        )
        out.append(path)
    return out


def mint(
    samples: str,
    font_dir: str,
    *,
    donor: str | None,
    fix: str,
    handcraft_chars: str | None,
    min_samples: int,
    out_npz: str,
    report_dir: str | None,
    fonts_root: str,
) -> dict[str, Any]:
    os.environ["GLYPH_CORPUS_NAME"] = _trace.corpus_label(samples)
    _trace.main([samples, font_dir])

    _simplify.SAMPLES_PATH = samples
    from io import StringIO

    buf, old = StringIO(), sys.stdout
    sys.stdout = buf
    try:
        _simplify.main([font_dir, "--apply", "--json"])
    finally:
        sys.stdout = old
    verdicts = {
        r["char"]: (r.get("gate") or {}).get("verdict")
        for r in json.loads(buf.getvalue())["results"]
    }

    glyphs = load_glyphs(font_dir)
    present = {chr(cp) for cp in glyphs}
    thin = set()
    for cp in list_codepoints(samples):
        stack = load_stack(samples, cp)
        if (
            stack is not None
            and 0 < len(stack) < min_samples
            and chr(cp) in present
        ):
            thin.add(chr(cp))
    # A simplify "reject" only means the node-pruned candidate was not
    # applied; the raw trace stays and is usually fine. It is reported, not
    # re-authored.
    rejected = {ch for ch, v in verdicts.items() if v in ("reject", "error")}
    missing = set(ASCII) - present
    wanted = missing | thin | set(fix)

    if handcraft_chars is not None:
        hand_pool = set(handcraft_chars)
    elif donor:
        hand_pool = HANDCRAFT_PREFERRED & set(_handcraft.DEFAULT_W)
    else:
        hand_pool = set(_handcraft.DEFAULT_W)
    to_hand = "".join(sorted(wanted & hand_pool))
    handcrafted: list[str] = []
    if to_hand:
        _handcraft.main([samples, font_dir, "--chars", to_hand])
        handcrafted = list(to_hand)

    leftover = wanted - set(handcrafted)
    adopted: list[str] = []
    if donor and leftover:
        traced_band = (
            cap_band(load_glyphs(font_dir), only_traced=True)
            or DEFAULT_TARGET_BAND
        )
        adopted = adopt_donor(
            os.path.join(fonts_root, donor),
            font_dir,
            "".join(sorted(leftover)),
            target_band=traced_band,
            note=(
                f"Corpus too thin/polluted for this char; skeleton adopted from the "
                f"{donor} face (y-remapped onto this font's cap band). Replace with a "
                f"trace once more receipts exist."
            ),
        )
    still_missing = set(ASCII) - {chr(cp) for cp in load_glyphs(font_dir)}

    extent = cell_extent(load_glyphs(font_dir))
    squeezed = squeeze_wide(font_dir, extent, skip=set()) if extent else []

    report = compile_font(font_dir, out_npz)
    final = load_glyphs(font_dir)
    traced_now = sorted(
        chr(cp) for cp, g in final.items() if g.get("provenance") == "traced"
    )
    summary = {
        "traced": traced_now,
        "simplify_gate_rejected": sorted(rejected),
        "thin_corpus": sorted(thin),
        "handcrafted": handcrafted,
        "adopted_from_donor": adopted,
        "squeezed": squeezed,
        "still_missing": sorted(still_missing),
        "coverage": report["coverage"],
        "cap_height_deviations": report["cap_height_deviations"],
        "clamp_width_warnings": report["clamp_width_warnings"],
        "advance_ratio": report["advance_ratio"],
        "cap_h": report["cap_h"],
        "npz": out_npz,
    }
    if report_dir:
        os.makedirs(report_dir, exist_ok=True)
        specimen(out_npz, os.path.join(report_dir, "specimen.png"))
        summary["strips"] = strips(samples, font_dir, report_dir)
        summary["specimen"] = os.path.join(report_dir, "specimen.png")
        atomic_write_json(os.path.join(report_dir, "mint.json"), summary)
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument(
        "samples", help="refined *.npz corpus (glyphstudio.refine output)"
    )
    ap.add_argument("font_dir")
    ap.add_argument(
        "--donor",
        help="sibling font dir name under fonts/ to adopt leftovers from",
    )
    ap.add_argument(
        "--fix",
        default="",
        help="chars the human triage rejected: re-author from handcraft/donor",
    )
    ap.add_argument(
        "--handcraft",
        default=None,
        help="restrict hand-authoring to these chars ('' disables)",
    )
    ap.add_argument(
        "--min-samples",
        type=int,
        default=6,
        help="traced chars with fewer refined samples are re-authored",
    )
    ap.add_argument("--out-npz", default=None)
    ap.add_argument(
        "--report-dir",
        default=None,
        help="specimen.png + compare strips + mint.json",
    )
    ap.add_argument(
        "--fonts-root", default=FONTS_DIR, help="where --donor font dirs live"
    )
    args = ap.parse_args(argv)

    font_dir = os.path.abspath(args.font_dir)
    fonts_root = args.fonts_root
    name = os.path.basename(font_dir)
    out_npz = args.out_npz or os.path.join(
        os.path.dirname(os.path.abspath(args.samples)), f"{name}.glyphs.npz"
    )
    summary = mint(
        args.samples,
        font_dir,
        donor=args.donor,
        fix=args.fix,
        handcraft_chars=args.handcraft,
        min_samples=args.min_samples,
        out_npz=out_npz,
        report_dir=args.report_dir,
        fonts_root=fonts_root,
    )
    font = load_font(font_dir)
    print(
        f"mint {name}: coverage {summary['coverage']}/94  cap_h={summary['cap_h']:.1f}  advance_ratio={summary['advance_ratio']:.3f}  weight={font['params'].get('weight')}"
    )
    print(f"  traced {len(summary['traced'])}: {''.join(summary['traced'])}")
    print(
        f"  thin(<{args.min_samples} samples): {''.join(summary['thin_corpus']) or '-'}   simplify kept raw trace for: {''.join(summary['simplify_gate_rejected']) or '-'}"
    )
    print(f"  handcrafted: {''.join(summary['handcrafted']) or '-'}")
    print(
        f"  donor({args.donor}): {''.join(summary['adopted_from_donor']) or '-'}"
    )
    print(f"  squeezed to cell: {''.join(summary['squeezed']) or '-'}")
    if summary["still_missing"]:
        print(f"  STILL MISSING: {''.join(summary['still_missing'])}")
    if summary["cap_height_deviations"]:
        print(f"  CAP-HEIGHT DEVIATIONS: {summary['cap_height_deviations']}")
    if summary["clamp_width_warnings"]:
        print(f"  clamp-width warnings: {summary['clamp_width_warnings']}")
    if args.report_dir:
        print(
            f"  review: {summary['specimen']} + {len(summary['strips'])} strips -> rerun with --fix \"<chars>\""
        )
    return 0 if not summary["still_missing"] else 1


if __name__ == "__main__":
    sys.exit(main())
