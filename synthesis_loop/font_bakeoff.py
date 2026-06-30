#!/usr/bin/env python3
"""font_bakeoff.py -- render ONE synthetic candidate across many fonts, SAME layout.

The grid renderer derives cell_w (char advance) and row pitch from the loaded
font's own metrics, so the PR1 layout (row grouping, amount lane, body gaps, row
pitch) is font-independent: point it at a different .ttf and the layout
recalibrates itself. This tool exploits that to A/B fonts with everything else
held fixed -- it fetches the merchant atlas/profile ONCE, then re-renders the same
op candidate with each font via ``_render_cached_hybrid(font_path=...)`` and
stitches a labeled comparison strip (real photo first when available).

Usage:
  font_bakeoff.py <merchant_dir> <merchant_name> <op> [font ...]

  <merchant_dir>   a dir holding bundle.json + receipt_dir/ (e.g. /tmp/gridfix/amazon_fresh)
  <op>             add_line_item | replace_field | remove_line_item | compose_*
  [font ...]       short names from FONT_CATALOG below, or explicit .ttf paths.
                   Default: the catalog's curated shortlist.

Output: <merchant_dir>/fontbakeoff.png  (+ per-font PNGs in <merchant_dir>/_fonts/)
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(os.path.dirname(HERE), "scripts")
for p in (HERE, SCRIPTS):
    if p not in sys.path:
        sys.path.insert(0, p)

import render_synthetic_receipts as rsr  # noqa: E402
import thermal_variants as TV  # noqa: E402
from PIL import Image, ImageDraw  # noqa: E402

TABLE = os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22")
REGION = os.environ.get("AWS_REGION", "us-east-1")
W, H = 460, 1100

# Short-name -> path. None path = the renderer's default candidate list (Andale
# -> vendored B612 -> legacy). Add a row to try a new face by name.
_FD = os.path.join(
    SCRIPTS, "..", "receipt_agent", "receipt_agent", "agents",
    "label_evaluator", "rendering", "fonts",
)
FONT_CATALOG = {
    "default": None,
    "andale": "/System/Library/Fonts/Supplemental/Andale Mono.ttf",
    "b612": os.path.join(_FD, "B612Mono-Regular.ttf"),
    "ptmono": "/System/Library/Fonts/Supplemental/PTMono.ttc",
    "menlo": "/System/Library/Fonts/Menlo.ttc",
    "courier": "/System/Library/Fonts/Supplemental/Courier New.ttf",
    "vga": os.path.join(_FD, "Px437_IBM_VGA_8x16.ttf"),
}
DEFAULT_SHORTLIST = ["andale", "b612", "ptmono", "vga"]

# The header+first-items band reads the font most clearly for side-by-side.
CROP_BAND = (0, 150, 460, 560)


def _op(ex):
    return (ex.get("metadata") or {}).get("operation") or ex.get("operation") or "?"


def _resolve_font(token: str) -> tuple[str, str | None]:
    if token in FONT_CATALOG:
        return token, FONT_CATALOG[token]
    return os.path.splitext(os.path.basename(token))[0], token


def _crop_labeled(path: str, label: str) -> Image.Image | None:
    if not os.path.exists(path):
        return None
    im = Image.open(path).convert("RGB")
    if im.width != W:
        im = im.resize((W, int(im.height * W / im.width)))
    c = im.crop(CROP_BAND)
    lab = Image.new("RGB", (c.width, 24), (255, 255, 255))
    ImageDraw.Draw(lab).text((6, 6), label, fill=(0, 0, 0))
    out = Image.new("RGB", (c.width, c.height + 24), (255, 255, 255))
    out.paste(lab, (0, 0))
    out.paste(c, (0, 24))
    return out


def main() -> int:
    if len(sys.argv) < 4:
        print(__doc__)
        return 2
    merchant_dir, merchant, op = sys.argv[1:4]
    tokens = sys.argv[4:] or DEFAULT_SHORTLIST

    bundle = json.load(open(os.path.join(merchant_dir, "bundle.json")))
    examples = bundle.get("synthetic_training_examples", []) or []
    ex = next((e for e in examples if _op(e) == op), None)
    if ex is None:
        print(f"no candidate for op={op}; available:",
              sorted({_op(e) for e in examples}))
        return 1

    atlas = rsr.cached_glyph_atlas(TABLE, merchant, region=REGION, max_receipts=8)
    profile = rsr.cached_font_profile(TABLE, merchant, region=REGION, max_receipts=12)
    synth = rsr._synthetic_receipt_dict(ex, TV._id_to_label(bundle))

    out_dir = os.path.join(merchant_dir, "_fonts")
    os.makedirs(out_dir, exist_ok=True)
    crops: list[Image.Image] = []

    real = os.path.join(merchant_dir, "original.jpg")
    real_crop = _crop_labeled(real, "REAL")
    if real_crop is not None:
        crops.append(real_crop)

    for token in tokens:
        name, font_path = _resolve_font(token)
        if font_path is not None and not os.path.exists(font_path):
            print(f"  skip {name}: missing {font_path}")
            continue
        png = os.path.join(out_dir, f"{op}__{name}.png")
        rsr._render_cached_hybrid(
            synth, atlas, profile=profile, width=W, height=H, path=png,
            font_path=font_path,
        )
        crop = _crop_labeled(png, name)
        if crop is not None:
            crops.append(crop)
        print(f"  rendered {name} -> {png}")

    if not crops:
        print("no renders produced")
        return 1
    gap = 8
    total_w = sum(c.width for c in crops) + gap * (len(crops) - 1)
    total_h = max(c.height for c in crops)
    strip = Image.new("RGB", (total_w, total_h), (200, 200, 200))
    x = 0
    for c in crops:
        strip.paste(c, (x, 0))
        x += c.width + gap
    out = os.path.join(merchant_dir, "fontbakeoff.png")
    strip.save(out)
    print("STRIP ->", out, strip.size)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
