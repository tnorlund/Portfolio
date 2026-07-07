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

R = 50.0  # dot radius in cap units (dot.size 100)
CAP = CAP_UNITS - R  # centerline cap line (ink cap = 1000)
BASE = R  # centerline baseline
XH = 700.0 - R  # centerline x-height line (ink x-height ~ 700)
DESC = -320.0 + R  # centerline descender floor
KAPPA = 0.5523  # circle-approximation handle factor


def node(x, y, h_in=None, h_out=None, smooth=False):
    n = {
        "x": round(x, 1),
        "y": round(y, 1),
        "type": "smooth" if smooth else "corner",
    }
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
    return {
        "closed": True,
        "nodes": [
            node(
                cx,
                cy + ry,
                h_in=(cx - kx, cy + ry),
                h_out=(cx + kx, cy + ry),
                smooth=True,
            ),
            node(
                cx + rx,
                cy,
                h_in=(cx + rx, cy + ky),
                h_out=(cx + rx, cy - ky),
                smooth=True,
            ),
            node(
                cx,
                cy - ry,
                h_in=(cx + kx, cy - ry),
                h_out=(cx - kx, cy - ry),
                smooth=True,
            ),
            node(
                cx - rx,
                cy,
                h_in=(cx - rx, cy - ky),
                h_out=(cx - rx, cy + ky),
                smooth=True,
            ),
        ],
    }


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
    L = R  # left centerline
    Rt = W - R  # right centerline
    M = W / 2.0  # mid
    return {
        "K": [
            line((L, BASE), (L, CAP)),
            line((Rt, CAP), (L + 30, 470)),
            line((L + 30, 470), (Rt, BASE)),
        ],
        "M": [
            line((L, BASE), (L, CAP)),
            line((L, CAP), (M, 400)),
            line((M, 400), (Rt, CAP)),
            line((Rt, CAP), (Rt, BASE)),
        ],
        "N": [
            line((L, BASE), (L, CAP)),
            line((L, CAP), (Rt, BASE)),
            line((Rt, BASE), (Rt, CAP)),
        ],
        "V": [line((L, CAP), (M, BASE)), line((M, BASE), (Rt, CAP))],
        "W": [
            line((L, CAP), (L + (M - L) * 0.55, BASE)),
            line((L + (M - L) * 0.55, BASE), (M, 560)),
            line((M, 560), (Rt - (Rt - M) * 0.55, BASE)),
            line((Rt - (Rt - M) * 0.55, BASE), (Rt, CAP)),
        ],
        "X": [line((L, BASE), (Rt, CAP)), line((L, CAP), (Rt, BASE))],
        "Y": [
            line((L, CAP), (M, 470)),
            line((Rt, CAP), (M, 470)),
            line((M, 470), (M, BASE)),
        ],
        "D": [
            line((L, BASE), (L, CAP)),
            {
                "closed": False,
                "nodes": [
                    node(L, CAP),
                    node(Rt, 790, h_in=(Rt, CAP), h_out=(Rt, 610), smooth=True),
                    node(Rt, 260, h_in=(Rt, 390), h_out=(Rt, BASE), smooth=True),
                    node(L, BASE),
                ],
            },
        ],
        "H": [
            line((L, BASE), (L, CAP)),
            line((Rt, BASE), (Rt, CAP)),
            line((L, 500), (Rt, 500)),
        ],
        "F": [
            line((L, BASE), (L, CAP)),
            line((L, CAP), (Rt, CAP)),
            line((L, 540), (Rt - 40, 540)),
        ],
        "8": [
            circle(M, 715, Rt - M, 245),
            circle(M, 285, Rt - M, 245),
        ],
        "v": [line((L, XH), (M, BASE)), line((M, BASE), (Rt, XH))],
        "w": [
            line((L, XH), (L + (M - L) * 0.55, BASE)),
            line((L + (M - L) * 0.55, BASE), (M, 400)),
            line((M, 400), (Rt - (Rt - M) * 0.55, BASE)),
            line((Rt - (Rt - M) * 0.55, BASE), (Rt, XH)),
        ],
        "x": [line((L, BASE), (Rt, XH)), line((L, XH), (Rt, BASE))],
        "y": [
            line((L, XH), (M, 60)),
            line((Rt, XH), (M + (L - Rt) * 0.18, DESC)),
        ],
        "z": [
            line((L, XH), (Rt, XH)),
            line((Rt, XH), (L, BASE)),
            line((L, BASE), (Rt, BASE)),
        ],
        "k": [
            line((L, BASE), (L, CAP + 30)),
            line((Rt - 20, XH), (L + 20, 380)),
            line((L + 20, 380), (Rt, BASE)),
        ],
        "%": [
            circle(L + 110, 760, 110, 105),
            line((L + 40, 80), (Rt - 40, 920)),
            circle(Rt - 110, 240, 110, 105),
        ],
        "#": [
            line((M - 110, BASE + 60), (M - 60, CAP - 60)),
            line((M + 60, BASE + 60), (M + 110, CAP - 60)),
            line((L - 10, 620), (Rt + 10, 640)),
            line((L - 10, 340), (Rt + 10, 360)),
        ],
        '"': [
            line((M - 90, CAP), (M - 100, CAP - 240)),
            line((M + 90, CAP), (M + 100, CAP - 240)),
        ],
        "'": [line((M, CAP), (M - 15, CAP - 240))],
        "(": [
            {
                "closed": False,
                "nodes": [
                    node(Rt - 20, CAP, h_out=(L + 20, 780)),
                    node(L, 500, h_in=(L, 760), h_out=(L, 240), smooth=True),
                    node(Rt - 20, BASE, h_in=(L + 20, 220)),
                ],
            }
        ],
        ")": [
            {
                "closed": False,
                "nodes": [
                    node(L + 20, CAP, h_out=(Rt - 20, 780)),
                    node(Rt, 500, h_in=(Rt, 760), h_out=(Rt, 240), smooth=True),
                    node(L + 20, BASE, h_in=(Rt - 20, 220)),
                ],
            }
        ],
        ",": [circle(M, 70, 38, 38), line((M + 25, 45), (M - 40, -140))],
        "^": [line((L + 40, 640), (M, CAP)), line((M, CAP), (Rt - 40, 640))],
        "`": [line((M - 50, CAP), (M + 50, CAP - 200))],
        ";": [circle(M, 560, 42, 42), line((M + 15, 160), (M - 55, -120))],
        "<": [line((Rt - 20, 720), (L + 20, 500)), line((L + 20, 500), (Rt - 20, 280))],
        "=": [line((L + 10, 620), (Rt - 10, 620)), line((L + 10, 380), (Rt - 10, 380))],
        ">": [line((L + 20, 720), (Rt - 20, 500)), line((Rt - 20, 500), (L + 20, 280))],
        "?": [
            {
                "closed": False,
                "nodes": [
                    node(L, 760, h_out=(L, 940), smooth=True),
                    node(
                        M,
                        CAP,
                        h_in=(M - 130, CAP),
                        h_out=(M + 130, CAP),
                        smooth=True,
                    ),
                    node(
                        Rt, 760, h_in=(Rt, 940), h_out=(Rt, 580), smooth=True
                    ),
                    node(M, 420, h_in=(M + 110, 500)),
                    node(M, 300),
                ],
            },
            circle(M, 60, 40, 40),
        ],
        "@": [
            circle(M, 500, Rt - M, 420),
            circle(M + 35, 450, max(90, (Rt - M) * 0.38), 150),
            line((M + 125, 580), (M + 155, 310)),
        ],
        "[": [
            line((Rt - 20, CAP), (L + 20, CAP)),
            line((L + 20, CAP), (L + 20, BASE)),
            line((L + 20, BASE), (Rt - 20, BASE)),
        ],
        "\\": [line((L + 20, CAP), (Rt - 20, BASE))],
        "]": [
            line((L + 20, CAP), (Rt - 20, CAP)),
            line((Rt - 20, CAP), (Rt - 20, BASE)),
            line((Rt - 20, BASE), (L + 20, BASE)),
        ],
        "_": [line((L + 10, -120), (Rt - 10, -120))],
        "~": [
            {
                "closed": False,
                "nodes": [
                    node(L, 500, h_out=(L + 80, 620)),
                    node(M, 500, h_in=(M - 100, 650), h_out=(M + 100, 350), smooth=True),
                    node(Rt, 500, h_in=(Rt - 80, 380)),
                ],
            }
        ],
        "O": [circle(M, (CAP + BASE) / 2.0, Rt - M, (CAP - BASE) / 2.0)],
        "Q": [
            circle(M, (CAP + BASE) / 2.0, Rt - M, (CAP - BASE) / 2.0),
            line((M + 60, 300), (Rt + 20, BASE - 20)),
        ],
        "U": [
            {
                "closed": False,
                "nodes": [
                    node(L, CAP),
                    node(L, 300, h_out=(L, 120)),
                    node(
                        M,
                        BASE,
                        h_in=(M - 130, BASE),
                        h_out=(M + 130, BASE),
                        smooth=True,
                    ),
                    node(Rt, 300, h_in=(Rt, 120)),
                    node(Rt, CAP),
                ],
            }
        ],
        "R": [
            line((L, BASE), (L, CAP)),
            {
                "closed": False,
                "nodes": [
                    node(L, CAP),
                    node(Rt - 30, 870, h_in=(Rt - 30, CAP), smooth=True),
                    node(Rt - 30, 640, h_out=(Rt - 30, 530)),
                    node(L, 510),
                ],
            },
            line((L + 60, 510), (Rt, BASE)),
        ],
        "P": [
            line((L, BASE), (L, CAP)),
            {
                "closed": False,
                "nodes": [
                    node(L, CAP),
                    node(Rt - 30, 870, h_in=(Rt - 30, CAP), smooth=True),
                    node(Rt - 30, 640, h_out=(Rt - 30, 530)),
                    node(L, 510),
                ],
            },
        ],
        "&": [
            {
                "closed": False,
                "nodes": [
                    node(Rt, 100),
                    node(L + 110, 700, h_in=(M + 40, 380)),
                    node(
                        M - 60,
                        CAP,
                        h_in=(L + 60, 930),
                        h_out=(M + 60, CAP + 20),
                        smooth=True,
                    ),
                    node(M + 90, 720, h_in=(M + 120, 850)),
                    node(L, 300, h_in=(M - 60, 520)),
                    node(
                        M - 40,
                        BASE,
                        h_in=(L, 90),
                        h_out=(M + 60, BASE - 10),
                        smooth=True,
                    ),
                    node(Rt - 30, 280, h_in=(Rt - 90, 90)),
                ],
            }
        ],
        "S": [
            {
                "closed": False,
                "nodes": [
                    node(Rt - 40, 790, h_in=(Rt - 60, 900)),
                    node(
                        M,
                        CAP,
                        h_in=(M + 130, CAP),
                        h_out=(M - 130, CAP),
                        smooth=True,
                    ),
                    node(L, 740, h_in=(L, 860), h_out=(L, 620), smooth=True),
                    node(
                        Rt, 300, h_in=(Rt, 440), h_out=(Rt, 180), smooth=True
                    ),
                    node(
                        M,
                        BASE,
                        h_in=(M + 130, BASE),
                        h_out=(M - 130, BASE),
                        smooth=True,
                    ),
                    node(L + 40, 210, h_in=(L + 60, 100)),
                ],
            }
        ],
        "Z": [
            line((L, CAP), (Rt, CAP)),
            line((Rt, CAP), (L, BASE)),
            line((L, BASE), (Rt, BASE)),
        ],
        "o": [circle(M, (XH + BASE) / 2.0, Rt - M, (XH - BASE) / 2.0)],
        "e": [
            circle(M, (XH + BASE) / 2.0, Rt - M, (XH - BASE) / 2.0),
            line((L - 10, 390), (Rt + 10, 390)),
        ],
        "a": [
            circle(M, (XH + BASE) / 2.0 - 20, Rt - M, (XH - BASE) / 2.0 - 20),
            line((L + 30, XH), (Rt, XH)),
            line((Rt, XH), (Rt, BASE)),
        ],
        "u": [
            {
                "closed": False,
                "nodes": [
                    node(L, XH),
                    node(L, 220, h_out=(L, 90)),
                    node(
                        M,
                        BASE,
                        h_in=(M - 120, BASE),
                        h_out=(M + 120, BASE),
                        smooth=True,
                    ),
                    node(Rt, 220, h_in=(Rt, 90)),
                    node(Rt, XH),
                ],
            }
        ],
        "h": [
            line((L, BASE), (L, CAP + 30)),
            {
                "closed": False,
                "nodes": [
                    node(L, 420, h_out=(L, 570)),
                    node(
                        M,
                        XH,
                        h_in=(M - 110, XH),
                        h_out=(M + 110, XH),
                        smooth=True,
                    ),
                    node(Rt, 420, h_in=(Rt, 570)),
                    node(Rt, BASE),
                ],
            },
        ],
        "b": [
            line((L, BASE), (L, CAP + 30)),
            circle(M, (XH + BASE) / 2.0 - 20, Rt - M, (XH - BASE) / 2.0 - 20),
        ],
        "i": [circle(M, 830, 40, 40), line((M, BASE), (M, XH))],
        "p": [
            line((L, DESC), (L, XH)),
            circle(M, (XH + BASE) / 2.0 + 40, Rt - M, (XH - BASE) / 2.0 - 20),
        ],
        "q": [
            circle(M, (XH + BASE) / 2.0, Rt - M, (XH - BASE) / 2.0),
            line((Rt, XH), (Rt, DESC)),
        ],
        "j": [
            circle(M + 40, 830, 40, 40),
            {
                "closed": False,
                "nodes": [
                    node(M + 40, XH),
                    node(M + 40, DESC + 160, h_out=(M + 40, DESC + 40)),
                    node(M - 60, DESC, h_in=(M + 10, DESC - 20)),
                ],
            },
        ],
        "!": [line((M, CAP), (M, 340)), circle(M, 60, 40, 40)],
        "+": [line((M, 660), (M, 240)), line((L + 10, 450), (Rt - 10, 450))],
        "c": [
            {
                "closed": False,
                "nodes": [
                    node(Rt - 20, XH - 70, h_out=(L + 80, XH + 30)),
                    node(
                        L,
                        (XH + BASE) / 2.0,
                        h_in=(L, XH - 90),
                        h_out=(L, BASE + 90),
                        smooth=True,
                    ),
                    node(Rt - 20, BASE + 70, h_in=(L + 80, BASE - 30)),
                ],
            }
        ],
        "d": [
            line((Rt, BASE), (Rt, CAP + 30)),
            {
                "closed": False,
                "nodes": [
                    node(Rt, XH, h_out=(L + 20, XH)),
                    node(
                        L,
                        (XH + BASE) / 2.0,
                        h_in=(L, XH - 90),
                        h_out=(L, BASE + 90),
                        smooth=True,
                    ),
                    node(Rt, BASE, h_in=(L + 20, BASE)),
                ],
            },
        ],
        "g": [
            circle(M, (XH + BASE) / 2.0, Rt - M, (XH - BASE) / 2.0),
            {
                "closed": False,
                "nodes": [
                    node(Rt, (XH + BASE) / 2.0 - 30),
                    node(
                        Rt,
                        DESC + 120,
                        h_in=(Rt + 10, BASE - 80),
                        h_out=(Rt - 60, DESC - 20),
                    ),
                    node(
                        M,
                        DESC,
                        h_in=(Rt - 80, DESC - 20),
                        h_out=(L + 80, DESC + 10),
                        smooth=True,
                    ),
                    node(L + 60, DESC + 130, h_in=(L + 40, DESC + 40)),
                ],
            },
        ],
        "n": [
            line((L, BASE), (L, XH)),
            {
                "closed": False,
                "nodes": [
                    node(L, 420, h_out=(L, 570)),
                    node(
                        M,
                        XH,
                        h_in=(M - 110, XH),
                        h_out=(M + 110, XH),
                        smooth=True,
                    ),
                    node(Rt, 420, h_in=(Rt, 570)),
                    node(Rt, BASE),
                ],
            },
        ],
        "m": [
            line((L, BASE), (L, XH)),
            {
                "closed": False,
                "nodes": [
                    node(L, 420, h_out=(L, 570)),
                    node(
                        (L + M) / 2.0,
                        XH,
                        h_in=((L + M) / 2.0 - 90, XH),
                        h_out=((L + M) / 2.0 + 70, XH),
                        smooth=True,
                    ),
                    node(M, 420, h_in=(M, 570)),
                    node(M, BASE),
                ],
            },
            {
                "closed": False,
                "nodes": [
                    node(M, 420, h_out=(M, 570)),
                    node(
                        (M + Rt) / 2.0,
                        XH,
                        h_in=((M + Rt) / 2.0 - 70, XH),
                        h_out=((M + Rt) / 2.0 + 90, XH),
                        smooth=True,
                    ),
                    node(Rt, 420, h_in=(Rt, 570)),
                    node(Rt, BASE),
                ],
            },
        ],
        "{": [
            {
                "closed": False,
                "nodes": [
                    node(Rt - 20, CAP, h_out=(M - 60, CAP)),
                    node(M - 20, 780, h_in=(M - 20, 900), smooth=True),
                    node(M - 30, 560, h_out=(M - 30, 480)),
                    node(L, 500),
                    node(M - 30, 440, h_in=(M - 30, 480)),
                    node(M - 20, 220, h_out=(M - 20, 100), smooth=True),
                    node(Rt - 20, BASE, h_in=(M - 60, BASE)),
                ],
            }
        ],
        "}": [
            {
                "closed": False,
                "nodes": [
                    node(L + 20, CAP, h_out=(M + 60, CAP)),
                    node(M + 20, 780, h_in=(M + 20, 900), smooth=True),
                    node(M + 30, 560, h_out=(M + 30, 480)),
                    node(Rt, 500),
                    node(M + 30, 440, h_in=(M + 30, 480)),
                    node(M + 20, 220, h_out=(M + 20, 100), smooth=True),
                    node(L + 20, BASE, h_in=(M + 60, BASE)),
                ],
            }
        ],
    }


# Default ink widths (cap units) when a char has no corpus samples.
DEFAULT_W = {
    "K": 560,
    "M": 640,
    "N": 580,
    "V": 580,
    "W": 660,
    "X": 560,
    "Y": 560,
    "D": 560,
    "H": 560,
    "F": 460,
    "8": 520,
    "v": 500,
    "w": 620,
    "x": 500,
    "y": 500,
    "z": 480,
    "k": 520,
    "%": 620,
    "#": 560,
    '"': 320,
    "'": 180,
    "(": 300,
    ")": 300,
    ",": 180,
    "^": 460,
    "`": 220,
    ";": 220,
    "<": 440,
    "=": 440,
    ">": 440,
    "?": 460,
    "@": 700,
    "[": 300,
    "\\": 420,
    "]": 300,
    "_": 440,
    "~": 460,
    "{": 340,
    "}": 340,
    "o": 500,
    "e": 500,
    "a": 500,
    "u": 500,
    "h": 520,
    "b": 520,
    "i": 220,
    "p": 520,
    "O": 580,
    "Q": 600,
    "U": 560,
    "R": 560,
    "P": 560,
    "&": 620,
    "S": 540,
    "Z": 520,
    "c": 500,
    "d": 520,
    "g": 520,
    "n": 520,
    "m": 640,
    "q": 520,
    "j": 300,
    "!": 160,
    "+": 460,
}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("samples")
    ap.add_argument("font_dir")
    ap.add_argument("--chars", default="".join(DEFAULT_W))
    ap.add_argument(
        "--default-width-chars",
        default="",
        help=(
            "Use DEFAULT_W instead of the soft consensus width for these chars. "
            "Use this when the receipt corpus consensus is visibly corrupted."
        ),
    )
    args = ap.parse_args(argv)

    paths = font_dir_paths(args.font_dir)
    written = []
    default_width_chars = set(args.default_width_chars)
    for ch in args.chars:
        if ch not in DEFAULT_W:
            print(f"no spec for {ch!r}, skipping")
            continue
        if ch in default_width_chars:
            W = DEFAULT_W[ch]
        else:
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
