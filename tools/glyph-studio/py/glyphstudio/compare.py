"""Visual perfection loop: soft-consensus vs compiled, magnified strips.

Usage:
  python -m glyphstudio.compare <samples.npz> <font_dir> <out.png> \
      [--chars "KMNVWX"] [--scale 4]

Per char, one row: [soft consensus | compiled bitmap | red/green overlay].
Overlay: compiled ink tinted blue over the soft map in red — misalignment
and weight mismatch read instantly. This is the CLI equivalent of the
editor's onion-skin for batch review.
"""
from __future__ import annotations

import argparse
import sys

import numpy as np
from PIL import Image, ImageDraw

from .raster import rasterize_glyph
from .samples import canvas_geometry, consensus_soft, load_stack
from .schema import load_font, load_glyphs, merged_params


def _cell(soft: np.ndarray | None, comp: np.ndarray | None,
          comp_off: int, ref_cap_s: int, baseline_s: int,
          cap_ref: int, scale: int) -> Image.Image:
    """One char row: consensus | compiled | overlay, baseline-aligned."""
    ch_h, ch_w = 3 * cap_ref, 2 * cap_ref
    base_row = int(cap_ref * 2.3)

    def canvas():
        return np.zeros((ch_h, ch_w), dtype=float)

    # resample soft map onto the cap_ref grid
    soft_c = canvas()
    if soft is not None:
        img = Image.fromarray((soft * 255).astype(np.uint8)).resize(
            (int(round(soft.shape[1] * cap_ref / ref_cap_s)),
             int(round(soft.shape[0] * cap_ref / ref_cap_s))),
            Image.BILINEAR)
        arr = np.asarray(img).astype(float) / 255.0
        src_base = int(round(baseline_s * cap_ref / ref_cap_s))
        y0 = base_row - src_base
        x0 = (ch_w - arr.shape[1]) // 2
        ys, xs = max(0, y0), max(0, x0)
        ye = min(ch_h, y0 + arr.shape[0])
        xe = min(ch_w, x0 + arr.shape[1])
        soft_c[ys:ye, xs:xe] = arr[ys - y0: ye - y0, xs - x0: xe - x0]

    comp_c = canvas()
    if comp is not None:
        h, w = comp.shape
        y1 = base_row + comp_off
        y0 = y1 - h
        x0 = (ch_w - w) // 2
        ys, xs = max(0, y0), max(0, x0)
        ye, xe = min(ch_h, y0 + h), min(ch_w, x0 + w)
        comp_c[ys:ye, xs:xe] = comp[ys - y0: ye - y0, xs - x0: xe - x0]

    cells = []
    for mode in ("soft", "comp", "overlay"):
        rgb = np.full((ch_h, ch_w, 3), 255, dtype=np.uint8)
        if mode == "soft":
            v = (255 - soft_c * 255).astype(np.uint8)
            rgb = np.stack([v, v, v], axis=2)
        elif mode == "comp":
            v = (255 - comp_c * 255).astype(np.uint8)
            rgb = np.stack([v, v, v], axis=2)
        else:
            # consensus in red channel-off, compiled in blue channel-off
            rgb[..., 1] = (255 * (1 - np.maximum(soft_c * 0.9, comp_c))).astype(np.uint8)
            rgb[..., 2] = (255 * (1 - soft_c * 0.9)).astype(np.uint8)
            rgb[..., 0] = (255 * (1 - comp_c)).astype(np.uint8)
        img = Image.fromarray(rgb).resize((ch_w * scale, ch_h * scale),
                                          Image.NEAREST)
        d = ImageDraw.Draw(img)
        by = (base_row + 0.5) * scale
        d.line([(0, by), (img.width, by)], fill=(180, 180, 180), width=1)
        cy = (base_row - cap_ref + 0.5) * scale
        d.line([(0, cy), (img.width, cy)], fill=(210, 210, 210), width=1)
        cells.append(img)
    strip = Image.new("RGB", (sum(c.width for c in cells) + 2 * len(cells),
                              cells[0].height), (140, 140, 140))
    x = 0
    for c in cells:
        strip.paste(c, (x, 0))
        x += c.width + 2
    return strip


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("samples")
    ap.add_argument("font_dir")
    ap.add_argument("out")
    ap.add_argument("--chars", default=None)
    ap.add_argument("--scale", type=int, default=4)
    args = ap.parse_args(argv)

    font = load_font(args.font_dir)
    glyphs = load_glyphs(args.font_dir)
    ref_cap = int(font.get("refCap", 60))
    chars = args.chars or "".join(sorted(chr(cp) for cp in glyphs))

    rows = []
    for ch in chars:
        cp = ord(ch)
        stack = load_stack(args.samples, cp)
        soft = None
        ref_cap_s, baseline_s = ref_cap, int(ref_cap * 2.3)
        if stack is not None and len(stack):
            soft = consensus_soft(stack)
            ref_cap_s, baseline_s = canvas_geometry(stack.shape[1])
        comp, off = (None, 0)
        if cp in glyphs:
            comp_arr, off = rasterize_glyph(
                glyphs[cp], merged_params(font, glyphs[cp]), ref_cap)
            comp = comp_arr.astype(float)
        row = _cell(soft, comp, off, ref_cap_s, baseline_s, ref_cap,
                    args.scale)
        labeled = Image.new("RGB", (row.width + 40 * args.scale, row.height),
                            (255, 255, 255))
        labeled.paste(row, (40 * args.scale, 0))
        d = ImageDraw.Draw(labeled)
        d.text((6, row.height // 2 - 10), ch, fill=(200, 30, 30))
        rows.append(labeled)

    total = Image.new("RGB", (max(r.width for r in rows),
                              sum(r.height + 2 for r in rows)), (120, 120, 120))
    y = 0
    for r in rows:
        total.paste(r, (0, y))
        y += r.height + 2
    total.save(args.out)
    print(f"wrote {args.out}: {len(rows)} chars  [soft | compiled | overlay]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
