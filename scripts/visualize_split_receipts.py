#!/usr/bin/env python3
"""
Visualize split receipts from DynamoDB or local files.

This script:
1. Loads split receipts from DynamoDB or local JSON files
2. Downloads the receipt images from CDN
3. Creates a visualization showing the original and split receipts side-by-side
4. Shows how the receipts were split

Usage:
    # From DynamoDB
    python scripts/visualize_split_receipts.py \
        --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
        --original-receipt-id 1 \
        --new-receipt-ids 4 5 \
        --output ./split_visualization.png

    # From local files
    python scripts/visualize_split_receipts.py \
        --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
        --local-dir ./local_receipt_splits \
        --output ./split_visualization.png
"""

import argparse
import json
import sys
from pathlib import Path
from typing import List, Optional

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


def download_image_from_s3(bucket: str, key: str) -> Optional[Image.Image]:
    """Download image from S3."""
    if not PIL_AVAILABLE:
        return None

    try:
        s3 = boto3.client('s3')
        response = s3.get_object(Bucket=bucket, Key=key)
        image_data = response['Body'].read()
        return Image.open(io.BytesIO(image_data))
    except Exception as e:
        print(f"⚠️  Could not download {bucket}/{key}: {e}")
        return None


def create_split_visualization(
    original_image: Optional[Image.Image],
    split_images: List[Image.Image],
    receipt_ids: List[int],
    output_path: Path,
    image_width: int = 2000,
) -> None:
    """Create a visualization showing original and split receipts."""
    if not PIL_AVAILABLE:
        print("⚠️  Cannot create visualization - PIL not available")
        return

    # Calculate layout
    num_splits = len(split_images)
    if original_image:
        num_panels = num_splits + 1
    else:
        num_panels = num_splits

    # Scale images to fit
    panel_width = image_width // num_panels
    max_height = 3500  # Max height for each panel

    # Scale original if present
    if original_image:
        orig_ratio = original_image.width / original_image.height
        orig_height = min(panel_width / orig_ratio, max_height)
        orig_width = int(orig_height * orig_ratio)
        original_scaled = original_image.resize((orig_width, int(orig_height)), Image.Resampling.LANCZOS)
    else:
        original_scaled = None
        orig_width = 0
        orig_height = 0

    # Scale split images
    split_scaled = []
    max_split_height = 0
    for img in split_images:
        ratio = img.width / img.height
        height = min(panel_width / ratio, max_height)
        width = int(height * ratio)
        split_scaled.append(img.resize((width, int(height)), Image.Resampling.LANCZOS))
        max_split_height = max(max_split_height, int(height))

    # Create canvas
    canvas_height = max(int(orig_height) if original_scaled else 0, max_split_height) + 100  # Extra space for labels
    canvas = Image.new('RGB', (image_width, canvas_height), color='white')
    draw = ImageDraw.Draw(canvas)

    # Draw images
    x_offset = 0

    # Draw original
    if original_scaled:
        y_offset = (canvas_height - original_scaled.height - 50) // 2
        canvas.paste(original_scaled, (x_offset, y_offset))

        # Label
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 24)
        except:
            font = ImageFont.load_default()
        draw.text((x_offset + orig_width // 2 - 50, canvas_height - 40),
                 "Original Receipt", fill=(0, 0, 0), font=font, anchor="mm")

        x_offset += orig_width + 20

    # Draw split receipts
    for i, (img, receipt_id) in enumerate(zip(split_scaled, receipt_ids)):
        y_offset = (canvas_height - img.height - 50) // 2
        canvas.paste(img, (x_offset, y_offset))

        # Label
        draw.text((x_offset + img.width // 2 - 50, canvas_height - 40),
                 f"Receipt {receipt_id}", fill=(0, 0, 0), font=font, anchor="mm")

        x_offset += img.width + 20

    # Add title
    draw.text((image_width // 2, 20), "Receipt Split Visualization",
             fill=(0, 0, 0), font=font, anchor="mm")

    # Save
    canvas.save(output_path)
    print(f"💾 Saved visualization to: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Visualize split receipts from DynamoDB or local files"
    )
    parser.add_argument(
        "--image-id",
        required=True,
        help="Image ID",
    )
    parser.add_argument(
        "--original-receipt-id",
        type=int,
        help="Original receipt ID (for DynamoDB mode)",
    )
    parser.add_argument(
        "--new-receipt-ids",
        type=int,
        nargs="+",
        help="New receipt IDs (for DynamoDB mode)",
    )
    parser.add_argument(
        "--local-dir",
        type=Path,
        help="Local directory with split receipt data (for local file mode)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("./split_visualization.png"),
        help="Output path for visualization",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=2000,
        help="Canvas width in pixels (default: 2000)",
    )

    args = parser.parse_args()

    if not PIL_AVAILABLE:
        print("❌ PIL/Pillow is required for visualization")
        sys.exit(1)

    # Determine mode
    if args.local_dir:
        # Local file mode
        print(f"📁 Loading from local directory: {args.local_dir}")
        image_dir = args.local_dir / args.image_id

        # Load original receipt
        original_file = image_dir / "original_receipt.json"
        if original_file.exists():
            with open(original_file) as f:
                original_receipt = json.load(f)
        else:
            original_receipt = None

        # Load split receipts
        split_receipts = []
        for receipt_dir in sorted(image_dir.glob("receipt_*")):
            receipt_file = receipt_dir / "receipt.json"
            if receipt_file.exists():
                with open(receipt_file) as f:
                    split_receipts.append(json.load(f))

        print(f"   Found {len(split_receipts)} split receipts")

        # Download images from CDN
        s3 = boto3.client('s3')
        original_image = None
        if original_receipt and original_receipt.get('cdn_s3_bucket') and original_receipt.get('cdn_s3_key'):
            print(f"   Downloading original image from {original_receipt['cdn_s3_bucket']}/{original_receipt['cdn_s3_key']}...")
            try:
                import io
                response = s3.get_object(Bucket=original_receipt['cdn_s3_bucket'], Key=original_receipt['cdn_s3_key'])
                original_image = Image.open(io.BytesIO(response['Body'].read()))
            except Exception as e:
                print(f"   ⚠️  Could not download original: {e}")

        split_images = []
        receipt_ids = []
        for receipt in split_receipts:
            if receipt.get('cdn_s3_bucket') and receipt.get('cdn_s3_key'):
                print(f"   Downloading receipt {receipt['receipt_id']} from {receipt['cdn_s3_bucket']}/{receipt['cdn_s3_key']}...")
                try:
                    import io
                    response = s3.get_object(Bucket=receipt['cdn_s3_bucket'], Key=receipt['cdn_s3_key'])
                    split_images.append(Image.open(io.BytesIO(response['Body'].read())))
                    receipt_ids.append(receipt['receipt_id'])
                except Exception as e:
                    print(f"   ⚠️  Could not download receipt {receipt['receipt_id']}: {e}")

    else:
        # DynamoDB mode
        if not args.original_receipt_id or not args.new_receipt_ids:
            print("❌ --original-receipt-id and --new-receipt-ids required for DynamoDB mode")
            sys.exit(1)

        print(f"📊 Loading from DynamoDB...")
        table_name = os.environ.get('DYNAMODB_TABLE_NAME', 'ReceiptsTable-dc5be22')
        client = DynamoClient(table_name)

        # Load original receipt
        original_receipt = client.get_receipt(args.image_id, args.original_receipt_id)
        print(f"   Original receipt: {args.original_receipt_id}")

        # Load split receipts
        split_receipts = []
        for receipt_id in args.new_receipt_ids:
            receipt = client.get_receipt(args.image_id, receipt_id)
            split_receipts.append(receipt)
            print(f"   Split receipt: {receipt_id}")

        # Download images from CDN
        s3 = boto3.client('s3')
        original_image = None
        if original_receipt.cdn_s3_bucket and original_receipt.cdn_s3_key:
            print(f"   Downloading original image from {original_receipt.cdn_s3_bucket}/{original_receipt.cdn_s3_key}...")
            try:
                import io
                response = s3.get_object(Bucket=original_receipt.cdn_s3_bucket, Key=original_receipt.cdn_s3_key)
                original_image = Image.open(io.BytesIO(response['Body'].read()))
            except Exception as e:
                print(f"   ⚠️  Could not download original: {e}")

        split_images = []
        receipt_ids = []
        for receipt in split_receipts:
            if receipt.cdn_s3_bucket and receipt.cdn_s3_key:
                print(f"   Downloading receipt {receipt.receipt_id} from {receipt.cdn_s3_bucket}/{receipt.cdn_s3_key}...")
                try:
                    import io
                    response = s3.get_object(Bucket=receipt.cdn_s3_bucket, Key=receipt.cdn_s3_key)
                    split_images.append(Image.open(io.BytesIO(response['Body'].read())))
                    receipt_ids.append(receipt.receipt_id)
                except Exception as e:
                    print(f"   ⚠️  Could not download receipt {receipt.receipt_id}: {e}")

    # Create visualization
    if not split_images:
        print("❌ No split receipt images to visualize")
        sys.exit(1)

    print(f"\n🎨 Creating visualization...")
    create_split_visualization(
        original_image,
        split_images,
        receipt_ids,
        args.output,
        image_width=args.width,
    )

    print(f"\n✅ Visualization complete!")


if __name__ == "__main__":
    import os
    import io
    main()

