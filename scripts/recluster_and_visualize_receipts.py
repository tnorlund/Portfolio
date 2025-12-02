#!/usr/bin/env python3
"""
Re-cluster an existing receipt using the new clustering algorithm and visualize results.

This script:
1. Loads existing receipt and receipt OCR data
2. Re-clusters using the new two-phase approach
3. Creates new receipt entities with transformed receipt OCR data
4. Visualizes the cropped receipts with bounding boxes

Usage:
    python scripts/recluster_and_visualize_receipts.py \
        --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
        --original-receipt-id 1 \
        --output-dir ./recluster_visualizations \
        --dry-run
"""

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

# Add repo root to path
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "receipt_upload"))

import boto3
from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import (
    Receipt,
    ReceiptLine,
    ReceiptWord,
    ReceiptLetter,
    Line,
)

# Import the same recluster function used in split_receipt.py
from scripts.split_receipt import recluster_receipt_lines

# Image processing imports
try:
    from PIL import Image as PIL_Image, ImageDraw, ImageFont
    IMAGE_PROCESSING_AVAILABLE = True
except ImportError:
    IMAGE_PROCESSING_AVAILABLE = False
    print("⚠️  PIL not available - visualization will be limited")

# Transform utilities
try:
    from receipt_upload.geometry.transformations import invert_warp
    TRANSFORM_AVAILABLE = True
except ImportError:
    TRANSFORM_AVAILABLE = False
    print("⚠️  Transform utilities not available")


def setup_environment() -> Dict[str, str]:
    """Load environment variables from Pulumi or environment."""
    import os
    import subprocess

    env = {}

    # Try to load from Pulumi
    try:
        result = subprocess.run(
            ["pulumi", "stack", "output", "--json"],
            capture_output=True,
            text=True,
            check=True,
        )
        pulumi_outputs = json.loads(result.stdout)

        env["dynamo_table_name"] = pulumi_outputs.get("dynamo_table_name", "")
        env["raw_bucket"] = pulumi_outputs.get("raw_image_bucket_name", "")
        env["site_bucket"] = pulumi_outputs.get("cdn_bucket_name", "")
    except (subprocess.CalledProcessError, json.JSONDecodeError, FileNotFoundError):
        pass

    # Fall back to environment variables
    env["dynamo_table_name"] = env.get("dynamo_table_name") or os.environ.get(
        "DYNAMODB_TABLE_NAME", "ReceiptsTable-dc5be22"
    )
    env["raw_bucket"] = env.get("raw_bucket") or os.environ.get("RAW_BUCKET", "")
    env["site_bucket"] = env.get("site_bucket") or os.environ.get("SITE_BUCKET", "")

    return env


# recluster_receipt_lines is now imported from scripts.split_receipt


def calculate_receipt_bounds(
    words: List[ReceiptWord],
    original_receipt: Receipt,
    image_width: int,
    image_height: int,
) -> Dict[str, Any]:
    """Calculate bounding box for a receipt from its words."""
    if not words:
        raise ValueError("No words provided to calculate bounds")

    # Get original receipt bounds in absolute image coordinates
    receipt_min_x = original_receipt.top_left["x"] * image_width
    receipt_max_x = original_receipt.top_right["x"] * image_width
    receipt_min_y = original_receipt.bottom_left["y"] * image_height  # Bottom in OCR space
    receipt_max_y = original_receipt.top_left["y"] * image_height  # Top in OCR space

    receipt_width = receipt_max_x - receipt_min_x
    receipt_height = receipt_max_y - receipt_min_y

    all_x_coords = []
    all_y_coords = []

    for word in words:
        # Convert from receipt-relative (0-1) to absolute image coordinates
        word_top_left_x = receipt_min_x + word.top_left["x"] * receipt_width
        word_top_left_y = receipt_min_y + word.top_left["y"] * receipt_height
        word_top_right_x = receipt_min_x + word.top_right["x"] * receipt_width
        word_top_right_y = receipt_min_y + word.top_right["y"] * receipt_height
        word_bottom_left_x = receipt_min_x + word.bottom_left["x"] * receipt_width
        word_bottom_left_y = receipt_min_y + word.bottom_left["y"] * receipt_height
        word_bottom_right_x = receipt_min_x + word.bottom_right["x"] * receipt_width
        word_bottom_right_y = receipt_min_y + word.bottom_right["y"] * receipt_height

        all_x_coords.extend([
            word_top_left_x,
            word_top_right_x,
            word_bottom_left_x,
            word_bottom_right_x,
        ])
        all_y_coords.extend([
            word_top_left_y,
            word_top_right_y,
            word_bottom_left_y,
            word_bottom_right_y,
        ])

    min_x = min(all_x_coords)
    max_x = max(all_x_coords)
    min_y = min(all_y_coords)  # Bottom in OCR space
    max_y = max(all_y_coords)  # Top in OCR space

    # Clamp to image bounds
    return {
        "top_left": {
            "x": max(0, min_x) / image_width,
            "y": min(image_height, max_y) / image_height,  # Top in OCR space
        },
        "top_right": {
            "x": min(image_width, max_x) / image_width,
            "y": min(image_height, max_y) / image_height,
        },
        "bottom_left": {
            "x": max(0, min_x) / image_width,
            "y": max(0, min_y) / image_height,  # Bottom in OCR space
        },
        "bottom_right": {
            "x": min(image_width, max_x) / image_width,
            "y": max(0, min_y) / image_height,
        },
    }


def create_receipt_image(
    original_image: PIL_Image.Image,
    bounds: Dict[str, Any],
    image_width: int,
    image_height: int,
) -> PIL_Image.Image:
    """Crop and create receipt image from original image."""
    # Convert bounds to absolute coordinates
    min_x = int(bounds["top_left"]["x"] * image_width)
    max_x = int(bounds["top_right"]["x"] * image_width)
    min_y_ocr = bounds["bottom_left"]["y"] * image_height  # Bottom in OCR space
    max_y_ocr = bounds["top_left"]["y"] * image_height  # Top in OCR space

    # Convert to PIL space (y=0 at top)
    min_y_pil = image_height - max_y_ocr  # Top in OCR -> top in PIL
    max_y_pil = image_height - min_y_ocr  # Bottom in OCR -> bottom in PIL

    # Crop the image
    cropped = original_image.crop((min_x, min_y_pil, max_x, max_y_pil))
    return cropped


def transform_receipt_ocr_to_new_receipt_space(
    receipt_entity: ReceiptLine | ReceiptWord | ReceiptLetter,
    original_receipt: Receipt,
    new_receipt_bounds: Dict[str, Any],
    image_width: int,
    image_height: int,
) -> Dict[str, Any]:
    """Transform receipt OCR entity coordinates from original receipt space to new receipt space."""
    import copy

    # Step 1: Transform from original receipt space to image space
    transform_coeffs, orig_receipt_width, orig_receipt_height = original_receipt.get_transform_to_image(
        image_width, image_height
    )

    entity_copy = copy.deepcopy(receipt_entity)
    forward_coeffs = invert_warp(*transform_coeffs)
    entity_copy.warp_transform(
        *forward_coeffs,
        src_width=image_width,
        src_height=image_height,
        dst_width=orig_receipt_width,
        dst_height=orig_receipt_height,
        flip_y=True,
    )

    # Convert to absolute image coordinates
    corners_img = {}
    for corner_name in ["top_left", "top_right", "bottom_left", "bottom_right"]:
        corner = getattr(entity_copy, corner_name)
        corners_img[corner_name] = {
            "x": corner["x"] * image_width,
            "y": corner["y"] * image_height,
        }

    # Step 2: Transform from image space to new receipt space
    new_receipt_min_x_abs = new_receipt_bounds["top_left"]["x"] * image_width
    new_receipt_max_x_abs = new_receipt_bounds["top_right"]["x"] * image_width
    new_receipt_min_y_abs = new_receipt_bounds["bottom_left"]["y"] * image_height
    new_receipt_max_y_abs = new_receipt_bounds["top_left"]["y"] * image_height
    new_receipt_width_abs = new_receipt_max_x_abs - new_receipt_min_x_abs
    new_receipt_height_abs = new_receipt_max_y_abs - new_receipt_min_y_abs

    def img_to_receipt_coord(img_x, img_y):
        ocr_y = image_height - img_y
        receipt_x = (img_x - new_receipt_min_x_abs) / new_receipt_width_abs if new_receipt_width_abs > 0 else 0.0
        receipt_y = (ocr_y - new_receipt_min_y_abs) / new_receipt_height_abs if new_receipt_height_abs > 0 else 0.0
        return receipt_x, receipt_y

    corners_receipt = {}
    for corner_name, corner_img in corners_img.items():
        receipt_x, receipt_y = img_to_receipt_coord(corner_img["x"], corner_img["y"])
        corners_receipt[corner_name] = {"x": receipt_x, "y": receipt_y}

    return corners_receipt


def visualize_receipt_with_boxes(
    receipt_image: PIL_Image.Image,
    receipt_lines: List[ReceiptLine],
    receipt_words: List[ReceiptWord],
    output_path: Path,
    receipt_id: int,
):
    """Visualize receipt with bounding boxes for lines and words."""
    if not IMAGE_PROCESSING_AVAILABLE:
        print("⚠️  Cannot create visualization - PIL not available")
        return

    img = receipt_image.copy()
    draw = ImageDraw.Draw(img)

    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 12)
    except:
        font = ImageFont.load_default()

    receipt_width = img.width
    receipt_height = img.height

    # Draw lines in red
    for line in receipt_lines:
        # Line coordinates are normalized 0-1 relative to receipt, OCR space
        # Convert to absolute receipt coordinates (PIL space)
        tl_x = line.top_left["x"] * receipt_width
        tl_y = (1 - line.top_left["y"]) * receipt_height  # Flip Y: OCR -> PIL
        tr_x = line.top_right["x"] * receipt_width
        tr_y = (1 - line.top_right["y"]) * receipt_height
        br_x = line.bottom_right["x"] * receipt_width
        br_y = (1 - line.bottom_right["y"]) * receipt_height
        bl_x = line.bottom_left["x"] * receipt_width
        bl_y = (1 - line.bottom_left["y"]) * receipt_height

        corners = [(tl_x, tl_y), (tr_x, tr_y), (br_x, br_y), (bl_x, bl_y)]
        draw.polygon(corners, outline="red", width=2)

    # Draw words in blue
    for word in receipt_words:
        # Word coordinates are normalized 0-1 relative to receipt, OCR space
        tl_x = word.top_left["x"] * receipt_width
        tl_y = (1 - word.top_left["y"]) * receipt_height  # Flip Y: OCR -> PIL
        tr_x = word.top_right["x"] * receipt_width
        tr_y = (1 - word.top_right["y"]) * receipt_height
        br_x = word.bottom_right["x"] * receipt_width
        br_y = (1 - word.bottom_right["y"]) * receipt_height
        bl_x = word.bottom_left["x"] * receipt_width
        bl_y = (1 - word.bottom_left["y"]) * receipt_height

        corners = [(tl_x, tl_y), (tr_x, tr_y), (br_x, br_y), (bl_x, bl_y)]
        draw.polygon(corners, outline="blue", width=1)

    # Add title
    draw.text((10, 10), f"Receipt {receipt_id}", fill="black", font=font)
    draw.text((10, 30), f"{len(receipt_lines)} lines, {len(receipt_words)} words", fill="black", font=font)

    img.save(output_path)
    print(f"💾 Saved visualization to: {output_path}")


def get_image_from_s3(bucket: str, key: str) -> PIL_Image.Image:
    """Download image from S3."""
    s3 = boto3.client("s3")
    response = s3.get_object(Bucket=bucket, Key=key)
    return PIL_Image.open(response["Body"])


def main():
    parser = argparse.ArgumentParser(
        description="Re-cluster receipt and visualize results"
    )
    parser.add_argument("--image-id", required=True, help="Image ID")
    parser.add_argument("--original-receipt-id", type=int, required=True, help="Original receipt ID")
    parser.add_argument("--output-dir", type=Path, required=True, help="Output directory")
    parser.add_argument("--dry-run", action="store_true", help="Dry run mode (don't save to DynamoDB)")

    args = parser.parse_args()

    # Setup environment
    env = setup_environment()
    client = DynamoClient(env["dynamo_table_name"])

    # Load data
    print(f"📥 Loading data for image {args.image_id}, receipt {args.original_receipt_id}...")
    image_entity = client.get_image(args.image_id)
    original_receipt = client.get_receipt(args.image_id, args.original_receipt_id)
    image_lines = client.list_lines_from_image(args.image_id)
    original_receipt_lines = client.list_receipt_lines_from_receipt(
        args.image_id, args.original_receipt_id
    )
    original_receipt_words = client.list_receipt_words_from_receipt(
        args.image_id, args.original_receipt_id
    )
    # Letters are optional - try to load if method exists
    original_receipt_letters = []
    try:
        if hasattr(client, 'list_receipt_letters_from_receipt'):
            original_receipt_letters = client.list_receipt_letters_from_receipt(
                args.image_id, args.original_receipt_id
            )
    except AttributeError:
        pass

    print(f"   Image: {image_entity.width} x {image_entity.height}")
    print(f"   Original receipt: {len(original_receipt_lines)} lines, {len(original_receipt_words)} words")

    # Re-cluster
    print(f"\n🔄 Re-clustering lines...")
    cluster_dict = recluster_receipt_lines(
        image_lines,
        image_entity.width,
        image_entity.height,
    )

    print(f"   Found {len(cluster_dict)} clusters")
    for cluster_id, cluster_lines in sorted(cluster_dict.items()):
        print(f"      Cluster {cluster_id}: {len(cluster_lines)} lines")

    # Download original image
    print(f"\n📥 Downloading original image...")
    original_image = None
    if image_entity.raw_s3_bucket and image_entity.raw_s3_key:
        try:
            original_image = get_image_from_s3(image_entity.raw_s3_bucket, image_entity.raw_s3_key)
            print(f"   ✅ Loaded from {image_entity.raw_s3_key}")
        except Exception as e:
            print(f"   ⚠️  Could not load from raw S3: {e}")
            if image_entity.cdn_s3_bucket and image_entity.cdn_s3_key:
                try:
                    original_image = get_image_from_s3(image_entity.cdn_s3_bucket, image_entity.cdn_s3_key)
                    print(f"   ✅ Loaded from CDN: {image_entity.cdn_s3_key}")
                except Exception as e2:
                    print(f"   ⚠️  Could not load from CDN: {e2}")

    if not original_image:
        print("   ⚠️  Could not load image - visualization will be limited")
        original_image = PIL_Image.new("RGB", (image_entity.width, image_entity.height), "white")

    # Create output directory
    args.output_dir.mkdir(parents=True, exist_ok=True)

    # Process each cluster
    print(f"\n📊 Processing clusters...")
    for cluster_id, cluster_lines in sorted(cluster_dict.items()):
        print(f"\n   Cluster {cluster_id}:")

        # Get line_ids in this cluster
        cluster_line_ids = {line.line_id for line in cluster_lines}

        # Filter receipt OCR data for this cluster
        cluster_receipt_lines = [
            rl for rl in original_receipt_lines if rl.line_id in cluster_line_ids
        ]
        cluster_receipt_words = [
            rw for rw in original_receipt_words if rw.line_id in cluster_line_ids
        ]
        cluster_receipt_letters = [
            rl for rl in original_receipt_letters if rl.line_id in cluster_line_ids
        ]

        # Check for missing receipt lines
        receipt_line_ids = {rl.line_id for rl in cluster_receipt_lines}
        missing_line_ids = cluster_line_ids - receipt_line_ids

        print(f"      Image lines: {len(cluster_lines)}")
        print(f"      Receipt OCR: {len(cluster_receipt_lines)} lines, {len(cluster_receipt_words)} words")
        if missing_line_ids:
            print(f"      ⚠️  Missing receipt lines: {len(missing_line_ids)} lines (will use image-level data)")

        if not cluster_receipt_words:
            print(f"      ⚠️  No receipt words found - skipping")
            continue

        # Calculate new receipt bounds
        new_receipt_bounds = calculate_receipt_bounds(
            cluster_receipt_words,
            original_receipt,
            image_entity.width,
            image_entity.height,
        )

        # Create receipt image
        receipt_image = create_receipt_image(
            original_image,
            new_receipt_bounds,
            image_entity.width,
            image_entity.height,
        )

        # Transform receipt OCR coordinates to new receipt space
        print(f"      Transforming coordinates...")
        transformed_lines = []
        transformed_words = []

        # First, transform receipt-level lines (more accurate OCR)
        for original_line in cluster_receipt_lines:
            corners = transform_receipt_ocr_to_new_receipt_space(
                original_line,
                original_receipt,
                new_receipt_bounds,
                image_entity.width,
                image_entity.height,
            )

            # Calculate bounding box
            x_coords = [corners[c]["x"] for c in ["top_left", "top_right", "bottom_left", "bottom_right"]]
            y_coords = [corners[c]["y"] for c in ["top_left", "top_right", "bottom_left", "bottom_right"]]
            min_x = min(x_coords)
            max_x = max(x_coords)
            min_y = min(y_coords)
            max_y = max(y_coords)

            receipt_width_abs = (new_receipt_bounds["top_right"]["x"] - new_receipt_bounds["top_left"]["x"]) * image_entity.width
            receipt_height_abs = (new_receipt_bounds["top_left"]["y"] - new_receipt_bounds["bottom_left"]["y"]) * image_entity.height

            transformed_line = ReceiptLine(
                receipt_id=cluster_id,
                image_id=args.image_id,
                line_id=original_line.line_id,
                text=original_line.text,
                bounding_box={
                    "x": min_x * receipt_width_abs,
                    "y": min_y * receipt_height_abs,
                    "width": (max_x - min_x) * receipt_width_abs,
                    "height": (max_y - min_y) * receipt_height_abs,
                },
                top_left=corners["top_left"],
                top_right=corners["top_right"],
                bottom_left=corners["bottom_left"],
                bottom_right=corners["bottom_right"],
                angle_degrees=original_line.angle_degrees,
                angle_radians=original_line.angle_radians,
                confidence=original_line.confidence,
            )
            transformed_lines.append(transformed_line)

        for original_word in cluster_receipt_words:
            corners = transform_receipt_ocr_to_new_receipt_space(
                original_word,
                original_receipt,
                new_receipt_bounds,
                image_entity.width,
                image_entity.height,
            )

            # Calculate bounding box
            x_coords = [corners[c]["x"] for c in ["top_left", "top_right", "bottom_left", "bottom_right"]]
            y_coords = [corners[c]["y"] for c in ["top_left", "top_right", "bottom_left", "bottom_right"]]
            min_x = min(x_coords)
            max_x = max(x_coords)
            min_y = min(y_coords)
            max_y = max(y_coords)

            receipt_width_abs = (new_receipt_bounds["top_right"]["x"] - new_receipt_bounds["top_left"]["x"]) * image_entity.width
            receipt_height_abs = (new_receipt_bounds["top_left"]["y"] - new_receipt_bounds["bottom_left"]["y"]) * image_entity.height

            transformed_word = ReceiptWord(
                receipt_id=cluster_id,
                image_id=args.image_id,
                line_id=original_word.line_id,
                word_id=original_word.word_id,
                text=original_word.text,
                bounding_box={
                    "x": min_x * receipt_width_abs,
                    "y": min_y * receipt_height_abs,
                    "width": (max_x - min_x) * receipt_width_abs,
                    "height": (max_y - min_y) * receipt_height_abs,
                },
                top_left=corners["top_left"],
                top_right=corners["top_right"],
                bottom_left=corners["bottom_left"],
                bottom_right=corners["bottom_right"],
                angle_degrees=original_word.angle_degrees,
                angle_radians=original_word.angle_radians,
                confidence=original_word.confidence,
                extracted_data=original_word.extracted_data,
                is_noise=original_word.is_noise,
            )
            transformed_words.append(transformed_word)

        # For lines that don't have receipt OCR data, use image-level lines
        # Transform them from image space to new receipt space
        if missing_line_ids:
            print(f"      Adding {len(missing_line_ids)} image-level lines...")
            for image_line in cluster_lines:
                if image_line.line_id in missing_line_ids:
                    # Transform from image space to new receipt space
                    # Image lines are already in image space (normalized 0-1, OCR space)
                    # Convert to absolute image coordinates
                    img_tl_x = image_line.top_left["x"] * image_entity.width
                    img_tl_y = (1 - image_line.top_left["y"]) * image_entity.height  # Flip Y: OCR -> PIL
                    img_tr_x = image_line.top_right["x"] * image_entity.width
                    img_tr_y = (1 - image_line.top_right["y"]) * image_entity.height
                    img_bl_x = image_line.bottom_left["x"] * image_entity.width
                    img_bl_y = (1 - image_line.bottom_left["y"]) * image_entity.height
                    img_br_x = image_line.bottom_right["x"] * image_entity.width
                    img_br_y = (1 - image_line.bottom_right["y"]) * image_entity.height

                    # Transform to new receipt space
                    def img_to_receipt_coord(img_x, img_y):
                        ocr_y = image_entity.height - img_y
                        receipt_x = (img_x - new_receipt_min_x_abs) / new_receipt_width_abs if new_receipt_width_abs > 0 else 0.0
                        receipt_y = (ocr_y - new_receipt_min_y_abs) / new_receipt_height_abs if new_receipt_height_abs > 0 else 0.0
                        return receipt_x, receipt_y

                    new_receipt_min_x_abs = new_receipt_bounds["top_left"]["x"] * image_entity.width
                    new_receipt_max_x_abs = new_receipt_bounds["top_right"]["x"] * image_entity.width
                    new_receipt_min_y_abs = new_receipt_bounds["bottom_left"]["y"] * image_entity.height
                    new_receipt_max_y_abs = new_receipt_bounds["top_left"]["y"] * image_entity.height
                    new_receipt_width_abs = new_receipt_max_x_abs - new_receipt_min_x_abs
                    new_receipt_height_abs = new_receipt_max_y_abs - new_receipt_min_y_abs

                    line_tl_x, line_tl_y = img_to_receipt_coord(img_tl_x, img_tl_y)
                    line_tr_x, line_tr_y = img_to_receipt_coord(img_tr_x, img_tr_y)
                    line_bl_x, line_bl_y = img_to_receipt_coord(img_bl_x, img_bl_y)
                    line_br_x, line_br_y = img_to_receipt_coord(img_br_x, img_br_y)

                    # Calculate bounding box
                    x_coords = [line_tl_x, line_tr_x, line_bl_x, line_br_x]
                    y_coords = [line_tl_y, line_tr_y, line_bl_y, line_br_y]
                    min_x = min(x_coords)
                    max_x = max(x_coords)
                    min_y = min(y_coords)
                    max_y = max(y_coords)

                    image_line_receipt = ReceiptLine(
                        receipt_id=cluster_id,
                        image_id=args.image_id,
                        line_id=image_line.line_id,
                        text=image_line.text,
                        bounding_box={
                            "x": min_x * new_receipt_width_abs,
                            "y": min_y * new_receipt_height_abs,
                            "width": (max_x - min_x) * new_receipt_width_abs,
                            "height": (max_y - min_y) * new_receipt_height_abs,
                        },
                        top_left={"x": line_tl_x, "y": line_tl_y},
                        top_right={"x": line_tr_x, "y": line_tr_y},
                        bottom_left={"x": line_bl_x, "y": line_bl_y},
                        bottom_right={"x": line_br_x, "y": line_br_y},
                        angle_degrees=image_line.angle_degrees,
                        angle_radians=image_line.angle_radians,
                        confidence=image_line.confidence,
                    )
                    transformed_lines.append(image_line_receipt)

        # Visualize
        output_path = args.output_dir / f"receipt_{cluster_id}_visualization.png"
        visualize_receipt_with_boxes(
            receipt_image,
            transformed_lines,
            transformed_words,
            output_path,
            cluster_id,
        )

        print(f"      ✅ Created visualization: {output_path} ({len(transformed_lines)} total lines)")

    print(f"\n✅ Complete! Visualizations saved to: {args.output_dir}")


if __name__ == "__main__":
    main()

