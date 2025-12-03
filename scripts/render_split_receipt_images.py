#!/usr/bin/env python3
"""
Render split receipt images locally from JSON data.

This script:
1. Loads split receipt data from local JSON files
2. Downloads the original image from S3
3. Crops and saves each split receipt image locally

Usage:
    python scripts/render_split_receipt_images.py \
        --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
        --output-dir ./rendered_receipt_images
"""

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Optional

# Add repo root to path
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "receipt_upload"))

try:
    from PIL import Image as PIL_Image
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("❌ PIL/Pillow is required")
    sys.exit(1)

import boto3
from receipt_dynamo import DynamoClient
from receipt_upload.utils import download_image_from_s3


def create_receipt_image_from_bounds(
    original_image: PIL_Image.Image,
    bounds: dict,
    image_width: int,
    image_height: int,
) -> Optional[PIL_Image.Image]:
    """
    Create a cropped image for a split receipt from the original image.

    Args:
        original_image: PIL Image object (the actual downloaded image)
        bounds: Normalized coordinates (0-1) in OCR space (y=0 at bottom)
        image_width: Width of the original image (used for coordinate conversion)
        image_height: Height of the original image (used for coordinate conversion)

    Returns:
        Cropped PIL Image, or None if cropping fails
    """
    try:
        # Use actual image dimensions for clamping
        actual_width = original_image.width
        actual_height = original_image.height

        # Convert normalized bounds to pixel coordinates
        # Bounds are in OCR space (y=0 at bottom), convert to image space (y=0 at top) for PIL
        # OCR_y = image_height - image_y, so image_y = image_height - OCR_y
        top_left_x = int(bounds["top_left"]["x"] * image_width)
        top_left_y = int(image_height - bounds["top_left"]["y"] * image_height)  # Top in OCR = convert to image space
        bottom_right_x = int(bounds["bottom_right"]["x"] * image_width)
        bottom_right_y = int(image_height - bounds["bottom_left"]["y"] * image_height)  # Bottom in OCR = convert to image space

        # Clamp to actual image bounds
        top_left_x = max(0, min(top_left_x, actual_width - 1))
        top_left_y = max(0, min(top_left_y, actual_height - 1))
        bottom_right_x = max(top_left_x + 1, min(bottom_right_x, actual_width))
        bottom_right_y = max(top_left_y + 1, min(bottom_right_y, actual_height))

        # Ensure valid crop region
        if bottom_right_x <= top_left_x or bottom_right_y <= top_left_y:
            print(f"⚠️  Invalid crop region: ({top_left_x}, {top_left_y}) to ({bottom_right_x}, {bottom_right_y})")
            return None

        cropped = original_image.crop((top_left_x, top_left_y, bottom_right_x, bottom_right_y))
        return cropped
    except Exception as e:
        print(f"⚠️  Error creating receipt image: {e}")
        import traceback
        traceback.print_exc()
        return None


def main():
    parser = argparse.ArgumentParser(
        description="Render split receipt images locally from JSON data"
    )
    parser.add_argument(
        "--image-id",
        required=True,
        help="Image ID to process",
    )
    parser.add_argument(
        "--local-dir",
        type=Path,
        default=Path("./local_receipt_splits"),
        help="Directory containing local receipt split data (default: ./local_receipt_splits)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./rendered_receipt_images"),
        help="Directory to save rendered images (default: ./rendered_receipt_images)",
    )

    args = parser.parse_args()

    # Load split receipt data from local JSON files
    image_dir = args.local_dir / args.image_id
    if not image_dir.exists():
        print(f"❌ Local directory not found: {image_dir}")
        sys.exit(1)

    print(f"📁 Loading split receipt data from: {image_dir}")

    # Load original receipt to get image info
    original_file = image_dir / "original_receipt.json"
    if not original_file.exists():
        print(f"❌ Original receipt file not found: {original_file}")
        sys.exit(1)

    with open(original_file) as f:
        original_receipt = json.load(f)

    # Get image entity to find raw S3 location
    print(f"📥 Loading image entity from DynamoDB...")
    table_name = os.environ.get("DYNAMODB_TABLE_NAME")
    if not table_name:
        # Try loading from Pulumi
        try:
            from receipt_dynamo.data._pulumi import load_env
            project_root = Path(__file__).parent.parent
            infra_dir = project_root / "infra"
            env = load_env("dev", working_dir=str(infra_dir))
            table_name = env.get("dynamodb_table_name") or env.get("receipts_table_name")
            if table_name:
                os.environ["DYNAMODB_TABLE_NAME"] = table_name
                print(f"📊 DynamoDB Table (from Pulumi): {table_name}")
        except Exception as e:
            print(f"⚠️  Could not load from Pulumi: {e}")

    if not table_name:
        print("❌ DYNAMODB_TABLE_NAME not set")
        sys.exit(1)

    client = DynamoClient(table_name)
    image_entity = client.get_image(args.image_id)

    # Download original image
    print(f"📥 Downloading original image...")
    if not image_entity.raw_s3_bucket or not image_entity.raw_s3_key:
        print(f"❌ Image entity has no raw S3 bucket/key")
        sys.exit(1)

    try:
        image_path = download_image_from_s3(
            image_entity.raw_s3_bucket,
            image_entity.raw_s3_key,
            args.image_id,
        )
        original_image = PIL_Image.open(image_path)
        print(f"   ✅ Loaded image: {original_image.width}x{original_image.height}")
    except Exception as e:
        print(f"   ❌ Could not download original image: {e}")
        sys.exit(1)

    # Load split receipts
    split_receipts = []
    for receipt_dir in sorted(image_dir.glob("receipt_*")):
        receipt_file = receipt_dir / "receipt.json"
        if receipt_file.exists():
            with open(receipt_file) as f:
                split_receipts.append(json.load(f))

    print(f"   Found {len(split_receipts)} split receipts")

    # Create output directory
    output_image_dir = args.output_dir / args.image_id
    output_image_dir.mkdir(parents=True, exist_ok=True)

    # Render each split receipt image
    print(f"\n🎨 Rendering receipt images...")
    for receipt in split_receipts:
        receipt_id = receipt["receipt_id"]
        print(f"   Rendering receipt {receipt_id}...")

        # Get bounds from receipt
        bounds = {
            "top_left": receipt["top_left"],
            "top_right": receipt["top_right"],
            "bottom_left": receipt["bottom_left"],
            "bottom_right": receipt["bottom_right"],
        }

        # Create cropped image
        receipt_image = create_receipt_image_from_bounds(
            original_image,
            bounds,
            image_entity.width,
            image_entity.height,
        )

        if receipt_image:
            # Save image
            output_path = output_image_dir / f"receipt_{receipt_id:05d}.png"
            receipt_image.save(output_path, "PNG")
            print(f"      ✅ Saved: {output_path} ({receipt_image.width}x{receipt_image.height})")
        else:
            print(f"      ⚠️  Failed to create image for receipt {receipt_id}")

    print(f"\n✅ All images rendered to: {output_image_dir}")


if __name__ == "__main__":
    main()

