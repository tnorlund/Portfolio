#!/usr/bin/env python3
"""
Visualize perspective transform corner detection differences.

This script downloads PHOTO images and draws corners from:
- Simplified approach (green) - new method using top/bottom line corners
- Stored corners (red) - what was computed during original processing

Usage:
    python scripts/visualize_perspective_transforms.py --stack dev --output-dir viz_output
    python scripts/visualize_perspective_transforms.py --stack dev --image-id <uuid> --output-dir viz_output
"""

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import List, Optional, Tuple

from PIL import Image as PIL_Image, ImageDraw, ImageFont

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)

sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))
sys.path.insert(0, os.path.join(parent_dir, "receipt_upload"))

from receipt_dynamo.constants import ImageType
from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities import Line, Word

from receipt_upload.cluster import dbscan_lines
from receipt_upload.utils import download_image_from_s3
from receipt_upload.geometry.utils import compute_rotated_bounding_box_corners
from receipt_upload.geometry.hull_operations import convex_hull

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


def compute_simplified_corners(
    cluster_lines: List[Line],
    cluster_words: List[Word],
    image_width: int,
    image_height: int,
) -> Optional[List[Tuple[float, float]]]:
    """Compute receipt corners using hull-based perspective approach.

    Uses weighted hull edge directions at extremes for true perspective
    (left/right edges can converge) rather than forcing parallel edges.
    """
    if len(cluster_lines) < 2 or len(cluster_words) < 4:
        return None

    # Find top and bottom lines by Y position
    # OCR normalized coords: y=0 at bottom, y=1 at top
    # Use line centroid (average of all 4 corners) for more robust sorting
    def get_line_centroid_y(line):
        return (
            line.top_left["y"] + line.top_right["y"] +
            line.bottom_left["y"] + line.bottom_right["y"]
        ) / 4

    sorted_lines = sorted(
        cluster_lines,
        key=get_line_centroid_y,
        reverse=True,
    )
    top_line = sorted_lines[0]      # Highest Y = top of image
    bottom_line = sorted_lines[-1]  # Lowest Y = bottom of image

    # Get corners from top and bottom lines
    top_line_corners = top_line.calculate_corners(
        width=image_width, height=image_height, flip_y=True
    )
    bottom_line_corners = bottom_line.calculate_corners(
        width=image_width, height=image_height, flip_y=True
    )

    # Compute convex hull from all word corners
    all_corners = []
    for word in cluster_words:
        word_corners = word.calculate_corners(
            width=image_width, height=image_height, flip_y=True
        )
        all_corners.extend(word_corners)

    if len(all_corners) < 3:
        return None

    hull = convex_hull(all_corners)

    # Use the new hull-based perspective algorithm
    return compute_rotated_bounding_box_corners(
        hull=hull,
        top_line_corners=top_line_corners,
        bottom_line_corners=bottom_line_corners,
    )


def draw_quadrilateral(
    draw: ImageDraw.ImageDraw,
    corners: List[Tuple[float, float]],
    color: str,
    width: int = 3,
    label: str | None = None,
):
    """Draw a quadrilateral on the image."""
    # Draw the four edges
    for i in range(4):
        p1 = corners[i]
        p2 = corners[(i + 1) % 4]
        draw.line([p1, p2], fill=color, width=width)

    # Draw corner markers
    corner_names = ["TL", "TR", "BR", "BL"]
    for i, corner in enumerate(corners):
        x, y = corner
        # Draw a circle at each corner
        radius = 8
        draw.ellipse(
            [x - radius, y - radius, x + radius, y + radius],
            fill=color,
            outline=color,
        )
        # Draw corner label
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 20)
        except (OSError, IOError):
            font = ImageFont.load_default()

        label_text = f"{corner_names[i]}"
        draw.text((x + 12, y - 10), label_text, fill=color, font=font)

    # Draw main label if provided
    if label:
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 24)
        except (OSError, IOError):
            font = ImageFont.load_default()

        # Position label near top-left corner
        tl = corners[0]
        draw.text((tl[0], tl[1] - 35), label, fill=color, font=font)


def visualize_image(
    client: DynamoClient,
    image_id: str,
    output_dir: Path,
) -> Optional[str]:
    """Visualize corner detection for a single image."""
    try:
        # Get image details
        image = client.get_image(image_id)
        img_type = image.image_type.value if hasattr(image.image_type, 'value') else image.image_type
        if img_type != "PHOTO":
            logger.debug(f"Skipping {image_id}: not a PHOTO")
            return None

        # Get lines and words
        lines = client.list_lines_from_image(image_id)
        if not lines:
            logger.debug(f"Skipping {image_id}: no lines")
            return None

        all_words = []
        for line in lines:
            words = client.list_words_from_line(image_id, line.line_id)
            all_words.extend(words)

        if not all_words:
            logger.debug(f"Skipping {image_id}: no words")
            return None

        # Get stored receipts
        receipts = client.get_receipts_from_image(image_id)
        if not receipts:
            logger.debug(f"Skipping {image_id}: no receipts")
            return None

        # Download the image
        logger.info(f"Downloading image {image_id}...")
        image_path = download_image_from_s3(
            s3_bucket=image.raw_s3_bucket,
            s3_key=image.raw_s3_key,
            image_id=image_id,
        )
        pil_image = PIL_Image.open(image_path)

        # Convert to RGB if needed (for drawing)
        if pil_image.mode != 'RGB':
            pil_image = pil_image.convert('RGB')

        draw = ImageDraw.Draw(pil_image)

        # Cluster the lines
        avg_diagonal_length = sum(
            [line.calculate_diagonal_length() for line in lines]
        ) / len(lines)
        clusters = dbscan_lines(lines, eps=avg_diagonal_length * 2, min_samples=10)
        clusters = {k: v for k, v in clusters.items() if k != -1}

        if not clusters:
            logger.debug(f"Skipping {image_id}: no valid clusters")
            return None

        # Process each cluster
        for cluster_id, cluster_lines in clusters.items():
            if len(cluster_lines) < 3:
                continue

            line_ids = [line.line_id for line in cluster_lines]
            cluster_words = [w for w in all_words if w.line_id in line_ids]

            if len(cluster_words) < 4:
                continue

            # Compute simplified corners
            simplified = compute_simplified_corners(
                cluster_lines, cluster_words, image.width, image.height
            )

            # Get stored receipt corners
            matching_receipts = [r for r in receipts if r.receipt_id == cluster_id]
            if not matching_receipts:
                matching_receipts = [receipts[0]] if receipts else []

            if not matching_receipts:
                continue

            receipt = matching_receipts[0]

            # Convert stored corners to pixels
            stored_corners = [
                (receipt.top_left["x"] * image.width, receipt.top_left["y"] * image.height),
                (receipt.top_right["x"] * image.width, receipt.top_right["y"] * image.height),
                (receipt.bottom_right["x"] * image.width, receipt.bottom_right["y"] * image.height),
                (receipt.bottom_left["x"] * image.width, receipt.bottom_left["y"] * image.height),
            ]

            # Check if stored corners are valid
            all_normalized = [
                receipt.top_left["x"], receipt.top_left["y"],
                receipt.top_right["x"], receipt.top_right["y"],
                receipt.bottom_left["x"], receipt.bottom_left["y"],
                receipt.bottom_right["x"], receipt.bottom_right["y"],
            ]
            stored_valid = all(0 <= v <= 1.0 for v in all_normalized)

            # Draw stored corners (red) - only if valid, otherwise dashed/dimmed
            if stored_valid:
                draw_quadrilateral(draw, stored_corners, color="red", width=4, label="STORED (old)")
            else:
                # Draw in orange to indicate invalid
                draw_quadrilateral(draw, stored_corners, color="orange", width=2, label="STORED (INVALID)")

            # Draw simplified corners (green)
            if simplified:
                draw_quadrilateral(draw, simplified, color="lime", width=4, label="SIMPLIFIED (new)")

        # Save the visualization
        output_path = output_dir / f"{image_id}_comparison.jpg"
        pil_image.save(output_path, quality=90)
        logger.info(f"Saved: {output_path}")

        # Clean up downloaded image
        if image_path.exists():
            image_path.unlink()

        return str(output_path)

    except Exception as e:
        logger.error(f"Error processing {image_id}: {e}")
        import traceback
        traceback.print_exc()
        return None


def main():
    parser = argparse.ArgumentParser(
        description="Visualize perspective transform corner detection"
    )
    parser.add_argument(
        "--stack",
        choices=["dev", "prod"],
        required=True,
        help="Pulumi stack to use",
    )
    parser.add_argument(
        "--output-dir",
        type=str,
        default="viz_output",
        help="Output directory for visualizations",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit number of images to process",
    )
    parser.add_argument(
        "--image-id",
        type=str,
        default=None,
        help="Process a specific image ID",
    )
    args = parser.parse_args()

    # Create output directory
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Get table name
    logger.info(f"Getting {args.stack.upper()} configuration from Pulumi...")
    env = load_env(env=args.stack)
    table_name = env.get("dynamodb_table_name")
    if not table_name:
        raise ValueError(f"Could not find dynamodb_table_name in Pulumi {args.stack} stack")
    logger.info(f"{args.stack.upper()} table: {table_name}")

    client = DynamoClient(table_name)
    output_files = []

    if args.image_id:
        # Process single image
        result = visualize_image(client, args.image_id, output_dir)
        if result:
            output_files.append(result)
    else:
        # List all PHOTO images
        logger.info("Listing PHOTO images...")
        photos, _ = client.list_images_by_type(ImageType.PHOTO, limit=args.limit)
        logger.info(f"Found {len(photos)} PHOTO images")

        for i, photo in enumerate(photos):
            logger.info(f"Processing {i+1}/{len(photos)}: {photo.image_id}")
            result = visualize_image(client, photo.image_id, output_dir)
            if result:
                output_files.append(result)

    # Summary
    print("\n" + "=" * 60)
    print("VISUALIZATION COMPLETE")
    print("=" * 60)
    print(f"Output directory: {output_dir.absolute()}")
    print(f"Files created: {len(output_files)}")
    for f in output_files:
        print(f"  - {f}")
    print("\nLegend:")
    print("  GREEN (lime) = Simplified approach (new)")
    print("  RED = Stored corners (old approach)")
    print("  ORANGE = Stored corners that are INVALID (outside image bounds)")


if __name__ == "__main__":
    main()
