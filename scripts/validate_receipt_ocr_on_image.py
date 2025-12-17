#!/usr/bin/env python3
"""
Overlay OCR data onto a clean receipt image to visually confirm correctness.

Assumptions:
- OCR coords are normalized (0-1) in OCR space, where y=0 is at the bottom.
- Image space (PIL) has y=0 at the top.

Input JSON format (minimal):
{
  "lines": [
    {
      "line_id": 1,
      "text": "...",
      "top_left": {"x": 0.1, "y": 0.9},
      "top_right": {"x": 0.4, "y": 0.9},
      "bottom_right": {"x": 0.4, "y": 0.8},
      "bottom_left": {"x": 0.1, "y": 0.8}
    },
    ...
  ]
}

Usage:
  python scripts/validate_receipt_ocr_on_image.py \
    --image ./split_out_13da/receipt_1_clean.png \
    --ocr-json ./split_out_13da/receipt_1_ocr_export.json \
    --output ./split_out_13da/receipt_1_overlay.png
"""

import argparse
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def ocr_to_image_xy(
    x_norm: float, y_norm: float, width: int, height: int
) -> tuple[float, float]:
    """Convert OCR space (y=0 bottom) to image pixels (y=0 top)."""
    return x_norm * width, height - (y_norm * height)


def draw_lines(
    draw: ImageDraw.ImageDraw, lines: list[dict], width: int, height: int
) -> None:
    colors = [
        (255, 0, 0),
        (0, 255, 0),
        (0, 0, 255),
        (255, 165, 0),
        (128, 0, 128),
        (255, 192, 203),
        (0, 255, 255),
        (255, 255, 0),
    ]
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 16)
    except Exception:
        font = ImageFont.load_default()

    for idx, line in enumerate(lines):
        color = colors[idx % len(colors)]
        corners = []
        # Some exports nest coords under "ocr_coords"
        coord_source = line
        if "ocr_coords" in line:
            coord_source = line["ocr_coords"]
        for name in ["top_left", "top_right", "bottom_right", "bottom_left"]:
            c = coord_source[name]
            corners.append(ocr_to_image_xy(c["x"], c["y"], width, height))

        # draw polygon
        draw.polygon(corners, outline=color, width=2)

        # optional text label near top_left
        tl = corners[0]
        label = f"{line.get('line_id', '?')}: {line.get('text', '')}"
        draw.text((tl[0] + 2, tl[1] - 14), label, fill=color, font=font)


def main():
    ap = argparse.ArgumentParser(
        description="Overlay OCR data onto a clean receipt image."
    )
    ap.add_argument(
        "--image",
        required=True,
        type=Path,
        help="Path to clean receipt image (PNG/JPG).",
    )
    ap.add_argument(
        "--ocr-json",
        required=True,
        type=Path,
        help="Path to OCR JSON (expects 'lines' array).",
    )
    ap.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Path to save overlay image.",
    )
    args = ap.parse_args()

    img = Image.open(args.image).convert("RGB")
    width, height = img.width, img.height

    data = json.loads(args.ocr_json.read_text())
    lines = data.get("lines", [])
    if not lines:
        print("⚠️  No lines found in OCR JSON.")

    draw = ImageDraw.Draw(img)
    draw_lines(draw, lines, width, height)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    img.save(args.output)
    print(f"✅ Saved overlay to {args.output}")


if __name__ == "__main__":
    main()






