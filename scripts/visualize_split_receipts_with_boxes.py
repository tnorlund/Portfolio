#!/usr/bin/env python3
"""
Visualize split receipts with bounding boxes showing the clustered lines.

This script:
1. Loads split receipts from DynamoDB
2. Downloads the original image from CDN
3. Draws bounding boxes for each receipt's lines in different colors
4. Shows how the receipts were split

Usage:
    python scripts/visualize_split_receipts_with_boxes.py \
        --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
        --original-receipt-id 1 \
        --new-receipt-ids 4 5 \
        --output ./split_visualization_boxes.png
"""

import argparse
import io
import os
import sys
from pathlib import Path
from typing import Dict, List, Optional

# Add repo root to path
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("⚠️  PIL/Pillow not available - visualization will be limited")

import boto3
from receipt_dynamo import DynamoClient


def create_visualization_with_boxes(
    original_image: Image.Image,
    original_receipt: any,
    original_lines: List[Dict],
    split_receipts: List[Dict],
    split_lines: List[List[Dict]],
    output_path: Path,
) -> None:
    """Create a visualization showing original image with bounding boxes for split receipts."""
    if not PIL_AVAILABLE:
        print("⚠️  Cannot create visualization - PIL not available")
        return

    # Create a copy of the original image
    img = original_image.copy()
    draw = ImageDraw.Draw(img)

    # Color palette for different receipts
    colors = [
        (255, 0, 0),      # Red
        (0, 255, 0),      # Green
        (0, 0, 255),      # Blue
        (255, 165, 0),    # Orange
        (128, 0, 128),    # Purple
        (255, 192, 203),  # Pink
        (0, 255, 255),    # Cyan
        (255, 255, 0),    # Yellow
    ]

    image_width = img.width
    image_height = img.height

    # Draw original lines in light gray (for reference)
    # Original receipt bounds for coordinate transformation
    # Receipt bounds are in OCR space (y=0 at bottom), normalized (0-1) relative to image
    orig_tl = original_receipt.top_left if hasattr(original_receipt, 'top_left') else original_receipt['top_left']
    orig_tr = original_receipt.top_right if hasattr(original_receipt, 'top_right') else original_receipt['top_right']
    orig_bl = original_receipt.bottom_left if hasattr(original_receipt, 'bottom_left') else original_receipt['bottom_left']
    orig_br = original_receipt.bottom_right if hasattr(original_receipt, 'bottom_right') else original_receipt['bottom_right']

    # Convert receipt bounds from OCR space (normalized 0-1) to image coordinates
    # OCR space: y=0 at bottom, so top_left.y > bottom_left.y (larger Y = top)
    # Image space: y=0 at top, so we flip: image_y = image_height - ocr_y * image_height
    orig_min_x = orig_tl['x'] * image_width
    orig_max_x = orig_tr['x'] * image_width
    # In OCR space: receipt_tl.y is top (larger), receipt_bl.y is bottom (smaller)
    # In image space: top is smaller Y, bottom is larger Y
    orig_top_y_img = image_height - orig_tl['y'] * image_height  # Top in OCR (large) -> top in image (small)
    orig_bottom_y_img = image_height - orig_bl['y'] * image_height  # Bottom in OCR (small) -> bottom in image (large)
    orig_width_abs = orig_max_x - orig_min_x
    orig_height_abs = orig_bottom_y_img - orig_top_y_img

    for line in original_lines:
        tl = line['top_left']
        tr = line['top_right']
        br = line['bottom_right']
        bl = line['bottom_left']

        # ReceiptLine coordinates are normalized (0-1) relative to receipt bounds
        # In OCR space: y=0 at bottom of receipt, y=1 at top of receipt
        # Convert to image coordinates:
        # - X: receipt_min_x + line_x * receipt_width
        # - Y: receipt_top_y_img + (1 - line_y) * receipt_height (flip Y: 0=bottom->large Y, 1=top->small Y)
        line_tl_x = orig_min_x + tl['x'] * orig_width_abs
        line_tl_y = orig_top_y_img + (1 - tl['y']) * orig_height_abs  # Flip: receipt 0=bottom, image bottom=large Y
        line_tr_x = orig_min_x + tr['x'] * orig_width_abs
        line_tr_y = orig_top_y_img + (1 - tr['y']) * orig_height_abs
        line_br_x = orig_min_x + br['x'] * orig_width_abs
        line_br_y = orig_top_y_img + (1 - br['y']) * orig_height_abs
        line_bl_x = orig_min_x + bl['x'] * orig_width_abs
        line_bl_y = orig_top_y_img + (1 - bl['y']) * orig_height_abs

        points = [
            (int(line_tl_x), int(line_tl_y)),
            (int(line_tr_x), int(line_tr_y)),
            (int(line_br_x), int(line_br_y)),
            (int(line_bl_x), int(line_bl_y)),
        ]
        draw.polygon(points, outline=(200, 200, 200), width=1)

    # Draw split receipt lines in different colors
    for i, (receipt, lines) in enumerate(zip(split_receipts, split_lines)):
        color = colors[i % len(colors)]
        receipt_id = receipt['receipt_id'] if isinstance(receipt, dict) else receipt.receipt_id

        # Get receipt bounds to transform line coordinates
        if isinstance(receipt, dict):
            receipt_tl = receipt['top_left']
            receipt_tr = receipt['top_right']
            receipt_bl = receipt['bottom_left']
            receipt_br = receipt['bottom_right']
            receipt_width = receipt['width']
            receipt_height = receipt['height']
        else:
            receipt_tl = receipt.top_left
            receipt_tr = receipt.top_right
            receipt_bl = receipt.bottom_left
            receipt_br = receipt.bottom_right
            receipt_width = receipt.width
            receipt_height = receipt.height

        # Calculate receipt bounds in image coordinates
        # Receipt bounds are in OCR space (y=0 at bottom), normalized (0-1) relative to image
        # In OCR space: receipt_tl.y is top (larger), receipt_bl.y is bottom (smaller)
        # Convert to image coordinates: image_y = image_height - ocr_y * image_height
        receipt_min_x = receipt_tl['x'] * image_width
        receipt_max_x = receipt_tr['x'] * image_width
        # In OCR space: receipt_tl.y is top (larger), receipt_bl.y is bottom (smaller)
        # In image space: top is smaller Y, bottom is larger Y
        receipt_top_y_img = image_height - receipt_tl['y'] * image_height  # Top in OCR (large) -> top in image (small)
        receipt_bottom_y_img = image_height - receipt_bl['y'] * image_height  # Bottom in OCR (small) -> bottom in image (large)

        receipt_width_abs = receipt_max_x - receipt_min_x
        receipt_height_abs = receipt_bottom_y_img - receipt_top_y_img

        for line in lines:
            if isinstance(line, dict):
                tl = line['top_left']
                tr = line['top_right']
                br = line['bottom_right']
                bl = line['bottom_left']
            else:
                tl = line.top_left
                tr = line.top_right
                br = line.bottom_right
                bl = line.bottom_left

            # Convert from receipt-relative (0-1) to absolute image coordinates
            # ReceiptLine coordinates are normalized (0-1) relative to receipt bounds
            # In OCR space: y=0 at bottom of receipt, y=1 at top of receipt
            # Convert to image coordinates:
            # - X: receipt_min_x + line_x * receipt_width
            # - Y: receipt_top_y_img + (1 - line_y) * receipt_height (flip Y: 0=bottom->large Y, 1=top->small Y)
            line_tl_x = receipt_min_x + tl['x'] * receipt_width_abs
            line_tl_y = receipt_top_y_img + (1 - tl['y']) * receipt_height_abs  # Flip: receipt 0=bottom, image bottom=large Y
            line_tr_x = receipt_min_x + tr['x'] * receipt_width_abs
            line_tr_y = receipt_top_y_img + (1 - tr['y']) * receipt_height_abs
            line_br_x = receipt_min_x + br['x'] * receipt_width_abs
            line_br_y = receipt_top_y_img + (1 - br['y']) * receipt_height_abs
            line_bl_x = receipt_min_x + bl['x'] * receipt_width_abs
            line_bl_y = receipt_top_y_img + (1 - bl['y']) * receipt_height_abs

            points = [
                (int(line_tl_x), int(line_tl_y)),
                (int(line_tr_x), int(line_tr_y)),
                (int(line_br_x), int(line_br_y)),
                (int(line_bl_x), int(line_bl_y)),
            ]

            # Draw bounding box with cluster color
            draw.polygon(points, outline=color, width=3)

    # Add legend
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 24)
        small_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 16)
    except:
        font = ImageFont.load_default()
        small_font = ImageFont.load_default()

    legend_y = 20
    draw.text((20, legend_y), "Receipt Split Visualization", fill=(0, 0, 0), font=font)
    legend_y += 35

    # Draw original receipt box
    draw.rectangle([20, legend_y, 40, legend_y + 15], outline=(200, 200, 200), width=2)
    draw.text((45, legend_y), "Original Receipt (gray)", fill=(0, 0, 0), font=small_font)
    legend_y += 25

    # Draw split receipt boxes
    for i, receipt in enumerate(split_receipts):
        color = colors[i % len(colors)]
        receipt_id = receipt['receipt_id'] if isinstance(receipt, dict) else receipt.receipt_id
        num_lines = len(split_lines[i]) if i < len(split_lines) else 0

        draw.rectangle([20, legend_y, 40, legend_y + 15], outline=color, width=3)
        draw.text((45, legend_y), f"Receipt {receipt_id}: {num_lines} lines", fill=(0, 0, 0), font=small_font)
        legend_y += 25

    # Save image
    img.save(output_path)
    print(f"💾 Saved visualization to: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Visualize split receipts with bounding boxes"
    )
    parser.add_argument(
        "--image-id",
        required=True,
        help="Image ID",
    )
    parser.add_argument(
        "--original-receipt-id",
        type=int,
        required=True,
        help="Original receipt ID",
    )
    parser.add_argument(
        "--new-receipt-ids",
        type=int,
        nargs="+",
        required=True,
        help="New receipt IDs",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("./split_visualization_boxes.png"),
        help="Output path for visualization",
    )

    args = parser.parse_args()

    if not PIL_AVAILABLE:
        print("❌ PIL/Pillow is required for visualization")
        sys.exit(1)

    print(f"📊 Loading from DynamoDB...")
    table_name = os.environ.get('DYNAMODB_TABLE_NAME', 'ReceiptsTable-dc5be22')
    client = DynamoClient(table_name)

    # Load original receipt and lines
    original_receipt = client.get_receipt(args.image_id, args.original_receipt_id)
    original_lines = client.list_receipt_lines_from_receipt(args.image_id, args.original_receipt_id)
    print(f"   Original receipt: {args.original_receipt_id} ({len(original_lines)} lines)")

    # Load split receipts and their lines
    split_receipts = []
    split_lines = []
    for receipt_id in args.new_receipt_ids:
        receipt = client.get_receipt(args.image_id, receipt_id)
        lines = client.list_receipt_lines_from_receipt(args.image_id, receipt_id)
        split_receipts.append(receipt)
        split_lines.append(lines)
        print(f"   Split receipt: {receipt_id} ({len(lines)} lines)")

    # Download original full image from raw S3 bucket
    s3 = boto3.client('s3')
    original_image = None

    # Get Image entity to access raw_s3_bucket and raw_s3_key
    image_details = client.get_image_details(args.image_id)
    if not image_details.images:
        print(f"❌ Could not find Image entity for {args.image_id}")
        sys.exit(1)
    image_entity = image_details.images[0]

    if image_entity.raw_s3_bucket and image_entity.raw_s3_key:
        print(f"\n📥 Downloading original full image from {image_entity.raw_s3_bucket}/{image_entity.raw_s3_key}...")
        try:
            response = s3.get_object(Bucket=image_entity.raw_s3_bucket, Key=image_entity.raw_s3_key)
            original_image = Image.open(io.BytesIO(response['Body'].read()))
            print(f"   ✅ Loaded image: {original_image.width}x{original_image.height}")
        except Exception as e:
            print(f"   ❌ Could not download original image: {e}")
            sys.exit(1)
    else:
        print("❌ Image entity has no raw S3 bucket/key")
        sys.exit(1)

    # Convert ReceiptLine entities to dicts for easier handling
    original_lines_dict = [dict(line) for line in original_lines]
    split_lines_dict = [[dict(line) for line in lines] for lines in split_lines]

    # Create visualization
    print(f"\n🎨 Creating visualization with bounding boxes...")
    create_visualization_with_boxes(
        original_image,
        original_receipt,
        original_lines_dict,
        split_receipts,
        split_lines_dict,
        args.output,
    )

    print(f"\n✅ Visualization complete!")


if __name__ == "__main__":
    main()

