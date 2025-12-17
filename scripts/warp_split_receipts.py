#!/usr/bin/env python3
"""
Warp the two manual splits into separate cropped images using oriented boxes,
and overlay transformed OCR for each cluster.

Inputs: the JSON produced by split_receipt_manual_by_x.py with left_obox/right_obox

Example:
  python scripts/warp_split_receipts.py \
    --image-id 752cf8e2-cb69-4643-8f28-0ac9e3d2df25 \
    --receipt-id 1 \
    --split-json manual_splits/752c/752cf8e2-cb69-4643-8f28-0ac9e3d2df25_receipt1_split.json \
    --output-dir warped_splits/752c \
    --prefer-cdn
"""

import argparse
import json
import sys
from pathlib import Path
from typing import List, Tuple

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

from receipt_upload.geometry.transformations import (
    find_perspective_coeffs,
    invert_warp,
)


def get_image_from_s3(bucket: str, key: str) -> PIL_Image.Image:
    s3 = boto3.client("s3")
    resp = s3.get_object(Bucket=bucket, Key=key)
    return PIL_Image.open(resp["Body"])


def load_receipt_image(
    receipt: Receipt, raw_bucket_override: str | None, prefer_cdn: bool
) -> PIL_Image.Image:
    img = None
    if prefer_cdn and receipt.cdn_s3_bucket and receipt.cdn_s3_key:
        try:
            img = get_image_from_s3(receipt.cdn_s3_bucket, receipt.cdn_s3_key)
            print("✅ Loaded receipt image from CDN (preferred)")
        except Exception:
            img = None
    if (
        img is None
        and (raw_bucket_override or receipt.raw_s3_bucket)
        and receipt.raw_s3_key
    ):
        bucket = raw_bucket_override or receipt.raw_s3_bucket
        try:
            img = get_image_from_s3(bucket, receipt.raw_s3_key)
            print(f"✅ Loaded receipt raw image: {receipt.raw_s3_key}")
        except Exception:
            img = None
    if (
        img is None
        and not prefer_cdn
        and receipt.cdn_s3_bucket
        and receipt.cdn_s3_key
    ):
        try:
            img = get_image_from_s3(receipt.cdn_s3_bucket, receipt.cdn_s3_key)
            print("✅ Loaded receipt image from CDN")
        except Exception:
            img = None
    if img is None:
        img = PIL_Image.new("RGB", (receipt.width, receipt.height), "white")
        print("⚠️  Using blank white receipt image fallback")
    return img


def apply_coeffs(
    pt: Tuple[float, float], coeffs: List[float]
) -> Tuple[float, float]:
    x, y = pt
    a, b, c, d, e, f, g, h = coeffs
    denom = g * x + h * y + 1.0
    if abs(denom) < 1e-12:
        return (0.0, 0.0)
    x2 = (a * x + b * y + c) / denom
    y2 = (d * x + e * y + f) / denom
    return (x2, y2)


def _edge_length(p1, p2):
    return ((p1[0] - p2[0]) ** 2 + (p1[1] - p2[1]) ** 2) ** 0.5


def warp_cluster(
    img: PIL_Image.Image,
    obox: dict,
    lines: List[ReceiptLine],
    color: str,
    out_prefix: Path,
):
    q = obox["pixel_quad"]
    src = [
        (q["top_left"]["x"], q["top_left"]["y"]),
        (q["top_right"]["x"], q["top_right"]["y"]),
        (q["bottom_right"]["x"], q["bottom_right"]["y"]),
        (q["bottom_left"]["x"], q["bottom_left"]["y"]),
    ]
    # destination rectangle dimensions based on edge lengths
    top_len = _edge_length(src[0], src[1])
    bottom_len = _edge_length(src[3], src[2])
    left_len = _edge_length(src[0], src[3])
    right_len = _edge_length(src[1], src[2])
    w = int(round(max(top_len, bottom_len)))
    h = int(round(max(left_len, right_len)))
    w = max(1, w)
    h = max(1, h)
    dst = [(0, 0), (w - 1, 0), (w - 1, h - 1), (0, h - 1)]

    # PIL expects coeffs mapping output->input (dst->src)
    coeffs_dst_to_src = find_perspective_coeffs(src_points=src, dst_points=dst)
    warped = img.transform(
        (w, h),
        PIL_Image.PERSPECTIVE,
        coeffs_dst_to_src,
        resample=PIL_Image.BICUBIC,
    )

    # For mapping OCR points src->dst, invert the coeffs
    coeffs_src_to_dst = invert_warp(*coeffs_dst_to_src)

    def transform_line(ln: ReceiptLine):
        pts = [
            (
                ln.top_left["x"] * img.width,
                (1 - ln.top_left["y"]) * img.height,
            ),
            (
                ln.top_right["x"] * img.width,
                (1 - ln.top_right["y"]) * img.height,
            ),
            (
                ln.bottom_right["x"] * img.width,
                (1 - ln.bottom_right["y"]) * img.height,
            ),
            (
                ln.bottom_left["x"] * img.width,
                (1 - ln.bottom_left["y"]) * img.height,
            ),
        ]
        warped_pts = [apply_coeffs(p, coeffs_src_to_dst) for p in pts]
        return warped_pts

    # draw overlay
    overlay = warped.copy()
    draw = ImageDraw.Draw(overlay)
    try:
        font = ImageFont.truetype("Arial.ttf", 14)
    except Exception:
        font = ImageFont.load_default()

    warped_lines = []
    for idx, ln in enumerate(lines, start=1):
        pts = transform_line(ln)
        draw.polygon(pts, outline=color, width=2)
        cx = sum(p[0] for p in pts) / 4
        cy = sum(p[1] for p in pts) / 4
        draw.text((cx, cy), f"{idx}", fill=color, font=font)
        # normalized coords in warped space (y=0 top in pixels -> flip to y=0 bottom norm)
        norm_pts = [{"x": p[0] / w, "y": 1.0 - (p[1] / h)} for p in pts]
        warped_lines.append(
            {
                "line_id": ln.line_id,
                "text": ln.text,
                "warped_pixels": [{"x": p[0], "y": p[1]} for p in pts],
                "warped_normalized": norm_pts,
            }
        )

    out_prefix.parent.mkdir(parents=True, exist_ok=True)
    warped_path = out_prefix.parent / f"{out_prefix.name}_clean.png"
    overlay_path = out_prefix.parent / f"{out_prefix.name}_overlay.png"
    json_path = out_prefix.parent / f"{out_prefix.name}_ocr.json"
    warped.save(warped_path)
    overlay.save(overlay_path)
    json_path.write_text(
        json.dumps(
            {
                "width": w,
                "height": h,
                "obox": obox,
                "lines": warped_lines,
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    print(f"Saved warped: {warped_path}")
    print(f"Saved overlay: {overlay_path}")
    print(f"Saved OCR JSON: {json_path}")


def main():
    ap = argparse.ArgumentParser(
        description="Warp clusters into new receipt images using oriented boxes."
    )
    ap.add_argument("--image-id", required=True)
    ap.add_argument("--receipt-id", type=int, default=1)
    ap.add_argument("--split-json", required=True, type=Path)
    ap.add_argument("--output-dir", required=True, type=Path)
    ap.add_argument("--prefer-cdn", action="store_true")
    ap.add_argument("--raw-bucket", required=False)
    args = ap.parse_args()

    env = setup_environment()
    table = env.get("table_name")
    if not table:
        raise ValueError("DYNAMODB_TABLE_NAME not set")
    raw_bucket = (
        args.raw_bucket or env.get("raw_bucket") or env.get("raw_bucket_name")
    )

    split_data = json.loads(args.split_json.read_text())
    obox_left = split_data.get("left_obox")
    obox_right = split_data.get("right_obox")

    client = DynamoClient(table)
    receipt = client.get_receipt(args.image_id, args.receipt_id)
    lines = client.list_receipt_lines_from_receipt(
        args.image_id, args.receipt_id
    )

    # reload lines in same order as split (using ids)
    left_ids = {ln["line_id"] for ln in split_data["left_lines"]}
    right_ids = {ln["line_id"] for ln in split_data["right_lines"]}
    lines_left = [ln for ln in lines if ln.line_id in left_ids]
    lines_right = [ln for ln in lines if ln.line_id in right_ids]

    img = load_receipt_image(
        receipt, raw_bucket_override=raw_bucket, prefer_cdn=args.prefer_cdn
    )

    args.output_dir.mkdir(parents=True, exist_ok=True)
    base_prefix_left = (
        args.output_dir / f"{args.image_id}_receipt{args.receipt_id}_left"
    )
    base_prefix_right = (
        args.output_dir / f"{args.image_id}_receipt{args.receipt_id}_right"
    )

    if obox_left:
        warp_cluster(img, obox_left, lines_left, "#FF0000", base_prefix_left)
    if obox_right:
        warp_cluster(
            img, obox_right, lines_right, "#00AA00", base_prefix_right
        )


if __name__ == "__main__":
    main()






