#!/usr/bin/env python3
"""
Visualize receipt-level OCR lines on the raw image (no clustering).

Usage:
  python scripts/show_receipt_ocr_overlay.py \
    --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
    --receipt-id 1 \
    --output ./receipt_overlays/13da_overlay.png \
    [--raw-bucket raw-image-bucket-...]

This pulls the raw image (raw bucket -> CDN -> blank fallback) and draws all
receipt lines from Dynamo (receipt_id-scope).
"""

import argparse
import sys
from pathlib import Path
from typing import List, Optional, Tuple

repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "receipt_upload"))

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import Receipt, ReceiptLine
from scripts.split_receipt import setup_environment

try:
    from PIL import Image as PIL_Image
    from PIL import ImageDraw, ImageFont
except ImportError:
    print("⚠️  pillow is required. Install with `pip install pillow`.")
    sys.exit(1)

import boto3


def get_image_from_s3(bucket: str, key: str) -> Optional[PIL_Image.Image]:
    s3 = boto3.client("s3")
    try:
        resp = s3.get_object(Bucket=bucket, Key=key)
        return PIL_Image.open(resp["Body"])
    except Exception as exc:
        print(f"⚠️  Could not load s3://{bucket}/{key}: {exc}")
        return None


def load_receipt_image(
    receipt: Receipt,
    raw_bucket_override: Optional[str],
    prefer_cdn: bool = False,
) -> PIL_Image.Image:
    img = None
    # CDN first (preferred)
    if prefer_cdn and receipt.cdn_s3_bucket and receipt.cdn_s3_key:
        img = get_image_from_s3(receipt.cdn_s3_bucket, receipt.cdn_s3_key)
        if img:
            print("✅ Loaded receipt image from CDN (preferred)")

    # Raw fallback (or primary when not preferring CDN)
    raw_bucket = raw_bucket_override or receipt.raw_s3_bucket
    if img is None and raw_bucket and receipt.raw_s3_key:
        img = get_image_from_s3(raw_bucket, receipt.raw_s3_key)
        if img:
            print(f"✅ Loaded receipt raw image: {receipt.raw_s3_key}")

    # CDN fallback if we didn't prefer it first
    if (
        img is None
        and not prefer_cdn
        and receipt.cdn_s3_bucket
        and receipt.cdn_s3_key
    ):
        img = get_image_from_s3(receipt.cdn_s3_bucket, receipt.cdn_s3_key)
        if img:
            print("✅ Loaded receipt image from CDN")

    if img is None:
        img = PIL_Image.new("RGB", (receipt.width, receipt.height), "white")
        print("⚠️  Using blank white receipt image fallback")
    return img


def line_corners_image(
    line: ReceiptLine, w: int, h: int
) -> List[Tuple[float, float]]:
    return [
        (line.top_left["x"] * w, (1.0 - line.top_left["y"]) * h),
        (line.top_right["x"] * w, (1.0 - line.top_right["y"]) * h),
        (line.bottom_right["x"] * w, (1.0 - line.bottom_right["y"]) * h),
        (line.bottom_left["x"] * w, (1.0 - line.bottom_left["y"]) * h),
    ]


def draw_lines(
    img: PIL_Image.Image, lines: List[ReceiptLine], color: str = "#FF0000"
):
    draw = ImageDraw.Draw(img)
    w, h = img.size
    font = None
    try:
        font = ImageFont.truetype("Arial.ttf", 16)
    except Exception:
        font = ImageFont.load_default()

    for idx, line in enumerate(lines, start=1):
        pts = line_corners_image(line, w, h)
        draw.polygon(pts, outline=color, width=3)
        cx = sum(p[0] for p in pts) / 4
        cy = sum(p[1] for p in pts) / 4
        draw.text((cx, cy), f"{idx}", fill=color, font=font)


def main():
    ap = argparse.ArgumentParser(
        description="Draw receipt-level OCR lines on raw image."
    )
    ap.add_argument("--image-id", required=True, help="Image ID")
    ap.add_argument(
        "--receipt-id", type=int, default=1, help="Receipt ID (default: 1)"
    )
    ap.add_argument(
        "--output", required=True, type=Path, help="Output image path"
    )
    ap.add_argument(
        "--prefer-cdn",
        action="store_true",
        help="Load CDN image first (fallback to raw/blank)",
    )
    ap.add_argument("--raw-bucket", required=False, help="Raw bucket override")
    args = ap.parse_args()

    env = setup_environment()
    table_name = env.get("table_name")
    if not table_name:
        raise ValueError("DYNAMODB_TABLE_NAME not set")
    raw_bucket = (
        args.raw_bucket or env.get("raw_bucket") or env.get("raw_bucket_name")
    )

    client = DynamoClient(table_name)
    receipt = client.get_receipt(args.image_id, args.receipt_id)
    lines = client.list_receipt_lines_from_receipt(
        args.image_id, args.receipt_id
    )
    print(
        f"Found {len(lines)} receipt-level lines for receipt {args.receipt_id}"
    )

    img = load_receipt_image(
        receipt, raw_bucket_override=raw_bucket, prefer_cdn=args.prefer_cdn
    )
    draw_lines(img, lines, color="#FF0000")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    img.save(args.output)
    print(f"Saved overlay to {args.output}")


if __name__ == "__main__":
    main()
