#!/usr/bin/env python3
"""
Fetch and save raw receipt images (no clustering).

Usage:
  python scripts/show_raw_receipts.py \
    --image-ids 13da1048-3888-429f-b2aa-b3e15341da5e 752cf8e2-cb69-4643-8f28-0ac9e3d2df25 \
    --output-dir ./raw_receipts_view \
    [--raw-bucket raw-image-bucket-...]

This uses Pulumi outputs (via setup_environment) to find the raw bucket if
one is not provided.
"""

import argparse
import sys
from pathlib import Path
from typing import Optional

repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "receipt_upload"))

from receipt_dynamo import DynamoClient
from scripts.split_receipt import setup_environment

try:
    from PIL import Image as PIL_Image
    from PIL import ImageDraw
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


def load_image(image_entity, raw_bucket: Optional[str]) -> PIL_Image.Image:
    img = None
    if raw_bucket:
        raw_key = image_entity.raw_s3_key or f"raw/{image_entity.image_id}.png"
        img = get_image_from_s3(raw_bucket, raw_key)
        if img:
            print(f"✅ Loaded raw image from bucket: {raw_key}")
    if img is None and image_entity.cdn_s3_bucket and image_entity.cdn_s3_key:
        img = get_image_from_s3(
            image_entity.cdn_s3_bucket, image_entity.cdn_s3_key
        )
        if img:
            print("✅ Loaded image from CDN")
    if img is None:
        img = PIL_Image.new(
            "RGB", (image_entity.width, image_entity.height), "white"
        )
        print("⚠️  Using blank white image fallback")
    return img


def main():
    ap = argparse.ArgumentParser(
        description="Save raw receipt images (receipt_id=1) without clustering."
    )
    ap.add_argument(
        "--image-ids", nargs="+", required=True, help="Image IDs to fetch"
    )
    ap.add_argument(
        "--output-dir",
        required=True,
        type=Path,
        help="Directory to write images",
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

    output_dir: Path = args.output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    client = DynamoClient(table_name)

    for image_id in args.image_ids:
        image_entity = client.get_image(image_id)
        img = load_image(image_entity, raw_bucket)
        out_path = output_dir / f"{image_id}_receipt_1_raw.png"
        img.save(out_path)
        print(f"Saved {out_path}")


if __name__ == "__main__":
    main()






