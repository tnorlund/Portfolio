"""Persistent JSON-lines RPC worker for the studio server.

One request per line on stdin: {"id": n, "op": str, ...args}
One response per line on stdout: {"id": n, "ok": bool, ...result|error}

Ops:
  raster_glyph  {glyph, params, refCap}         -> {png_b64, w, h, off}
  sample_png    {samples, codepoint, mode, i}   -> {png_b64, w, h,
                                                    refCap, baselineRow, n}
  measure_char  {samples, char, threshold}      -> measure.measure_char(...)
  atlas_json    {npz}                           -> {glyphs: {cp: {w,h,off,bits_b64}}}
"""

from __future__ import annotations

import base64
import io
import json
import sys

import numpy as np
from PIL import Image

from .measure import measure_char
from .raster import rasterize_glyph
from .samples import canvas_geometry, consensus, consensus_soft, load_stack


def _png_b64(arr: np.ndarray) -> str:
    """Mask -> black-ink transparent PNG, base64.

    Accepts binary {0,1} OR soft float 0..1 maps — alpha carries the soft
    ink fraction so onion-skins show real stroke-weight gradients.
    """
    a = arr.astype(float)
    if a.max() > 1.0:
        a = a / 255.0
    h, w = a.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[..., 3] = np.clip(a * 255.0, 0, 255).astype(np.uint8)
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


def _grid_montage(stack: np.ndarray) -> np.ndarray:
    """Montage the first up-to-9 individual samples in a 3x3 grid with 2px
    white gutters, so a reviewer sees several real prints at once.

    Samples in a stack share one canvas shape, so the per-tile pad-to-common
    -size is a no-op here; the layout stays general anyway.
    """
    n = min(9, len(stack))
    th, tw = int(stack.shape[1]), int(stack.shape[2])
    gut = 2
    grid = np.zeros((3 * th + 2 * gut, 3 * tw + 2 * gut), dtype=float)
    for k in range(n):
        r, c = divmod(k, 3)
        y0 = r * (th + gut)
        x0 = c * (tw + gut)
        grid[y0 : y0 + th, x0 : x0 + tw] = stack[k].astype(float)
    return grid


def op_sample_png(req: dict) -> dict:
    stack = load_stack(req["samples"], int(req["codepoint"]))
    if stack is None or len(stack) == 0:
        raise ValueError("no samples for codepoint")
    mode = req.get("mode", "median")
    if mode == "index":
        i = max(0, min(len(stack) - 1, int(req.get("i", 0))))
        mask = stack[i]
    elif mode == "binary":
        mask = consensus(stack)
    elif mode == "grid":
        mask = _grid_montage(stack)
    else:
        # soft fraction map: no strict binarization for LOOKING at glyphs
        mask = consensus_soft(stack)
    ref_cap, baseline_row = canvas_geometry(stack.shape[1])
    return {
        "png_b64": _png_b64(mask),
        "w": int(mask.shape[1]),
        "h": int(mask.shape[0]),
        "refCap": int(ref_cap),
        "baselineRow": int(baseline_row),
        "n": int(len(stack)),
    }


def op_measure_char(req: dict) -> dict:
    return measure_char(
        req["samples"], req["char"], float(req.get("threshold", 0.45))
    )


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
    "measure_char": op_measure_char,
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
            result = {
                "id": (req.get("id") if isinstance(req, dict) else None),
                "ok": False,
                "error": repr(exc),
            }
        sys.stdout.write(json.dumps(result) + "\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
