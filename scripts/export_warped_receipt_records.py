#!/usr/bin/env python3
"""
Create Dynamo-style JSON records for a warped receipt image + OCR, so they can be
round-tripped with Receipt(**json_data) and ReceiptLine(**json_data).

Inputs:
  --image-id           Original image_id
  --receipt-id         New receipt_id to assign (e.g., 2 or 3)
  --ocr-json           Path to warped OCR JSON produced by warp_split_receipts.py
  --warped-image       Path to warped PNG (for width/height)
  --output             Output JSON file to write (contains receipt and lines)

Notes:
- Corners are set to the full warped image: TL(0,1), TR(1,1), BL(0,0), BR(1,0)
  in normalized coordinates (y=0 at bottom), consistent with existing records.
- raw/cdn bucket/key are left empty; fill them if you upload the warped PNGs.
"""

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from PIL import Image as PIL_Image

from receipt_dynamo.entities import Receipt, ReceiptLine


def main():
    ap = argparse.ArgumentParser(
        description="Export warped receipt + lines as Receipt/ReceiptLine JSON."
    )
    ap.add_argument("--image-id", required=True)
    ap.add_argument("--receipt-id", type=int, required=True)
    ap.add_argument("--ocr-json", required=True, type=Path)
    ap.add_argument("--warped-image", required=True, type=Path)
    ap.add_argument("--output", required=True, type=Path)
    args = ap.parse_args()

    ocr = json.loads(args.ocr_json.read_text())
    img = PIL_Image.open(args.warped_image)
    width, height = img.size

    timestamp = datetime.now(timezone.utc).isoformat()

    # Normalized corners for full warped image (y=0 bottom)
    tl = {"x": 0.0, "y": 1.0}
    tr = {"x": 1.0, "y": 1.0}
    bl = {"x": 0.0, "y": 0.0}
    br = {"x": 1.0, "y": 0.0}

    receipt = Receipt(
        image_id=args.image_id,
        receipt_id=args.receipt_id,
        width=width,
        height=height,
        timestamp_added=timestamp,
        raw_s3_bucket="",
        raw_s3_key="",
        top_left=tl,
        top_right=tr,
        bottom_left=bl,
        bottom_right=br,
        sha256=None,
        cdn_s3_bucket=None,
        cdn_s3_key=None,
        cdn_webp_s3_key=None,
        cdn_avif_s3_key=None,
        cdn_thumbnail_s3_key=None,
        cdn_thumbnail_webp_s3_key=None,
        cdn_thumbnail_avif_s3_key=None,
        cdn_small_s3_key=None,
        cdn_small_webp_s3_key=None,
        cdn_small_avif_s3_key=None,
        cdn_medium_s3_key=None,
        cdn_medium_webp_s3_key=None,
        cdn_medium_avif_s3_key=None,
    )

    lines = []
    for ln in ocr.get("lines", []):
        pts = ln["warped_normalized"]
        tl_n, tr_n, br_n, bl_n = pts
        min_x = min(p["x"] for p in pts)
        max_x = max(p["x"] for p in pts)
        min_y = min(p["y"] for p in pts)
        max_y = max(p["y"] for p in pts)
        bbox = {
            "x": min_x,
            "y": min_y,
            "width": max_x - min_x,
            "height": max_y - min_y,
        }
        lines.append(
            ReceiptLine(
                image_id=args.image_id,
                receipt_id=args.receipt_id,
                line_id=ln["line_id"],
                text=ln.get("text", ""),
                confidence=1.0,
                angle_degrees=0.0,
                angle_radians=0.0,
                bounding_box=bbox,
                top_left={"x": tl_n["x"], "y": tl_n["y"]},
                top_right={"x": tr_n["x"], "y": tr_n["y"]},
                bottom_right={"x": br_n["x"], "y": br_n["y"]},
                bottom_left={"x": bl_n["x"], "y": bl_n["y"]},
            )
        )

    payload = {
        "receipt": receipt.__dict__,
        "lines": [ln.__dict__ for ln in lines],
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Wrote receipt+lines JSON to {args.output}")


if __name__ == "__main__":
    main()






