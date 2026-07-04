"""Hand-authored skeletons for glyphs whose consensus is a jitter cloud.

The diagonals (K M N V W X Y, v w x y z) blur into smoke when 140 jittery
thermal prints are averaged — tracing the blur yields scrambled topology.
These letterforms are authored as clean parametric skeletons instead, sized
per character from the SOFT consensus envelope (ink bbox width, x-height),
which is exactly what the stroke-skeleton font model is for.

Usage:
  python -m glyphstudio.handcraft <samples.npz> <font_dir> [--chars "KMN"]

Writes glyphs with provenance "edited" so re-traces never clobber them.
"""
from __future__ import annotations

import argparse
import sys

import numpy as np

from . import CAP_UNITS
from .samples import canvas_geometry, consensus_soft, load_stack
from .schema import atomic_write_json, font_dir_paths, glyph_filename

R = 50.0          # dot radius in cap units (dot.size 100)
CAP = CAP_UNITS - R   # centerline cap line (ink cap = 1000)
BASE = R              # centerline baseline
XH = 700.0 - R        # centerline x-height line (ink x-height ~ 700)
DESC = -320.0 + R     # centerline descender floor
KAPPA = 0.5523        # circle-approximation handle factor


def node(x, y, h_in=None, h_out=None, smooth=False):
    n = {"x": round(x, 1), "y": round(y, 1),
         "type": "smooth" if smooth else "corner"}
    if h_in is not None:
        n["hIn"] = {"x": round(h_in[0], 1), "y": round(h_in[1], 1)}
    if h_out is not None:
        n["hOut"] = {"x": round(h_out[0], 1), "y": round(h_out[1], 1)}
    return n


def line(*pts):
    return {"closed": False, "nodes": [node(x, y) for x, y in pts]}


def circle(cx, cy, rx, ry=None):
    """Closed 4-node cubic circle/ellipse."""
    ry = rx if ry is None else ry
    kx, ky = KAPPA * rx, KAPPA * ry
    return {"closed": True, "nodes": [
        node(cx, cy + ry, h_in=(cx - kx, cy + ry), h_out=(cx + kx, cy + ry), smooth=True),
        node(cx + rx, cy, h_in=(cx + rx, cy + ky), h_out=(cx + rx, cy - ky), smooth=True),
        node(cx, cy - ry, h_in=(cx + kx, cy - ry), h_out=(cx - kx, cy - ry), smooth=True),
        node(cx - rx, cy, h_in=(cx - rx, cy - ky), h_out=(cx - rx, cy + ky), smooth=True),
    ]}


def soft_ink_width(samples_path: str, cp: int, default: float) -> float:
    """Ink bbox width (cap units) from the soft consensus envelope."""
    stack = load_stack(samples_path, cp)
    if stack is None or not len(stack):
        return default
    frac = consensus_soft(stack)
    ref_cap, _ = canvas_geometry(stack.shape[1])
    colmax = frac.max(axis=0)
    cols = np.where(colmax >= 0.30)[0]
    if len(cols) < 2:
        return default
    width_px = float(cols[-1] - cols[0] + 1)
    return width_px / ref_cap * CAP_UNITS


def specs(W: float) -> dict:
    """char -> strokes, given ink width W (centerline span = W - 2R)."""
    L = R                # left centerline
    Rt = W - R           # right centerline
    M = W / 2.0          # mid
    return {
        "K": [line((L, BASE), (L, CAP)),
              line((Rt, CAP), (L + 30, 470)),
              line((L + 30, 470), (Rt, BASE))],
        "M": [line((L, BASE), (L, CAP)),
              line((L, CAP), (M, 400)),
              line((M, 400), (Rt, CAP)),
              line((Rt, CAP), (Rt, BASE))],
        "N": [line((L, BASE), (L, CAP)),
              line((L, CAP), (Rt, BASE)),
              line((Rt, BASE), (Rt, CAP))],
        "V": [line((L, CAP), (M, BASE)),
              line((M, BASE), (Rt, CAP))],
        "W": [line((L, CAP), (L + (M - L) * 0.55, BASE)),
              line((L + (M - L) * 0.55, BASE), (M, 560)),
              line((M, 560), (Rt - (Rt - M) * 0.55, BASE)),
              line((Rt - (Rt - M) * 0.55, BASE), (Rt, CAP))],
        "X": [line((L, BASE), (Rt, CAP)),
              line((L, CAP), (Rt, BASE))],
        "Y": [line((L, CAP), (M, 470)),
              line((Rt, CAP), (M, 470)),
              line((M, 470), (M, BASE))],
        "v": [line((L, XH), (M, BASE)),
              line((M, BASE), (Rt, XH))],
        "w": [line((L, XH), (L + (M - L) * 0.55, BASE)),
              line((L + (M - L) * 0.55, BASE), (M, 400)),
              line((M, 400), (Rt - (Rt - M) * 0.55, BASE)),
              line((Rt - (Rt - M) * 0.55, BASE), (Rt, XH))],
        "x": [line((L, BASE), (Rt, XH)),
              line((L, XH), (Rt, BASE))],
        "y": [line((L, XH), (M, 60)),
              line((Rt, XH), (M + (L - Rt) * 0.18, DESC))],
        "z": [line((L, XH), (Rt, XH)),
              line((Rt, XH), (L, BASE)),
              line((L, BASE), (Rt, BASE))],
        "k": [line((L, BASE), (L, CAP + 30)),
              line((Rt - 20, XH), (L + 20, 380)),
              line((L + 20, 380), (Rt, BASE))],
        "%": [circle(L + 110, 760, 110, 105),
              line((L + 40, 80), (Rt - 40, 920)),
              circle(Rt - 110, 240, 110, 105)],
        "#": [line((M - 110, BASE + 60), (M - 60, CAP - 60)),
              line((M + 60, BASE + 60), (M + 110, CAP - 60)),
              line((L - 10, 620), (Rt + 10, 640)),
              line((L - 10, 340), (Rt + 10, 360))],
        '"': [line((M - 90, CAP), (M - 100, CAP - 240)),
              line((M + 90, CAP), (M + 100, CAP - 240))],
        "^": [line((L + 40, 640), (M, CAP)),
              line((M, CAP), (Rt - 40, 640))],
        "`": [line((M - 50, CAP), (M + 50, CAP - 200))],
        ";": [circle(M, 560, 42, 42),
              line((M + 15, 160), (M - 55, -120))],
        "?": [{"closed": False, "nodes": [
                  node(L, 760, h_out=(L, 940), smooth=True),
                  node(M, CAP, h_in=(M - 130, CAP), h_out=(M + 130, CAP), smooth=True),
                  node(Rt, 760, h_in=(Rt, 940), h_out=(Rt, 580), smooth=True),
                  node(M, 420, h_in=(M + 110, 500)),
                  node(M, 300),
              ]},
              circle(M, 60, 40, 40)],
        "o": [circle(M, (XH + BASE) / 2.0, Rt - M, (XH - BASE) / 2.0)],
        "e": [circle(M, (XH + BASE) / 2.0, Rt - M, (XH - BASE) / 2.0),
              line((L - 10, 390), (Rt + 10, 390))],
        "a": [circle(M, (XH + BASE) / 2.0 - 20, Rt - M, (XH - BASE) / 2.0 - 20),
              line((L + 30, XH), (Rt, XH)),
              line((Rt, XH), (Rt, BASE))],
        "u": [{"closed": False, "nodes": [
                  node(L, XH),
                  node(L, 220, h_out=(L, 90)),
                  node(M, BASE, h_in=(M - 120, BASE), h_out=(M + 120, BASE), smooth=True),
                  node(Rt, 220, h_in=(Rt, 90)),
                  node(Rt, XH),
              ]}],
        "h": [line((L, BASE), (L, CAP + 30)),
              {"closed": False, "nodes": [
                  node(L, 420, h_out=(L, 570)),
                  node(M, XH, h_in=(M - 110, XH), h_out=(M + 110, XH), smooth=True),
                  node(Rt, 420, h_in=(Rt, 570)),
                  node(Rt, BASE),
              ]}],
        "i": [circle(M, 830, 40, 40),
              line((M, BASE), (M, XH))],
        "{": [{"closed": False, "nodes": [
                  node(Rt - 20, CAP, h_out=(M - 60, CAP)),
                  node(M - 20, 780, h_in=(M - 20, 900), smooth=True),
                  node(M - 30, 560, h_out=(M - 30, 480)),
                  node(L, 500),
                  node(M - 30, 440, h_in=(M - 30, 480)),
                  node(M - 20, 220, h_out=(M - 20, 100), smooth=True),
                  node(Rt - 20, BASE, h_in=(M - 60, BASE)),
              ]}],
        "}": [{"closed": False, "nodes": [
                  node(L + 20, CAP, h_out=(M + 60, CAP)),
                  node(M + 20, 780, h_in=(M + 20, 900), smooth=True),
                  node(M + 30, 560, h_out=(M + 30, 480)),
                  node(Rt, 500),
                  node(M + 30, 440, h_in=(M + 30, 480)),
                  node(M + 20, 220, h_out=(M + 20, 100), smooth=True),
                  node(L + 20, BASE, h_in=(M + 60, BASE)),
              ]}],
    }


# Default ink widths (cap units) when a char has no corpus samples.
DEFAULT_W = {"K": 560, "M": 640, "N": 580, "V": 580, "W": 660, "X": 560,
             "Y": 560, "v": 500, "w": 620, "x": 500, "y": 500, "z": 480,
             "k": 520, "%": 620, "#": 560, '"': 320, "^": 460, "`": 220,
             ";": 220, "?": 460, "{": 340, "}": 340,
             "o": 500, "e": 500, "a": 500, "u": 500, "h": 520, "i": 220}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("samples")
    ap.add_argument("font_dir")
    ap.add_argument("--chars", default="".join(DEFAULT_W))
    args = ap.parse_args(argv)

    paths = font_dir_paths(args.font_dir)
    written = []
    for ch in args.chars:
        if ch not in DEFAULT_W:
            print(f"no spec for {ch!r}, skipping")
            continue
        W = soft_ink_width(args.samples, ord(ch), DEFAULT_W[ch])
        W = max(200.0, min(760.0, W))
        glyph = {
            "version": 1,
            "char": ch,
            "codepoint": ord(ch),
            "provenance": "edited",
            "width": round(W, 1),
            "baselineNudgePx": 0,
            "overrides": {},
            "strokes": specs(W)[ch],
        }
        target = f"{paths['glyphs']}/{glyph_filename(ord(ch))}"
        atomic_write_json(target, glyph)
        written.append(ch)
    print(f"hand-authored: {''.join(written)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
