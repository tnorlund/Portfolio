#!/usr/bin/env python3
"""
Add reclustered receipts (and their images) to Dynamo and S3/CDN.

This script assumes you already have local split results:
- <local-base-dir>/<image-id>/receipts.json
- <local-base-dir>/<image-id>/receipt_0000X/lines.json (and optionally words/letters)
- A merged/original image PNG to crop from.

Workflow per receipt id:
1) Compute a tight bounding box from the receipt's lines in image space.
2) Crop the merged image to that box (with optional padding).
3) Upload the cropped PNG to the raw bucket.
4) Upload CDN variants (jpg/webp/avif + thumbs/small/medium) to the site bucket.
5) Write Receipt and ReceiptLines to DynamoDB using receipt_dynamo entities.

Coordinates:
- Line coordinates are converted from image space (OCR y=0 at bottom) to receipt space
  relative to the cropped image: x' = (x_px - min_x)/crop_w, y' = 1 - (y_px - min_y)/crop_h
- Receipt corners are stored in normalized image coordinates (OCR y=0 bottom).

Usage example:
  python scripts/add_split_receipts_to_dynamo.py \
    --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
    --local-base-dir ./local_receipt_splits \
    --local-image ./local_receipt_data/13da1048-3888-429f-b2aa-b3e15341da5e/13da1048-3888-429f-b2aa-b3e15341da5e_merged.png \
    --receipt-ids 2 3 \
    --table-name ReceiptsTable-dc5be22 \
    --raw-bucket raw-image-bucket-c779c32 \
    --site-bucket sitebucket-ad92f1f

Flags:
- --dry-run: skip all writes/uploads (prints what it would do)
- --no-upload: skip S3 uploads but still write to Dynamo (unless dry-run)
- --padding: fraction of image size added around the computed bbox (default 0.02 = 2%)
"""

import argparse
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

from PIL import Image

repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "receipt_upload"))

from receipt_dynamo import DynamoClient  # noqa: E402
from receipt_dynamo.entities import Receipt, ReceiptLine  # noqa: E402
from receipt_upload.utils import (  # noqa: E402
    upload_all_cdn_formats,
    upload_png_to_s3,
)


def load_receipt_lines(base: Path, receipt_id: int) -> List[Dict]:
    lines_path = base / f"receipt_{receipt_id:05d}" / "lines.json"
    return json.loads(lines_path.read_text())


def load_receipts_meta(base: Path) -> List[Dict]:
    receipts_path = base / "receipts.json"
    return json.loads(receipts_path.read_text())


def ensure_dir(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def ocr_to_image_y(y_norm: float, image_height: int) -> float:
    """OCR space y=0 at bottom; image space y=0 at top."""
    return image_height * (1.0 - y_norm)


def line_bbox_px(
    line: Dict, image_width: int, image_height: int
) -> Tuple[float, float, float, float]:
    """Return min_x, min_y, max_x, max_y in image pixels for a line."""
    xs = []
    ys = []
    for corner in ["top_left", "top_right", "bottom_left", "bottom_right"]:
        c = line[corner]
        xs.append(c["x"] * image_width)
        ys.append(ocr_to_image_y(c["y"], image_height))
    return min(xs), min(ys), max(xs), max(ys)


def normalize_to_receipt(
    corners_px: Dict[str, Tuple[float, float]],
    min_x: float,
    min_y: float,
    w: float,
    h: float,
) -> Dict[str, Dict]:
    """Convert image-pixel corners to receipt-space normalized (y=0 bottom)."""
    norm = {}
    for name, (px, py) in corners_px.items():
        rx = (px - min_x) / w
        ry = 1.0 - ((py - min_y) / h)  # flip back to OCR space
        norm[name] = {"x": rx, "y": ry}
    return norm


def build_receipt_entity(
    image_id: str,
    receipt_id: int,
    crop_min_x: float,
    crop_min_y: float,
    crop_w: float,
    crop_h: float,
    image_w: int,
    image_h: int,
    raw_bucket: str,
    raw_key: str,
    cdn_bucket: str,
    cdn_keys: Dict[str, str],
) -> Receipt:
    # Corners in normalized image coords (OCR space)
    tl_x = crop_min_x / image_w
    tl_y = 1.0 - (crop_min_y / image_h)
    tr_x = (crop_min_x + crop_w) / image_w
    tr_y = tl_y
    bl_x = tl_x
    bl_y = 1.0 - ((crop_min_y + crop_h) / image_h)
    br_x = tr_x
    br_y = bl_y

    return Receipt(
        image_id=image_id,
        receipt_id=receipt_id,
        width=int(crop_w),
        height=int(crop_h),
        timestamp_added=datetime.now(timezone.utc).isoformat(),
        raw_s3_bucket=raw_bucket,
        raw_s3_key=raw_key,
        top_left={"x": tl_x, "y": tl_y},
        top_right={"x": tr_x, "y": tr_y},
        bottom_left={"x": bl_x, "y": bl_y},
        bottom_right={"x": br_x, "y": br_y},
        cdn_s3_bucket=cdn_bucket,
        cdn_s3_key=cdn_keys.get("jpg", ""),
        cdn_webp_s3_key=cdn_keys.get("webp", ""),
        cdn_avif_s3_key=cdn_keys.get("avif", ""),
        cdn_thumbnail_s3_key=cdn_keys.get("thumb_jpg", ""),
        cdn_thumbnail_webp_s3_key=cdn_keys.get("thumb_webp", ""),
        cdn_thumbnail_avif_s3_key=cdn_keys.get("thumb_avif", ""),
        cdn_small_s3_key=cdn_keys.get("small_jpg", ""),
        cdn_small_webp_s3_key=cdn_keys.get("small_webp", ""),
        cdn_small_avif_s3_key=cdn_keys.get("small_avif", ""),
        cdn_medium_s3_key=cdn_keys.get("medium_jpg", ""),
        cdn_medium_webp_s3_key=cdn_keys.get("medium_webp", ""),
        cdn_medium_avif_s3_key=cdn_keys.get("medium_avif", ""),
    )


def build_line_entities(
    lines_raw: List[Dict],
    receipt_id: int,
    image_w: int,
    image_h: int,
    crop_min_x: float,
    crop_min_y: float,
    crop_w: float,
    crop_h: float,
) -> List[ReceiptLine]:
    entities = []
    for ld in lines_raw:
        corners_img_px = {
            name: (
                ld[name]["x"] * image_w,
                ocr_to_image_y(ld[name]["y"], image_h),
            )
            for name in [
                "top_left",
                "top_right",
                "bottom_right",
                "bottom_left",
            ]
        }
        corners_receipt = normalize_to_receipt(
            corners_img_px, crop_min_x, crop_min_y, crop_w, crop_h
        )
        bbox = {
            "x": (corners_img_px["top_left"][0] - crop_min_x) / crop_w,
            "y": 1.0
            - (
                (corners_img_px["bottom_left"][1] - crop_min_y) / crop_h
            ),  # bottom in OCR
            "width": (
                corners_img_px["top_right"][0] - corners_img_px["top_left"][0]
            )
            / crop_w,
            "height": (
                corners_img_px["top_left"][1]
                - corners_img_px["bottom_left"][1]
            )
            / crop_h,
        }
        entities.append(
            ReceiptLine(
                receipt_id=receipt_id,
                image_id=ld["image_id"],
                line_id=ld["line_id"],
                text=ld.get("text", ""),
                bounding_box=bbox,
                top_left=corners_receipt["top_left"],
                top_right=corners_receipt["top_right"],
                bottom_left=corners_receipt["bottom_left"],
                bottom_right=corners_receipt["bottom_right"],
                angle_degrees=ld.get("angle_degrees", 0.0),
                angle_radians=ld.get("angle_radians", 0.0),
                confidence=ld.get("confidence", 0.0),
            )
        )
    return entities


def compute_bbox_for_receipt(
    lines_raw: List[Dict], image_w: int, image_h: int, padding: float
) -> Tuple[float, float, float, float]:
    min_x = min_y = 1e9
    max_x = max_y = -1e9
    for ld in lines_raw:
        lx0, ly0, lx1, ly1 = line_bbox_px(ld, image_w, image_h)
        min_x = min(min_x, lx0)
        min_y = min(min_y, ly0)
        max_x = max(max_x, lx1)
        max_y = max(max_y, ly1)
    # Apply padding in pixels
    pad_x = padding * image_w
    pad_y = padding * image_h
    min_x = max(0, min_x - pad_x)
    min_y = max(0, min_y - pad_y)
    max_x = min(image_w, max_x + pad_x)
    max_y = min(image_h, max_y + pad_y)
    return min_x, min_y, max_x, max_y


def upload_images(
    local_png: Path,
    raw_bucket: str,
    raw_key: str,
    site_bucket: str,
    base_cdn_key: str,
    do_upload: bool,
) -> Dict[str, str]:
    if not do_upload:
        return {
            "raw_key": raw_key,
            "jpg": f"{base_cdn_key}.jpg",
            "webp": f"{base_cdn_key}.webp",
            "avif": f"{base_cdn_key}.avif",
            "thumb_jpg": f"{base_cdn_key}_thumbnail.jpg",
            "thumb_webp": f"{base_cdn_key}_thumbnail.webp",
            "thumb_avif": f"{base_cdn_key}_thumbnail.avif",
            "small_jpg": f"{base_cdn_key}_small.jpg",
            "small_webp": f"{base_cdn_key}_small.webp",
            "small_avif": f"{base_cdn_key}_small.avif",
            "medium_jpg": f"{base_cdn_key}_medium.jpg",
            "medium_webp": f"{base_cdn_key}_medium.webp",
            "medium_avif": f"{base_cdn_key}_medium.avif",
        }

    img_obj = Image.open(local_png)
    upload_png_to_s3(img_obj, raw_bucket, raw_key)
    cdn_keys = upload_all_cdn_formats(
        image=img_obj,
        s3_bucket=site_bucket,
        base_key=base_cdn_key,
    )
    return {
        "raw_key": raw_key,
        "jpg": cdn_keys.get("jpeg_full") or cdn_keys.get("jpeg"),
        "webp": cdn_keys.get("webp_full") or cdn_keys.get("webp"),
        "avif": cdn_keys.get("avif_full") or cdn_keys.get("avif"),
        "thumb_jpg": cdn_keys.get("jpeg_thumbnail"),
        "thumb_webp": cdn_keys.get("webp_thumbnail"),
        "thumb_avif": cdn_keys.get("avif_thumbnail"),
        "small_jpg": cdn_keys.get("jpeg_small"),
        "small_webp": cdn_keys.get("webp_small"),
        "small_avif": cdn_keys.get("avif_small"),
        "medium_jpg": cdn_keys.get("jpeg_medium"),
        "medium_webp": cdn_keys.get("webp_medium"),
        "medium_avif": cdn_keys.get("avif_medium"),
    }


def main():
    ap = argparse.ArgumentParser(
        description="Add reclustered receipts and upload images."
    )
    ap.add_argument("--image-id", required=True)
    ap.add_argument("--local-base-dir", required=True, type=Path)
    ap.add_argument("--local-image", required=True, type=Path)
    ap.add_argument(
        "--receipt-ids",
        type=int,
        nargs="+",
        help="Receipt IDs to add (e.g., 2 3). Default: all split receipts except 1.",
    )
    ap.add_argument(
        "--table-name",
        default=os.environ.get("DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22"),
    )
    ap.add_argument("--raw-bucket", default=os.environ.get("RAW_BUCKET"))
    ap.add_argument("--site-bucket", default=os.environ.get("SITE_BUCKET"))
    ap.add_argument(
        "--padding",
        type=float,
        default=0.02,
        help="Padding fraction of image size added around bbox.",
    )
    ap.add_argument(
        "--dry-run",
        action="store_true",
        help="Do not write to Dynamo or upload to S3.",
    )
    ap.add_argument(
        "--no-upload",
        action="store_true",
        help="Skip S3 uploads (Dynamo writes still happen unless dry-run).",
    )
    args = ap.parse_args()

    if not args.raw_bucket or not args.site_bucket:
        print("RAW_BUCKET and SITE_BUCKET are required (env or flags).")
        sys.exit(1)

    image_id = args.image_id
    base = args.local_base_dir / image_id
    receipts_meta = load_receipts_meta(base)
    receipt_ids = args.receipt_ids
    if not receipt_ids:
        receipt_ids = []
        for rdir in base.glob("receipt_*"):
            rid = int(rdir.name.split("_")[-1])
            if rid != 1:
                receipt_ids.append(rid)
        receipt_ids.sort()

    img = Image.open(args.local_image)
    image_w, image_h = img.width, img.height

    if not args.dry_run:
        client = DynamoClient(args.table_name)

    for rid in receipt_ids:
        lines_raw = load_receipt_lines(base, rid)
        if not lines_raw:
            print(f"⚠️  No lines for receipt {rid}, skipping")
            continue

        min_x, min_y, max_x, max_y = compute_bbox_for_receipt(
            lines_raw, image_w, image_h, args.padding
        )
        crop_w = max_x - min_x
        crop_h = max_y - min_y
        if crop_w <= 0 or crop_h <= 0:
            print(f"⚠️  Invalid bbox for receipt {rid}, skipping")
            continue

        with tempfile.TemporaryDirectory() as td:
            crop_path = Path(td) / f"{image_id}_RECEIPT_{rid:05d}.png"
            crop_img = img.crop((min_x, min_y, max_x, max_y))
            crop_img.save(crop_path)

            raw_key = f"raw/{image_id}_RECEIPT_{rid:05d}.png"
            base_cdn_key = f"assets/{image_id}_RECEIPT_{rid:05d}"
            cdn_keys = upload_images(
                crop_path,
                args.raw_bucket,
                raw_key,
                args.site_bucket,
                base_cdn_key,
                do_upload=not args.no_upload and not args.dry_run,
            )

        receipt_entity = build_receipt_entity(
            image_id=image_id,
            receipt_id=rid,
            crop_min_x=min_x,
            crop_min_y=min_y,
            crop_w=crop_w,
            crop_h=crop_h,
            image_w=image_w,
            image_h=image_h,
            raw_bucket=args.raw_bucket,
            raw_key=cdn_keys.get("raw_key", raw_key),
            cdn_bucket=args.site_bucket,
            cdn_keys=cdn_keys,
        )

        line_entities = build_line_entities(
            lines_raw,
            receipt_id=rid,
            image_w=image_w,
            image_h=image_h,
            crop_min_x=min_x,
            crop_min_y=min_y,
            crop_w=crop_w,
            crop_h=crop_h,
        )

        print(
            f"✅ Prepared receipt {rid}: {len(line_entities)} lines, crop {int(crop_w)}x{int(crop_h)}"
        )
        if args.dry_run:
            continue

        client.add_receipt(receipt_entity)
        client.add_receipt_lines(line_entities)
        print(f"   ➜ Added receipt {rid} to Dynamo (table {args.table_name})")


if __name__ == "__main__":
    main()



