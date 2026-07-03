"""Load/validate/write glyph + font JSON sources (schema v1).

Coordinate space: cap units — y-up, baseline y=0, cap height y=1000.
1 px at REF_CAP=60 is 1000/60 ≈ 16.67 units.

IMPORTANT CONVENTION: baseline/cap are INK lines, but stroke coordinates are
CENTERLINES. Ink extends a dot radius past the centerline, so a cap-height
stem's centerline runs from y = dot/2 to y = 1000 - dot/2. The tracer emits
this automatically (skeletons sit half a stroke inside the ink); the editor
draws both the cap guide and the inset "centerline cap" guide.
"""
from __future__ import annotations

import json
import os
from typing import Any

SCHEMA_VERSION = 1

DEFAULT_FONT = {
    "version": SCHEMA_VERSION,
    "name": "",
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
    "preview": {"condense": 0.895, "capPx": 22, "thin": "auto"},
    "review": {},
}


def glyph_filename(codepoint: int) -> str:
    return f"u{codepoint:04x}.json"


def font_dir_paths(font_dir: str) -> dict[str, str]:
    return {
        "font": os.path.join(font_dir, "font.json"),
        "glyphs": os.path.join(font_dir, "glyphs"),
        "traced": os.path.join(font_dir, "_traced"),
        "reference": os.path.join(font_dir, "reference"),
    }


def load_font(font_dir: str) -> dict[str, Any]:
    path = font_dir_paths(font_dir)["font"]
    with open(path, encoding="utf-8") as fh:
        font = json.load(fh)
    if font.get("version") != SCHEMA_VERSION:
        raise ValueError(f"unsupported font schema version: {font.get('version')}")
    return font


def load_glyphs(font_dir: str) -> dict[int, dict[str, Any]]:
    gdir = font_dir_paths(font_dir)["glyphs"]
    glyphs: dict[int, dict[str, Any]] = {}
    if not os.path.isdir(gdir):
        return glyphs
    for name in sorted(os.listdir(gdir)):
        if not name.endswith(".json"):
            continue
        with open(os.path.join(gdir, name), encoding="utf-8") as fh:
            g = json.load(fh)
        validate_glyph(g)
        glyphs[int(g["codepoint"])] = g
    return glyphs


def validate_glyph(g: dict[str, Any]) -> None:
    if g.get("version") != SCHEMA_VERSION:
        raise ValueError(f"glyph schema version {g.get('version')} unsupported")
    for field in ("char", "codepoint", "provenance", "strokes"):
        if field not in g:
            raise ValueError(f"glyph missing field: {field}")
    if g["provenance"] not in ("traced", "edited"):
        raise ValueError(f"bad provenance: {g['provenance']}")
    for stroke in g["strokes"]:
        nodes = stroke.get("nodes", [])
        if len(nodes) < 2:
            raise ValueError("stroke with < 2 nodes")
        for node in nodes:
            for key in ("x", "y"):
                if not isinstance(node.get(key), (int, float)):
                    raise ValueError("node missing numeric x/y")


def atomic_write_json(path: str, obj: Any) -> None:
    tmp = path + ".tmp"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=1)
        fh.write("\n")
    os.replace(tmp, path)


def write_glyph(font_dir: str, glyph: dict[str, Any], *,
                respect_edited: bool = True) -> str:
    """Write a glyph source; hand-edited glyphs divert to _traced/.

    Returns the path written. Machine re-traces must never clobber hand
    work: if the target exists with provenance "edited" and respect_edited,
    the new JSON lands in `_traced/` for a diff/adopt flow instead.
    """
    validate_glyph(glyph)
    paths = font_dir_paths(font_dir)
    name = glyph_filename(int(glyph["codepoint"]))
    target = os.path.join(paths["glyphs"], name)
    if respect_edited and os.path.exists(target):
        with open(target, encoding="utf-8") as fh:
            existing = json.load(fh)
        if existing.get("provenance") == "edited":
            diverted = os.path.join(paths["traced"], name)
            atomic_write_json(diverted, glyph)
            return diverted
    atomic_write_json(target, glyph)
    return target


def merged_params(font: dict[str, Any], glyph: dict[str, Any]) -> dict[str, Any]:
    """Font params with per-glyph overrides applied (shallow per subtree)."""
    params = json.loads(json.dumps(font["params"]))  # deep copy
    for key, val in (glyph.get("overrides") or {}).items():
        if isinstance(val, dict) and isinstance(params.get(key), dict):
            params[key].update(val)
        else:
            params[key] = val
    return params
