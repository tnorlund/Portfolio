"""Persistent JSON-lines RPC worker for the studio server.

One request per line on stdin: {"id": n, "op": str, ...args}
One response per line on stdout: {"id": n, "ok": bool, ...result|error}

Ops:
  raster_glyph  {glyph, params, refCap}         -> {png_b64, w, h, off}
  sample_png    {samples, codepoint, mode, i}   -> {png_b64, w, h,
                                                    refCap, baselineRow, n}
  atlas_json    {npz}                           -> {glyphs: {cp: {w,h,off,bits_b64}}}
"""
from __future__ import annotations

import base64
import io
import json
import sys

import numpy as np
from PIL import Image

from .raster import rasterize_glyph
from .samples import canvas_geometry, consensus, load_stack


def _png_b64(arr: np.ndarray) -> str:
    """Binary/uint8 mask -> black-ink transparent PNG, base64."""
    mask = (arr > 0).astype(np.uint8)
    h, w = mask.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[..., 3] = mask * 255
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def op_raster_glyph(req: dict) -> dict:
    bitmap, off = rasterize_glyph(
        req["glyph"], req["params"], int(req.get("refCap", 60))
    )
    return {
        "png_b64": _png_b64(bitmap),
        "w": int(bitmap.shape[1]),
        "h": int(bitmap.shape[0]),
        "off": int(off),
    }


def op_sample_png(req: dict) -> dict:
    stack = load_stack(req["samples"], int(req["codepoint"]))
    if stack is None or len(stack) == 0:
        raise ValueError("no samples for codepoint")
    mode = req.get("mode", "median")
    if mode == "index":
        i = max(0, min(len(stack) - 1, int(req.get("i", 0))))
        mask = stack[i]
    else:
        mask = consensus(stack)
    ref_cap, baseline_row = canvas_geometry(stack.shape[1])
    return {
        "png_b64": _png_b64(mask.astype(np.uint8)),
        "w": int(mask.shape[1]),
        "h": int(mask.shape[0]),
        "refCap": int(ref_cap),
        "baselineRow": int(baseline_row),
        "n": int(len(stack)),
    }


def op_atlas_json(req: dict) -> dict:
    data = np.load(req["npz"])
    glyphs = {}
    for key in data.files:
        if not key.startswith("c"):
            continue
        cp = int(key[1:])
        arr = data[key]
        glyphs[str(cp)] = {
            "w": int(arr.shape[1]),
            "h": int(arr.shape[0]),
            "off": int(data.get(f"o{cp}", 0)),
            "bits_b64": base64.b64encode(
                np.packbits(arr.astype(bool), axis=None).tobytes()
            ).decode(),
        }
    return {"glyphs": glyphs}


OPS = {
    "raster_glyph": op_raster_glyph,
    "sample_png": op_sample_png,
    "atlas_json": op_atlas_json,
    "ping": lambda req: {"pong": True},
}


def main() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            handler = OPS[req["op"]]
            result = handler(req)
            result.update({"id": req.get("id"), "ok": True})
        except Exception as exc:  # noqa: BLE001
            result = {"id": (req.get("id") if isinstance(req, dict) else None),
                      "ok": False, "error": repr(exc)}
        sys.stdout.write(json.dumps(result) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
