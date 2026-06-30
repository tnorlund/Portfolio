#!/usr/bin/env python3
"""font_match.py -- rank fonts against a cached glyph fingerprint + per-character WHY.

Loads a merchant zone's cached prototypes (body_protos.npz from
glyph_prototype_dynamo), ranks every font in the library (incl. emboldened
variants), and -- the part you asked for -- reports WHICH characters don't align
and WHY, by localizing where the real-ink and font-ink disagree.

Usage: font_match.py <out_dir> [worst_n]
Output: <out_dir>/font_match_diff.png (worst chars: prototype | best font | overlay)
"""
from __future__ import annotations

import os
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from glyph_prototype import (  # noqa: E402
    BOX, GH, CHARS, _font_glyph, _sim, font_candidates, _all_font_paths,
)

ROWS = ["top", "mid", "bottom"]
COLS = ["left", "center", "right"]


def _why(P, F):
    """Localize where real-ink (prototype) and font-ink disagree -> a text reason."""
    p, f = P > 0.4, F > 0.4
    H, W = P.shape

    def grid(mask):
        g = np.zeros((3, 3))
        for i in range(3):
            for j in range(3):
                g[i, j] = mask[i * H // 3:(i + 1) * H // 3, j * W // 3:(j + 1) * W // 3].sum()
        return g

    p_only, f_only = p & ~f, f & ~p
    notes = []
    if p_only.sum() > p.sum() * 0.08:
        i, j = np.unravel_index(grid(p_only).argmax(), (3, 3))
        notes.append(f"real has a stroke {ROWS[i]}-{COLS[j]} the font lacks")
    if f_only.sum() > f.sum() * 0.08:
        i, j = np.unravel_index(grid(f_only).argmax(), (3, 3))
        notes.append(f"font adds ink {ROWS[i]}-{COLS[j]}")
    # global width hint
    pw = p.any(0).sum(); fw = f.any(0).sum()
    if fw and pw:
        r = fw / pw
        if r > 1.18:
            notes.append("font too wide")
        elif r < 0.85:
            notes.append("font too narrow")
    return "; ".join(notes) or "close"


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 2
    out_dir = sys.argv[1]
    worst_n = int(sys.argv[2]) if len(sys.argv) > 2 else 12
    data = np.load(os.path.join(out_dir, "body_protos.npz"))
    protos = {k: data[k] for k in data.files}
    print(f"{len(protos)} prototype chars; {len(_all_font_paths())} fonts in library")

    cands = font_candidates()
    # rank fonts (mean shape similarity across chars), keep per-char sims for the winner
    scored = []
    for label, font, stroke in cands:
        per = {ch: _sim(p, _font_glyph(font, ch, stroke)) for ch, p in protos.items()
               if _font_glyph(font, ch, stroke) is not None}
        if per:
            scored.append((label, float(np.mean(list(per.values()))), font, stroke, per))
    scored.sort(key=lambda t: -t[1])

    print("\n=== TOP FONTS ===")
    for label, s, *_ in scored[:10]:
        print(f"  {label:22} {s:.3f}")

    best_label, best_s, best_font, best_stroke, best_per = scored[0]
    print(f"\n=== WORST CHARS for {best_label} ({best_s:.3f}) + why ===")
    worst = sorted(best_per.items(), key=lambda kv: kv[1])[:worst_n]
    # for each worst char, also which font matches IT best (discriminating)
    for ch, sim in worst:
        best_for_ch = max(scored, key=lambda t: t[4].get(ch, -1))
        P = protos[ch]
        F = _font_glyph(best_font, ch, best_stroke)
        reason = _why(P, F)
        print(f"  {ch!r}: {sim:.2f}  | {reason:48} | best-for-{ch}: "
              f"{best_for_ch[0]} ({best_for_ch[4].get(ch,0):.2f})")

    # diff contact sheet: prototype | best font | overlay (green=real-only, red=font-only)
    cols = [ch for ch, _ in worst]
    sh = Image.new("RGB", (len(cols) * BOX, BOX * 3 + 16), (235, 235, 235))
    for i, ch in enumerate(cols):
        P = protos[ch]
        F = _font_glyph(best_font, ch, best_stroke)
        sh.paste(Image.fromarray((P * 255).astype(np.uint8)).convert("RGB"), (i * BOX, 0))
        sh.paste(Image.fromarray((F * 255).astype(np.uint8)).convert("RGB"), (i * BOX, BOX))
        ov = np.zeros((BOX, BOX, 3), np.uint8)
        p, f = P > 0.4, F > 0.4
        ov[p & f] = (255, 255, 255)
        ov[p & ~f] = (40, 220, 40)   # real-only
        ov[f & ~p] = (230, 40, 40)   # font-only
        sh.paste(Image.fromarray(ov), (i * BOX, BOX * 2))
    ImageDraw.Draw(sh).text((2, BOX * 3 + 2),
                            "rows: prototype / best-font / overlay (green=real-only red=font-only)",
                            fill=(0, 0, 0))
    out = os.path.join(out_dir, "font_match_diff.png")
    sh.save(out)
    print(f"\ndiff sheet -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
