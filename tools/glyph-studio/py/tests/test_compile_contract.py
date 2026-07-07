import json
import os

import numpy as np
import pytest
from glyphstudio.compile import compile_font
from glyphstudio.raster import rasterize_glyph
from glyphstudio.trace import trace_char


def _line(x0, y0, x1, y1):
    return {
        "closed": False,
        "nodes": [
            {"x": x0, "y": y0, "type": "corner"},
            {"x": x1, "y": y1, "type": "corner"},
        ],
    }


R = 55  # dot radius in cap units (dot.size 110): centerlines inset by this


def _fixture_font(tmp_path):
    font_dir = tmp_path / "testfont"
    (font_dir / "glyphs").mkdir(parents=True)
    font = {
        "version": 1,
        "name": "testfont",
        "refCap": 60,
        "metrics": {
            "capHeight": 1000,
            "xHeight": 700,
            "ascender": 1080,
            "descender": -350,
        },
        "params": {
            "dot": {"shape": "round", "size": 110, "pitch": 0.4},
            "weight": 1.0,
            "trackingScale": 1.0,
            "slant": 0.0,
            "corner": {"mode": "round", "tension": 0.5},
            "supersample": 4,
        },
        "preview": {},
        "review": {},
    }
    (font_dir / "font.json").write_text(json.dumps(font))

    glyphs = {
        # cap-ref chars: simple full-height strokes
        "A": [
            _line(0, R, 280, 1000 - R),
            _line(280, 1000 - R, 560, R),
            _line(120, 350, 440, 350),
        ],
        "B": [_line(60, R, 60, 1000 - R)],
        "D": [_line(60, R, 60, 1000 - R)],
        "E": [
            _line(60, R, 60, 1000 - R),
            _line(60, 1000 - R, 500, 1000 - R),
            _line(60, R, 500, R),
        ],
        "F": [_line(60, R, 60, 1000 - R)],
        "G": [_line(60, R, 60, 1000 - R)],
        "H": [
            _line(60, R, 60, 1000 - R),
            _line(560, R, 560, 1000 - R),
            _line(60, 500, 560, 500),
        ],
        "K": [_line(60, R, 60, 1000 - R)],
        "L": [_line(60, R, 60, 1000 - R), _line(60, R, 480, R)],
        "M": [
            _line(40, R, 40, 1000 - R),
            _line(40, 1000 - R, 320, 400),
            _line(320, 400, 600, 1000 - R),
            _line(600, 1000 - R, 600, R),
        ],
        "N": [
            _line(60, R, 60, 1000 - R),
            _line(60, 1000 - R, 560, R),
            _line(560, R, 560, 1000 - R),
        ],
        "P": [_line(60, R, 60, 1000 - R)],
        "R": [_line(60, R, 60, 1000 - R)],
        "S": [_line(60, R, 60, 1000 - R)],
        "T": [
            _line(300, R, 300, 1000 - R),
            _line(40, 1000 - R, 560, 1000 - R),
        ],
        "U": [
            _line(60, R, 60, 1000 - R),
            _line(560, R, 560, 1000 - R),
            _line(60, R, 560, R),
        ],
        "V": [_line(40, 1000 - R, 300, R), _line(300, R, 560, 1000 - R)],
        "W": [
            _line(20, 1000 - R, 180, R),
            _line(180, R, 320, 600),
            _line(320, 600, 460, R),
            _line(460, R, 620, 1000 - R),
        ],
        "X": [_line(40, R, 560, 1000 - R), _line(40, 1000, 560, 0)],
        "Z": [
            _line(40, 1000 - R, 560, 1000 - R),
            _line(560, 1000 - R, 40, R),
            _line(40, R, 560, R),
        ],
        "O": [_line(60, R, 60, 1000 - R)],
        # hyphen above baseline; p with descender
        "-": [_line(60, 420, 420, 420)],
        "p": [_line(60, -320 + R, 60, 700 - R)],
    }
    for ch, strokes in glyphs.items():
        g = {
            "version": 1,
            "char": ch,
            "codepoint": ord(ch),
            "provenance": "traced",
            "width": 600,
            "baselineNudgePx": 0,
            "overrides": {},
            "strokes": strokes,
        }
        (font_dir / "glyphs" / f"u{ord(ch):04x}.json").write_text(
            json.dumps(g)
        )
    return str(font_dir)


def test_compile_contract(tmp_path):
    font_dir = _fixture_font(tmp_path)
    out = str(tmp_path / "testfont.glyphs.npz")
    report = compile_font(font_dir, out)

    data = np.load(out)
    for key in data.files:
        assert key[0] in ("c", "o"), f"unexpected key {key}"
        if key.startswith("c"):
            arr = data[key]
            assert arr.dtype == np.uint8
            assert set(np.unique(arr)).issubset({0, 1})
            # tight crop: ink touches all four edges
            assert arr[0].any() and arr[-1].any()
            assert arr[:, 0].any() and arr[:, -1].any()
        else:
            assert data[key].dtype == np.int16

    # real BitmapFont accepted it
    assert abs(report["cap_h"] - 60) <= 3
    assert 0.3 < report["advance_ratio"] < 1.0
    assert report["sample_offsets"]["A"] in range(-3, 4)
    assert report["sample_offsets"]["-"] < 0
    assert report["sample_offsets"]["p"] > 0


def test_rasterize_cubic_bowl():
    glyph = {
        "version": 1,
        "char": "o",
        "codepoint": 111,
        "provenance": "traced",
        "width": 500,
        "baselineNudgePx": 0,
        "overrides": {},
        "strokes": [
            {
                "closed": True,
                "nodes": [
                    {
                        "x": 250,
                        "y": 700,
                        "type": "smooth",
                        "hIn": {"x": 100, "y": 700},
                        "hOut": {"x": 400, "y": 700},
                    },
                    {
                        "x": 250,
                        "y": 0,
                        "type": "smooth",
                        "hIn": {"x": 400, "y": 0},
                        "hOut": {"x": 100, "y": 0},
                    },
                ],
            }
        ],
    }
    params = {
        "dot": {"shape": "round", "size": 110, "pitch": 0.4},
        "weight": 1.0,
        "trackingScale": 1.0,
        "slant": 0.0,
        "supersample": 4,
    }
    bitmap, off = rasterize_glyph(glyph, params, 60)
    assert bitmap.sum() > 50
    # x-height bowl: ~42px tall at REF_CAP 60 (700 units + dot)
    assert 35 <= bitmap.shape[0] <= 55
    assert abs(off) <= 4
    # bowl must have a hole (counter not filled)
    interior = bitmap[
        bitmap.shape[0] // 2 - 2 : bitmap.shape[0] // 2 + 2,
        bitmap.shape[1] // 2 - 2 : bitmap.shape[1] // 2 + 2,
    ]
    assert interior.sum() == 0


def test_trace_roundtrip_bar():
    """Rasterize a known skeleton -> stack it -> trace -> shape survives."""
    glyph = {
        "version": 1,
        "char": "I",
        "codepoint": 73,
        "provenance": "traced",
        "width": 200,
        "baselineNudgePx": 0,
        "overrides": {},
        "strokes": [_line(100, 0, 100, 1000)],
    }
    params = {
        "dot": {"shape": "round", "size": 110, "pitch": 0.4},
        "weight": 1.0,
        "trackingScale": 1.0,
        "slant": 0.0,
        "supersample": 4,
    }
    bitmap, _ = rasterize_glyph(glyph, params, 40)  # sample-scale cap

    # place onto a 120x80 corpus-like canvas at baseline 92
    canvas = np.zeros((120, 80), dtype=bool)
    h, w = bitmap.shape
    y1 = 92
    canvas[y1 - h : y1, 10 : 10 + w] = bitmap.astype(bool)
    stack = np.stack([canvas] * 12)

    traced, width_units = trace_char(stack, 73)
    assert traced is not None
    assert width_units > 0
    # a vertical bar: one stroke, endpoints near y=0 and y=1000
    ys = [n["y"] for s in traced["strokes"] for n in s["nodes"]]
    assert min(ys) < 120 and max(ys) > 880
    xs = [n["x"] for s in traced["strokes"] for n in s["nodes"]]
    assert (max(xs) - min(xs)) < 80  # essentially vertical
