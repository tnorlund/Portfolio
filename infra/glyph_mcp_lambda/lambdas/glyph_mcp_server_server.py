#!/usr/bin/env python3
"""MCP Server for Glyph Studio (read-only, hosted on Lambda).

Exposes the Glyph Studio inspection loop as MCP tools over a Lambda
Function URL, so ``glyph-studio`` can be added as a claude.ai Connector.
It imports the ``glyphstudio`` package directly (no node, no subprocess)
and reuses the same rasterizer / measurement / compare code the local
studio uses.

This is the READ-ONLY sibling of the local stdio MCP server. Anything
that writes back into the repo (set_glyph, simplify --apply, publish)
lives only in the local server: the cloud has no working tree to write
to, so those tools return a clear error naming the local MCP.

Fonts (skeleton sources) are baked into the container image. The
letterform corpora (.npz) that measure/compare/view_samples need are
downloaded lazily from S3 on first use and cached in /tmp.

Environment:
    GLYPH_FONTS_DIR      dir of baked font sources (default /var/task/fonts)
    GLYPH_CORPUS_BUCKET  S3 bucket for corpora (default raw-image-bucket-c779c32)
    GLYPH_CORPUS_PREFIX  S3 key prefix (default merchant_fonts)
"""

import base64
import io
import json
import logging
import os
import sys

import numpy as np

# The glyphstudio package is installed into the image; these imports
# resolve against it (numpy + PIL only — no scipy/skimage).
from glyphstudio.compare import _cell
from glyphstudio.compile import compile_font
from glyphstudio.measure import measure_char
from glyphstudio.raster import rasterize_glyph
from glyphstudio.samples import (
    canvas_geometry,
    consensus,
    consensus_soft,
    load_stack,
)
from glyphstudio.schema import (
    glyph_filename,
    load_font,
    load_glyphs,
    merged_params,
)
from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import ImageContent, TextContent, Tool
from PIL import Image

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

FONTS_DIR = os.environ.get("GLYPH_FONTS_DIR", "/var/task/fonts")
CORPUS_BUCKET = os.environ.get(
    "GLYPH_CORPUS_BUCKET", "raw-image-bucket-c779c32"
)
CORPUS_PREFIX = os.environ.get("GLYPH_CORPUS_PREFIX", "merchant_fonts")
CORPUS_CACHE_DIR = "/tmp/glyph_corpus"

# Font name -> corpus S3 key. Font name doubles as the corpus slug: the
# corpora were uploaded to s3://<bucket>/<prefix>/<font>/corpus.npz.
CORPUS_FONTS = ("costco", "cvs", "sprouts", "traderjoes", "vons")

# Tools that only exist in the local (repo-writing) MCP server.
_LOCAL_ONLY = (
    "set_glyph",
    "simplify_glyphs",
    "publish_font",
    "review_font",
)

_LOCAL_ONLY_MSG = (
    "This tool is not available on the hosted (read-only) glyph-studio "
    "MCP. Writes and repo-coupled renders (set_glyph, simplify --apply, "
    "publish_font, review_font) run only on the LOCAL glyph-studio MCP "
    "server, which has the working tree and the receipt compositor."
)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _font_dir(font: str) -> str:
    """Resolve and validate a baked font source dir."""
    if not font or not all(c.isalnum() or c in "-_" for c in font):
        raise ValueError(f"bad font name: {font!r}")
    path = os.path.join(FONTS_DIR, font)
    if not os.path.isdir(path):
        available = sorted(
            d
            for d in os.listdir(FONTS_DIR)
            if os.path.isdir(os.path.join(FONTS_DIR, d))
        )
        raise ValueError(
            f"unknown font {font!r}; available: {', '.join(available)}"
        )
    return path


def _corpus_path(font: str) -> str:
    """Return a local path to the font's corpus .npz, downloading once."""
    if font not in CORPUS_FONTS:
        raise ValueError(
            f"no sample corpus configured for {font!r}; "
            f"corpora exist for: {', '.join(CORPUS_FONTS)}"
        )
    os.makedirs(CORPUS_CACHE_DIR, exist_ok=True)
    local = os.path.join(CORPUS_CACHE_DIR, f"{font}.npz")
    if not os.path.exists(local):
        import boto3

        key = f"{CORPUS_PREFIX}/{font}/corpus.npz"
        logger.info("downloading corpus s3://%s/%s", CORPUS_BUCKET, key)
        boto3.client("s3").download_file(CORPUS_BUCKET, key, local)
    return local


def _png_b64(arr: np.ndarray) -> str:
    """Mask -> black-ink transparent PNG, base64 (binary or soft 0..1)."""
    a = arr.astype(float)
    if a.max() > 1.0:
        a = a / 255.0
    h, w = a.shape
    rgba = np.zeros((h, w, 4), dtype=np.uint8)
    rgba[..., 3] = np.clip(a * 255.0, 0, 255).astype(np.uint8)
    buf = io.BytesIO()
    Image.fromarray(rgba, "RGBA").save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _rgb_png_b64(img: Image.Image, max_dim: int = 1000) -> str:
    """Downscale an RGB PIL image to <= max_dim tall and return base64."""
    w, h = img.size
    if h > max_dim:
        s = max_dim / h
        img = img.resize((max(1, round(w * s)), max_dim), Image.LANCZOS)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return base64.b64encode(buf.getvalue()).decode()


def _grid_montage(stack: np.ndarray) -> np.ndarray:
    """Montage the first up-to-9 samples into a 3x3 grid (2px gutters)."""
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


def _count_nodes(glyph: dict) -> int:
    return sum(len(s.get("nodes", [])) for s in glyph.get("strokes", []))


def _audit_glyph(g: dict, font: dict) -> dict:
    """Per-glyph anatomy (mirrors the local MCP's auditGlyph)."""
    cap_h = (font.get("metrics") or {}).get("capHeight", 1000)
    nodes = 0
    line_segs = 0
    cubic_segs = 0
    closed_loops = 0
    min_y = float("inf")
    max_y = float("-inf")
    for s in g.get("strokes", []):
        ns = s.get("nodes", [])
        nodes += len(ns)
        for n in ns:
            y = n.get("y")
            if isinstance(y, (int, float)):
                min_y = min(min_y, y)
                max_y = max(max_y, y)
        pairs = [(ns[i], ns[i + 1]) for i in range(len(ns) - 1)]
        if s.get("closed") and len(ns) > 1:
            pairs.append((ns[-1], ns[0]))
            closed_loops += 1
        for a, b in pairs:
            if a.get("hOut") or b.get("hIn"):
                cubic_segs += 1
            else:
                line_segs += 1
    return {
        "char": g.get("char"),
        "codepoint": g.get("codepoint"),
        "provenance": g.get("provenance"),
        "strokes": len(g.get("strokes", [])),
        "nodes": nodes,
        "lineSegs": line_segs,
        "cubicSegs": cubic_segs,
        "closedLoops": closed_loops,
        "width": g.get("width"),
        "descender": (min_y < 0) if min_y != float("inf") else False,
        "ascender": (max_y > cap_h) if max_y != float("-inf") else False,
        "weight": (g.get("overrides") or {}).get(
            "weight", (font.get("params") or {}).get("weight")
        ),
    }


def _load_one_glyph(font_dir: str, char: str) -> dict:
    cp = ord(char)
    path = os.path.join(font_dir, "glyphs", glyph_filename(cp))
    if not os.path.exists(path):
        raise ValueError(f"no saved glyph for {char!r} (U+{cp:04X})")
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------
server = Server("glyph-studio")


@server.list_tools()
async def list_tools() -> list[Tool]:
    return [
        Tool(
            name="list_fonts",
            description=(
                "List the baked-in glyph fonts available on this hosted "
                "studio, with glyph counts and whether a sample corpus is "
                "available for measure/compare/view_samples."
            ),
            inputSchema={"type": "object", "properties": {}},
        ),
        Tool(
            name="list_glyphs",
            description=(
                "Coverage table for a font: every glyph's char, codepoint, "
                "provenance, width, and stroke/node counts."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "font": {"type": "string", "description": "Font name"}
                },
                "required": ["font"],
            },
        ),
        Tool(
            name="get_glyph",
            description=(
                "Return the saved uNNNN.json glyph source (pretty-printed) "
                "plus a rendered PNG of that glyph the way the receipt "
                "compositor draws it."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "font": {"type": "string"},
                    "char": {
                        "type": "string",
                        "description": "Single character",
                    },
                },
                "required": ["font", "char"],
            },
        ),
        Tool(
            name="render_glyph",
            description=(
                "Rasterize the saved glyph for a char to a PNG the way the "
                "receipt compositor draws it. Pass unsaved_overrides (a "
                'params override dict, e.g. {"weight": 1.2}) to PREVIEW a '
                "parameter tweak without writing. refCap defaults to 60."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "font": {"type": "string"},
                    "char": {"type": "string"},
                    "unsaved_overrides": {
                        "type": "object",
                        "description": (
                            "Optional params overrides merged over the "
                            "glyph's own overrides for a preview render."
                        ),
                    },
                    "refCap": {"type": "integer", "default": 60},
                },
                "required": ["font", "char"],
            },
        ),
        Tool(
            name="compile_report",
            description=(
                "Compile the font to the BitmapFont .glyphs.npz contract and "
                "run the self-check: cap_h, advance_ratio, coverage, "
                "cap-height deviations, clamp-width warnings, empty rasters, "
                "and sample offsets. Returns metrics only (no contact sheet)."
            ),
            inputSchema={
                "type": "object",
                "properties": {"font": {"type": "string"}},
                "required": ["font"],
            },
        ),
        Tool(
            name="font_audit",
            description=(
                "Per-glyph anatomy from the sources: stroke/node counts, "
                "line vs cubic segment counts, closed loops, width, and "
                "ascender/descender flags. Finds over-fragmented glyphs."
            ),
            inputSchema={
                "type": "object",
                "properties": {"font": {"type": "string"}},
                "required": ["font"],
            },
        ),
        Tool(
            name="measure_glyph",
            description=(
                "Numerically measure a char's real-print consensus geometry: "
                "ink bbox in cap units, per-height ink spans, horizontal "
                "crossbars, vertical stems, stroke width, hole count. Needs "
                "the font's sample corpus. Returns {available:false} when the "
                "corpus lacks the char."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "font": {"type": "string"},
                    "char": {"type": "string"},
                    "threshold": {"type": "number", "default": 0.45},
                },
                "required": ["font", "char"],
            },
        ),
        Tool(
            name="compare_glyph",
            description=(
                "Confirmation-arbiter view: for one or more chars, a "
                "magnified strip PNG with rows [soft consensus | compiled | "
                "overlay] so misalignment and weight mismatch read instantly "
                "against the real prints. Needs the font's sample corpus."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "font": {"type": "string"},
                    "chars": {
                        "type": "string",
                        "description": "Characters to compare, e.g. 'KMNVWX'",
                    },
                },
                "required": ["font", "chars"],
            },
        ),
        Tool(
            name="view_samples",
            description=(
                "Render the real-letterform corpus for a char: median (soft "
                "consensus), binary (thresholded consensus), index (a single "
                "sample i), or grid (first up-to-9 samples montaged 3x3). "
                "Needs the font's sample corpus."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "font": {"type": "string"},
                    "char": {"type": "string"},
                    "mode": {
                        "type": "string",
                        "enum": ["median", "binary", "index", "grid"],
                        "default": "median",
                    },
                    "i": {"type": "integer", "default": 0},
                },
                "required": ["font", "char"],
            },
        ),
    ]


@server.call_tool()
async def call_tool(
    name: str, arguments: dict
) -> list[TextContent | ImageContent]:
    try:
        if name in _LOCAL_ONLY:
            raise ValueError(_LOCAL_ONLY_MSG)
        if name == "list_fonts":
            return _tool_list_fonts()
        if name == "list_glyphs":
            return _tool_list_glyphs(arguments["font"])
        if name == "get_glyph":
            return _tool_get_glyph(arguments["font"], arguments["char"])
        if name == "render_glyph":
            return _tool_render_glyph(
                arguments["font"],
                arguments["char"],
                arguments.get("unsaved_overrides"),
                int(arguments.get("refCap", 60)),
            )
        if name == "compile_report":
            return _tool_compile_report(arguments["font"])
        if name == "font_audit":
            return _tool_font_audit(arguments["font"])
        if name == "measure_glyph":
            return _tool_measure_glyph(
                arguments["font"],
                arguments["char"],
                float(arguments.get("threshold", 0.45)),
            )
        if name == "compare_glyph":
            return _tool_compare_glyph(arguments["font"], arguments["chars"])
        if name == "view_samples":
            return _tool_view_samples(
                arguments["font"],
                arguments["char"],
                arguments.get("mode", "median"),
                int(arguments.get("i", 0)),
            )
        raise ValueError(f"unknown tool: {name}")
    except Exception as e:  # noqa: BLE001
        logger.exception("tool %s failed", name)
        return [TextContent(type="text", text=f"Error: {e}")]


# ---------------------------------------------------------------------------
# tool implementations
# ---------------------------------------------------------------------------
def _tool_list_fonts() -> list[TextContent]:
    fonts = []
    for d in sorted(os.listdir(FONTS_DIR)):
        fdir = os.path.join(FONTS_DIR, d)
        if not os.path.isfile(os.path.join(fdir, "font.json")):
            continue
        try:
            glyphs = load_glyphs(fdir)
            font = load_font(fdir)
            fonts.append(
                {
                    "font": d,
                    "name": font.get("name", d),
                    "glyphs": len(glyphs),
                    "hasCorpus": d in CORPUS_FONTS,
                }
            )
        except Exception as e:  # noqa: BLE001
            fonts.append({"font": d, "error": str(e)})
    lines = [
        f"{f['font']}  ({f.get('glyphs', '?')} glyphs)"
        f"{'  [corpus]' if f.get('hasCorpus') else ''}"
        for f in fonts
    ]
    text = f"{len(fonts)} fonts:\n" + "\n".join(lines)
    return [
        TextContent(type="text", text=text),
        TextContent(type="text", text=json.dumps({"fonts": fonts}, indent=2)),
    ]


def _tool_list_glyphs(font: str) -> list[TextContent]:
    fdir = _font_dir(font)
    glyphs = load_glyphs(fdir)
    rows = []
    for cp in sorted(glyphs):
        g = glyphs[cp]
        rows.append(
            {
                "char": g.get("char"),
                "codepoint": cp,
                "provenance": g.get("provenance"),
                "width": g.get("width"),
                "strokes": len(g.get("strokes", [])),
                "nodes": _count_nodes(g),
                "weightOverride": (g.get("overrides") or {}).get("weight"),
            }
        )
    text_lines = [
        f"{r['char']}  U+{r['codepoint']:04X}  {r['provenance']}  "
        f"w={r['width']}  strokes={r['strokes']}  nodes={r['nodes']}"
        + (
            f"  wt={r['weightOverride']}"
            if r["weightOverride"] is not None
            else ""
        )
        for r in rows
    ]
    header = f"{len(rows)} glyphs in font {font!r}"
    return [
        TextContent(type="text", text=header + "\n" + "\n".join(text_lines)),
        TextContent(
            type="text",
            text=json.dumps(
                {"font": font, "count": len(rows), "glyphs": rows}, indent=2
            ),
        ),
    ]


def _tool_get_glyph(font: str, char: str) -> list[TextContent | ImageContent]:
    fdir = _font_dir(font)
    font_src = load_font(fdir)
    glyph = _load_one_glyph(fdir, char)
    params = merged_params(font_src, glyph)
    bitmap, off = rasterize_glyph(glyph, params, 60)
    return [
        TextContent(type="text", text=json.dumps(glyph, indent=1)),
        ImageContent(
            type="image", data=_png_b64(bitmap), mimeType="image/png"
        ),
        TextContent(
            type="text",
            text=(
                f"render: w={bitmap.shape[1]} h={bitmap.shape[0]} "
                f"off={off} refCap=60"
            ),
        ),
    ]


def _tool_render_glyph(
    font: str, char: str, unsaved_overrides, ref_cap: int
) -> list[TextContent | ImageContent]:
    fdir = _font_dir(font)
    font_src = load_font(fdir)
    glyph = _load_one_glyph(fdir, char)
    note = ""
    if unsaved_overrides:
        if not isinstance(unsaved_overrides, dict):
            raise ValueError("unsaved_overrides must be an object")
        merged = dict(glyph.get("overrides") or {})
        for k, v in unsaved_overrides.items():
            if isinstance(v, dict) and isinstance(merged.get(k), dict):
                merged[k] = {**merged[k], **v}
            else:
                merged[k] = v
        glyph = {**glyph, "overrides": merged}
        note = " (unsaved override preview)"
    params = merged_params(font_src, glyph)
    bitmap, off = rasterize_glyph(glyph, params, ref_cap)
    return [
        ImageContent(
            type="image", data=_png_b64(bitmap), mimeType="image/png"
        ),
        TextContent(
            type="text",
            text=(
                f"w={bitmap.shape[1]} h={bitmap.shape[0]} off={off} "
                f"refCap={ref_cap}{note}"
            ),
        ),
    ]


def _tool_compile_report(font: str) -> list[TextContent]:
    fdir = _font_dir(font)
    out = os.path.join("/tmp", f"{font}-studio.glyphs.npz")
    report = compile_font(fdir, out)
    lines = [
        f"font {font!r}: {report.get('glyph_count')} glyphs compiled",
        f"cap_h={report.get('cap_h'):.1f} "
        f"advance_ratio={report.get('advance_ratio'):.3f}",
        f"coverage={report.get('coverage')}/94 "
        f"missing: {report.get('missing') or '-'}",
    ]
    if report.get("cap_height_deviations"):
        lines.append(
            f"CAP-HEIGHT DEVIATIONS: {report['cap_height_deviations']}"
        )
    if report.get("clamp_width_warnings"):
        lines.append(f"CLAMP-WIDTH WARNINGS: {report['clamp_width_warnings']}")
    if report.get("empty"):
        lines.append(f"EMPTY RASTERS: {''.join(report['empty'])}")
    lines.append(f"offsets: {report.get('sample_offsets')}")
    return [
        TextContent(type="text", text="\n".join(lines)),
        TextContent(type="text", text=json.dumps(report, indent=2)),
    ]


def _tool_font_audit(font: str) -> list[TextContent]:
    fdir = _font_dir(font)
    font_src = load_font(fdir)
    glyphs = load_glyphs(fdir)
    rows = [_audit_glyph(glyphs[cp], font_src) for cp in sorted(glyphs)]
    text_lines = [
        f"{r['char']}  strokes={r['strokes']} nodes={r['nodes']} "
        f"line={r['lineSegs']} cubic={r['cubicSegs']} "
        f"loops={r['closedLoops']}"
        + (" desc" if r["descender"] else "")
        + (" asc" if r["ascender"] else "")
        for r in rows
    ]
    header = f"Anatomy for {len(rows)} glyphs in {font!r}"
    return [
        TextContent(type="text", text=header + "\n" + "\n".join(text_lines)),
        TextContent(
            type="text",
            text=json.dumps(
                {"font": font, "count": len(rows), "glyphs": rows}, indent=2
            ),
        ),
    ]


def _tool_measure_glyph(
    font: str, char: str, threshold: float
) -> list[TextContent]:
    _font_dir(font)  # validate name
    corpus = _corpus_path(font)
    measurement = measure_char(corpus, char, threshold)
    if not measurement.get("available"):
        return [
            TextContent(
                type="text",
                text=(
                    f"char {char!r} not in {font!r} corpus "
                    "(available:false)"
                ),
            ),
            TextContent(type="text", text=json.dumps(measurement, indent=2)),
        ]
    bb = measurement.get("ink_bbox", {})
    summary = (
        f"measure {char!r} ({font}, {measurement.get('samples')} samples, "
        f"threshold={threshold})\n"
        f"ink_bbox: y {bb.get('y_bottom')}..{bb.get('y_top')}, "
        f"x {bb.get('x_left')}..{bb.get('x_right')} (cap units)\n"
        f"stroke_width={measurement.get('stroke_width_units', '?')}u  "
        f"bars={len(measurement.get('horizontal_bars', []))}  "
        f"stems={len(measurement.get('vertical_stems', []))}  "
        f"holes={measurement.get('holes')}"
    )
    return [
        TextContent(type="text", text=summary),
        TextContent(type="text", text=json.dumps(measurement, indent=2)),
    ]


def _tool_compare_glyph(
    font: str, chars: str
) -> list[TextContent | ImageContent]:
    fdir = _font_dir(font)
    corpus = _corpus_path(font)
    font_src = load_font(fdir)
    glyphs = load_glyphs(fdir)
    ref_cap = int(font_src.get("refCap", 60))
    scale = 2

    rows = []
    for ch in chars:
        cp = ord(ch)
        stack = load_stack(corpus, cp)
        soft = None
        ref_cap_s, baseline_s = ref_cap, int(ref_cap * 2.3)
        if stack is not None and len(stack):
            soft = consensus_soft(stack)
            ref_cap_s, baseline_s = canvas_geometry(stack.shape[1])
        comp, off = (None, 0)
        if cp in glyphs:
            comp_arr, off = rasterize_glyph(
                glyphs[cp], merged_params(font_src, glyphs[cp]), ref_cap
            )
            comp = comp_arr.astype(float)
        row = _cell(soft, comp, off, ref_cap_s, baseline_s, ref_cap, scale)
        from PIL import ImageDraw

        labeled = Image.new(
            "RGB", (row.width + 40 * scale, row.height), (255, 255, 255)
        )
        labeled.paste(row, (40 * scale, 0))
        ImageDraw.Draw(labeled).text(
            (6, row.height // 2 - 10), ch, fill=(200, 30, 30)
        )
        rows.append(labeled)

    total = Image.new(
        "RGB",
        (max(r.width for r in rows), sum(r.height + 2 for r in rows)),
        (120, 120, 120),
    )
    y = 0
    for r in rows:
        total.paste(r, (0, y))
        y += r.height + 2

    return [
        ImageContent(
            type="image", data=_rgb_png_b64(total), mimeType="image/png"
        ),
        TextContent(
            type="text",
            text=(
                f"compare {chars!r} — columns "
                "[soft consensus | compiled | overlay]"
            ),
        ),
    ]


def _tool_view_samples(
    font: str, char: str, mode: str, i: int
) -> list[TextContent | ImageContent]:
    _font_dir(font)  # validate name
    corpus = _corpus_path(font)
    stack = load_stack(corpus, ord(char))
    if stack is None or len(stack) == 0:
        raise ValueError(f"no samples for {char!r} in {font!r} corpus")
    if mode == "index":
        idx = max(0, min(len(stack) - 1, i))
        mask = stack[idx]
    elif mode == "binary":
        mask = consensus(stack)
    elif mode == "grid":
        mask = _grid_montage(stack)
    else:
        mask = consensus_soft(stack)
    ref_cap, baseline_row = canvas_geometry(stack.shape[1])
    return [
        ImageContent(type="image", data=_png_b64(mask), mimeType="image/png"),
        TextContent(
            type="text",
            text=(
                f"char={char!r} mode={mode} n={len(stack)} "
                f"refCap={ref_cap} baselineRow={baseline_row} "
                f"w={mask.shape[1]} h={mask.shape[0]}"
            ),
        ),
    ]


async def main():
    """Run the MCP server over stdio."""
    logger.info("Starting Glyph Studio MCP Server (read-only)...")
    async with stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            server.create_initialization_options(),
        )


if __name__ == "__main__":
    import asyncio

    asyncio.run(main())
