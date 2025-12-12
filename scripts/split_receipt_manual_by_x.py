#!/usr/bin/env python3
"""
Manually split receipt-level OCR lines by a vertical x-threshold (normalized),
using the receipt record image (CDN first, raw fallback) as background.

Example:
  python scripts/split_receipt_manual_by_x.py \
    --image-id 752cf8e2-cb69-4643-8f28-0ac9e3d2df25 \
    --receipt-id 1 \
    --split-x 0.47 \
    --output-dir ./manual_splits/752c \
    --prefer-cdn \
    --raw-bucket raw-image-bucket-c779c32
"""

import argparse
import json
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

from receipt_upload.cluster import reorder_box_points
from receipt_upload.geometry import box_points, min_area_rect


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


def line_centroid_x(line: ReceiptLine) -> float:
    return (
        line.top_left["x"]
        + line.top_right["x"]
        + line.bottom_left["x"]
        + line.bottom_right["x"]
    ) / 4


def line_corners_image(
    line: ReceiptLine, w: int, h: int
) -> List[Tuple[float, float]]:
    return [
        (line.top_left["x"] * w, (1.0 - line.top_left["y"]) * h),
        (line.top_right["x"] * w, (1.0 - line.top_right["y"]) * h),
        (line.bottom_right["x"] * w, (1.0 - line.bottom_right["y"]) * h),
        (line.bottom_left["x"] * w, (1.0 - line.bottom_left["y"]) * h),
    ]


def bbox_from_lines(
    lines: List[ReceiptLine], w: int, h: int
) -> Optional[dict]:
    if not lines:
        return None
    min_x = min(
        min(
            line.top_left["x"],
            line.top_right["x"],
            line.bottom_left["x"],
            line.bottom_right["x"],
        )
        for line in lines
    )
    max_x = max(
        max(
            line.top_left["x"],
            line.top_right["x"],
            line.bottom_left["x"],
            line.bottom_right["x"],
        )
        for line in lines
    )
    min_y = min(
        min(
            line.top_left["y"],
            line.top_right["y"],
            line.bottom_left["y"],
            line.bottom_right["y"],
        )
        for line in lines
    )
    max_y = max(
        max(
            line.top_left["y"],
            line.top_right["y"],
            line.bottom_left["y"],
            line.bottom_right["y"],
        )
        for line in lines
    )
    pixel_min_x = min_x * w
    pixel_max_x = max_x * w
    pixel_min_y = (1.0 - max_y) * h
    pixel_max_y = (1.0 - min_y) * h
    return {
        "normalized_receipt": {
            "min_x": min_x,
            "max_x": max_x,
            "min_y": min_y,
            "max_y": max_y,
        },
        "pixel": {
            "min_x": pixel_min_x,
            "max_x": pixel_max_x,
            "min_y": pixel_min_y,
            "max_y": pixel_max_y,
        },
    }


def min_rect_from_lines(
    lines: List[ReceiptLine], w: int, h: int
) -> Optional[dict]:
    """
    Compute min-area rectangle (oriented) around all line corners in pixel space.
    Returns TL/TR/BR/BL pixel quad.
    """
    if not lines:
        return None
    pts = []
    for ln in lines:
        pts.extend(line_corners_image(ln, w, h))
    (cx, cy), (rw, rh), angle_deg = min_area_rect(pts)
    box = box_points((cx, cy), (rw, rh), angle_deg)
    ordered = reorder_box_points(box)
    return {
        "center": {"x": cx, "y": cy},
        "size": {"w": rw, "h": rh},
        "angle_deg": angle_deg,
        "pixel_quad": {
            "top_left": {"x": ordered[0][0], "y": ordered[0][1]},
            "top_right": {"x": ordered[1][0], "y": ordered[1][1]},
            "bottom_right": {"x": ordered[2][0], "y": ordered[2][1]},
            "bottom_left": {"x": ordered[3][0], "y": ordered[3][1]},
        },
    }


def draw_overlay(
    base: PIL_Image.Image,
    lines_left: List[ReceiptLine],
    lines_right: List[ReceiptLine],
    split_x: float,
    out_path: Path,
    bbox_left: Optional[dict],
    bbox_right: Optional[dict],
    obox_left: Optional[dict],
    obox_right: Optional[dict],
):
    img = base.copy()
    draw = ImageDraw.Draw(img)
    w, h = img.size
    try:
        font = ImageFont.truetype("Arial.ttf", 16)
    except Exception:
        font = ImageFont.load_default()

    # draw split line
    split_px = split_x * w
    draw.line([(split_px, 0), (split_px, h)], fill="#00FFFF", width=2)

    def draw_lines(lines, color):
        for idx, ln in enumerate(lines, start=1):
            pts = line_corners_image(ln, w, h)
            draw.polygon(pts, outline=color, width=3)
            cx = sum(p[0] for p in pts) / 4
            cy = sum(p[1] for p in pts) / 4
            draw.text((cx, cy), f"{idx}", fill=color, font=font)

    draw_lines(lines_left, "#FF0000")
    draw_lines(lines_right, "#00FF00")

    def draw_bbox(box: Optional[dict], color: str):
        if not box:
            return
        p = box["pixel"]
        draw.rectangle(
            [(p["min_x"], p["min_y"]), (p["max_x"], p["max_y"])],
            outline=color,
            width=3,
        )

    def draw_obox(box: Optional[dict], color: str):
        if not box:
            return
        q = box["pixel_quad"]
        poly = [
            (q["top_left"]["x"], q["top_left"]["y"]),
            (q["top_right"]["x"], q["top_right"]["y"]),
            (q["bottom_right"]["x"], q["bottom_right"]["y"]),
            (q["bottom_left"]["x"], q["bottom_left"]["y"]),
        ]
        draw.line(poly + [poly[0]], fill=color, width=3)

    draw_bbox(bbox_left, "#FF0000")
    draw_bbox(bbox_right, "#00FF00")
    draw_obox(obox_left, "#FF0000")
    draw_obox(obox_right, "#00FF00")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path)
    print(f"Saved overlay: {out_path}")


def main():
    ap = argparse.ArgumentParser(
        description="Manually split receipt OCR lines by x-threshold."
    )
    ap.add_argument("--image-id", required=True, help="Image ID")
    ap.add_argument("--receipt-id", type=int, default=1, help="Receipt ID")
    ap.add_argument(
        "--split-x",
        type=float,
        required=True,
        help="Normalized x threshold (0-1); <= goes to left cluster.",
    )
    ap.add_argument(
        "--output-dir", required=True, type=Path, help="Directory for outputs"
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

    lines_left = [ln for ln in lines if line_centroid_x(ln) <= args.split_x]
    lines_right = [ln for ln in lines if line_centroid_x(ln) > args.split_x]
    print(
        f"Split at x={args.split_x:.3f}: left={len(lines_left)} right={len(lines_right)}"
    )

    img = load_receipt_image(
        receipt, raw_bucket_override=raw_bucket, prefer_cdn=args.prefer_cdn
    )
    bbox_left = bbox_from_lines(lines_left, img.width, img.height)
    bbox_right = bbox_from_lines(lines_right, img.width, img.height)
    obox_left = min_rect_from_lines(lines_left, img.width, img.height)
    obox_right = min_rect_from_lines(lines_right, img.width, img.height)
    out_overlay = (
        args.output_dir / f"{args.image_id}_receipt{args.receipt_id}_split.png"
    )
    draw_overlay(
        img,
        lines_left,
        lines_right,
        args.split_x,
        out_overlay,
        bbox_left,
        bbox_right,
        obox_left,
        obox_right,
    )

    # Export JSON with left/right clusters and basic metadata
    out_json = (
        args.output_dir
        / f"{args.image_id}_receipt{args.receipt_id}_split.json"
    )
    payload = {
        "image_id": args.image_id,
        "receipt_id": args.receipt_id,
        "split_x": args.split_x,
        "left_count": len(lines_left),
        "right_count": len(lines_right),
        "left_bbox": bbox_left,
        "right_bbox": bbox_right,
        "left_obox": obox_left,
        "right_obox": obox_right,
        "left_lines": [
            {
                "line_id": ln.line_id,
                "text": ln.text,
                "ocr_coords": {
                    "top_left": ln.top_left,
                    "top_right": ln.top_right,
                    "bottom_left": ln.bottom_left,
                    "bottom_right": ln.bottom_right,
                },
            }
            for ln in lines_left
        ],
        "right_lines": [
            {
                "line_id": ln.line_id,
                "text": ln.text,
                "ocr_coords": {
                    "top_left": ln.top_left,
                    "top_right": ln.top_right,
                    "bottom_left": ln.bottom_left,
                    "bottom_right": ln.bottom_right,
                },
            }
            for ln in lines_right
        ],
    }
    out_json.parent.mkdir(parents=True, exist_ok=True)
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(f"Saved JSON: {out_json}")


if __name__ == "__main__":
    main()





