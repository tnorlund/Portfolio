#!/usr/bin/env python3
"""
Visualize the final clustered receipts for an image.

Shows the original image with bounding boxes for each final cluster in different colors.
"""

import argparse
import sys
from pathlib import Path

repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "receipt_upload"))

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import Line
from scripts.split_receipt import recluster_receipt_lines, setup_environment

try:
    from PIL import Image as PIL_Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("⚠️  PIL not available, install pillow")
    sys.exit(1)

import boto3


def get_image_from_s3(bucket: str, key: str) -> PIL_Image.Image:
    """Download image from S3."""
    s3 = boto3.client("s3")
    response = s3.get_object(Bucket=bucket, Key=key)
    return PIL_Image.open(response["Body"])


def get_cluster_color(cluster_id: int, total_clusters: int) -> str:
    """Get a bright, high-contrast color for each cluster."""
    colors = [
        "#FF0000",  # Bright Red
        "#00FF00",  # Bright Green
        "#0000FF",  # Bright Blue
        "#FF00FF",  # Magenta
        "#00FFFF",  # Cyan
        "#FFFF00",  # Yellow
        "#FF8000",  # Orange
        "#8000FF",  # Purple
    ]
    return colors[cluster_id % len(colors)]


def get_line_corners_image_coords(line: Line, img_width: int, img_height: int):
    """Get line corners in image coordinate space (y=0 at top)."""
    # Line corners are in OCR space (normalized 0-1, y=0 at bottom)
    # Convert to image space (pixels, y=0 at top)
    tl = (
        line.top_left["x"] * img_width,
        (1.0 - line.top_left["y"]) * img_height,  # Flip Y
    )
    tr = (
        line.top_right["x"] * img_width,
        (1.0 - line.top_right["y"]) * img_height,  # Flip Y
    )
    br = (
        line.bottom_right["x"] * img_width,
        (1.0 - line.bottom_right["y"]) * img_height,  # Flip Y
    )
    bl = (
        line.bottom_left["x"] * img_width,
        (1.0 - line.bottom_left["y"]) * img_height,  # Flip Y
    )
    return [tl, tr, br, bl]


def visualize_final_clusters(
    image_id: str,
    output_path: Path,
    raw_bucket: str,
):
    """Create visualization of final clustered receipts."""
    client = DynamoClient("ReceiptsTable-dc5be22")

    # Load data
    image_entity = client.get_image(image_id)
    image_lines = client.list_lines_from_image(image_id)

    # Get final clusters
    cluster_dict = recluster_receipt_lines(
        image_lines,
        image_entity.width,
        image_entity.height,
    )

    # Download original image
    original_image = None
    s3_key = image_entity.raw_s3_key if image_entity.raw_s3_key else f"raw/{image_id}.png"
    try:
        original_image = get_image_from_s3(raw_bucket, s3_key)
        print(f"✅ Loaded image from raw S3: {s3_key}")
    except Exception as e:
        print(f"⚠️  Could not load image from raw S3 key '{s3_key}': {e}")
        # Try CDN image as fallback
        if image_entity.cdn_s3_bucket and image_entity.cdn_s3_key:
            try:
                print(f"   Trying CDN image: {image_entity.cdn_s3_key}")
                original_image = get_image_from_s3(image_entity.cdn_s3_bucket, image_entity.cdn_s3_key)
                print(f"✅ Loaded image from CDN")
            except Exception as e2:
                print(f"⚠️  Could not load image from CDN: {e2}")
        if original_image is None:
            print(f"   Creating blank image for visualization...")
            original_image = PIL_Image.new("RGB", (image_entity.width, image_entity.height), "white")

    img_width, img_height = original_image.size

    # Create visualization
    img = original_image.copy()
    draw = ImageDraw.Draw(img)

    # Draw each cluster in a different color
    print(f"\n📊 Final clusters for {image_id}:")
    for cluster_id, cluster_lines in sorted(cluster_dict.items()):
        color = get_cluster_color(cluster_id, len(cluster_dict))
        print(f"   Cluster {cluster_id}: {len(cluster_lines)} lines (color: {color})")

        for line in cluster_lines:
            corners = get_line_corners_image_coords(line, img_width, img_height)
            draw.polygon(corners, outline=color, width=3)

    # Add legend
    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 24)
        small_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 18)
    except:
        font = ImageFont.load_default()
        small_font = ImageFont.load_default()

    legend_y = 10
    draw.rectangle([10, legend_y, 300, legend_y + 30 * len(cluster_dict)], fill=(255, 255, 255, 200), outline="black", width=2)
    for cluster_id, cluster_lines in sorted(cluster_dict.items()):
        color = get_cluster_color(cluster_id, len(cluster_dict))
        draw.text((20, legend_y + 5), f"Cluster {cluster_id}: {len(cluster_lines)} lines", fill=color, font=small_font)
        legend_y += 30

    # Save
    img.save(output_path)
    print(f"\n💾 Saved visualization to: {output_path}")
    print(f"   Total clusters: {len(cluster_dict)}")
    print(f"   Total lines: {sum(len(lines) for lines in cluster_dict.values())}")


def main():
    parser = argparse.ArgumentParser(description="Visualize final clustered receipts")
    parser.add_argument("--image-id", required=True, help="Image ID to visualize")
    parser.add_argument("--output", required=True, type=Path, help="Output image path")
    parser.add_argument("--raw-bucket", help="Raw S3 bucket name (optional, will try to load from environment)")

    args = parser.parse_args()

    # Setup environment
    env = setup_environment()
    raw_bucket = args.raw_bucket or env.get("raw_bucket", "")
    if not raw_bucket:
        print("⚠️  Raw bucket not specified and not found in environment")
        sys.exit(1)

    visualize_final_clusters(args.image_id, args.output, raw_bucket)


if __name__ == "__main__":
    main()

