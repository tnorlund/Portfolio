#!/usr/bin/env python3
"""
Visualize the difference between old and new left/right edge detection.

This shows:
- Hull convex (blue)
- Hull centroid (blue dot)
- Old approach: left/right extremes along tilt angle (magenta)
- Old approach: left/right boundary lines (magenta dashed)
- New approach: min/max hull X (cyan vertical lines)
"""

import argparse
import logging
import math
import os
import sys
from pathlib import Path
from typing import List, Tuple

from PIL import Image as PIL_Image, ImageDraw, ImageFont

script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)
sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))
sys.path.insert(0, os.path.join(parent_dir, "receipt_upload"))

from receipt_dynamo.constants import ImageType
from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo.data.dynamo_client import DynamoClient

from receipt_upload.cluster import dbscan_lines
from receipt_upload.geometry import (
    compute_hull_centroid,
    convex_hull,
    find_hull_extremes_along_angle,
    refine_hull_extremes_with_hull_edge_alignment,
    create_boundary_line_from_points,
    compute_final_receipt_tilt,
)
from receipt_upload.utils import download_image_from_s3

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)


def draw_line_equation(draw, line_eq, image_width, image_height, color, width=2):
    """Draw a line defined by slope/intercept across the image."""
    if line_eq.get("isVertical"):
        x = line_eq["x"]
        draw.line([(x, 0), (x, image_height)], fill=color, width=width)
    elif line_eq.get("isInverted"):
        # x = slope * y + intercept
        slope = line_eq["slope"]
        intercept = line_eq["intercept"]
        y1, y2 = 0, image_height
        x1 = slope * y1 + intercept
        x2 = slope * y2 + intercept
        draw.line([(x1, y1), (x2, y2)], fill=color, width=width)
    else:
        # y = slope * x + intercept
        slope = line_eq["slope"]
        intercept = line_eq["intercept"]
        x1, x2 = 0, image_width
        y1 = slope * x1 + intercept
        y2 = slope * x2 + intercept
        draw.line([(x1, y1), (x2, y2)], fill=color, width=width)


def visualize_edge_detection(client, image_id, output_dir):
    """Visualize edge detection approaches for one image."""
    try:
        image = client.get_image(image_id)
        img_type = image.image_type.value if hasattr(image.image_type, 'value') else image.image_type
        if img_type != "PHOTO":
            return None

        lines = client.list_lines_from_image(image_id)
        if not lines:
            return None

        all_words = []
        for line in lines:
            words = client.list_words_from_line(image_id, line.line_id)
            all_words.extend(words)

        # Download image
        logger.info(f"Downloading {image_id}...")
        image_path = download_image_from_s3(
            s3_bucket=image.raw_s3_bucket,
            s3_key=image.raw_s3_key,
            image_id=image_id,
        )
        pil_image = PIL_Image.open(image_path)
        if pil_image.mode != 'RGB':
            pil_image = pil_image.convert('RGB')
        draw = ImageDraw.Draw(pil_image)

        # Cluster lines
        avg_diag = sum([line.calculate_diagonal_length() for line in lines]) / len(lines)
        clusters = dbscan_lines(lines, eps=avg_diag * 2, min_samples=10)
        clusters = {k: v for k, v in clusters.items() if k != -1}

        if not clusters:
            return None

        for _cluster_id, cluster_lines in clusters.items():
            if len(cluster_lines) < 3:
                continue

            line_ids = [line.line_id for line in cluster_lines]
            cluster_words = [w for w in all_words if w.line_id in line_ids]
            if len(cluster_words) < 4:
                continue

            # Get word corners in pixel coords
            all_corners = []
            for word in cluster_words:
                corners = word.calculate_corners(image.width, image.height, flip_y=True)
                all_corners.extend([(float(x), float(y)) for x, y in corners])

            hull = convex_hull(all_corners)
            if len(hull) < 4:
                continue

            # === Draw Hull (blue) ===
            for i in range(len(hull)):
                p1 = hull[i]
                p2 = hull[(i + 1) % len(hull)]
                draw.line([p1, p2], fill="blue", width=2)

            # === Compute centroid ===
            centroid = compute_hull_centroid(hull)
            r = 10
            draw.ellipse([centroid[0]-r, centroid[1]-r, centroid[0]+r, centroid[1]+r],
                        fill="blue", outline="white")

            # === Compute average angle ===
            angles = [line.angle_degrees for line in cluster_lines if line.angle_degrees != 0]
            avg_angle = sum(angles) / len(angles) if angles else 0.0

            # Compute final tilt
            final_angle = compute_final_receipt_tilt(cluster_lines, hull, centroid, avg_angle)

            # === OLD APPROACH: Find extremes along tilt angle ===
            extremes = find_hull_extremes_along_angle(hull, centroid, final_angle)
            left_extreme = extremes["leftPoint"]
            right_extreme = extremes["rightPoint"]

            # Draw left/right extreme points (magenta)
            for pt, label in [(left_extreme, "L"), (right_extreme, "R")]:
                draw.ellipse([pt[0]-8, pt[1]-8, pt[0]+8, pt[1]+8], fill="magenta", outline="white")
                try:
                    font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 18)
                except (OSError, IOError):
                    font = ImageFont.load_default()
                draw.text((pt[0]+12, pt[1]-8), f"{label}(old)", fill="magenta", font=font)

            # Refine with neighbors
            refined = refine_hull_extremes_with_hull_edge_alignment(
                hull, left_extreme, right_extreme, final_angle
            )

            # Draw left boundary line (magenta dashed - we'll draw solid for visibility)
            left_line = create_boundary_line_from_points(
                refined["leftSegment"]["extreme"],
                refined["leftSegment"]["optimizedNeighbor"],
            )
            right_line = create_boundary_line_from_points(
                refined["rightSegment"]["extreme"],
                refined["rightSegment"]["optimizedNeighbor"],
            )

            draw_line_equation(draw, left_line, image.width, image.height, "magenta", width=3)
            draw_line_equation(draw, right_line, image.width, image.height, "magenta", width=3)

            # === NEW APPROACH: Simple min/max X ===
            hull_xs = [p[0] for p in hull]
            min_hull_x = min(hull_xs)
            max_hull_x = max(hull_xs)

            # Draw vertical lines at min/max X (cyan)
            draw.line([(min_hull_x, 0), (min_hull_x, image.height)], fill="cyan", width=3)
            draw.line([(max_hull_x, 0), (max_hull_x, image.height)], fill="cyan", width=3)

            # Labels
            try:
                font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 24)
            except (OSError, IOError):
                font = ImageFont.load_default()
            draw.text((min_hull_x + 5, 50), "NEW L", fill="cyan", font=font)
            draw.text((max_hull_x - 80, 50), "NEW R", fill="cyan", font=font)

        # Add legend
        try:
            font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 28)
        except (OSError, IOError):
            font = ImageFont.load_default()

        legend_y = 20
        draw.rectangle([10, legend_y, 400, legend_y + 120], fill="white", outline="black")
        draw.text((20, legend_y + 5), "BLUE = Hull", fill="blue", font=font)
        draw.text((20, legend_y + 35), "MAGENTA = Old L/R edges (angled)", fill="magenta", font=font)
        draw.text((20, legend_y + 65), "CYAN = New L/R edges (vertical)", fill="cyan", font=font)

        # Save
        output_path = output_dir / f"{image_id}_edge_comparison.jpg"
        pil_image.save(output_path, quality=90)
        logger.info(f"Saved: {output_path}")

        if image_path.exists():
            image_path.unlink()

        return str(output_path)

    except Exception as e:
        logger.exception("Error processing image: %s", e)
        return None


def main():
    parser = argparse.ArgumentParser(description="Visualize edge detection approaches")
    parser.add_argument("--stack", choices=["dev", "prod"], required=True)
    parser.add_argument("--output-dir", type=str, default="viz_output")
    parser.add_argument("--image-id", type=str, default=None)
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    env = load_env(env=args.stack)
    table_name = env.get("dynamodb_table_name")
    client = DynamoClient(table_name)

    if args.image_id:
        visualize_edge_detection(client, args.image_id, output_dir)
    else:
        # Process the two problematic images
        problem_images = [
            "2c453d65-edae-4aeb-819a-612b27d99894",
            "8362b52a-60a1-4534-857f-028dd531976e",
        ]
        for img_id in problem_images:
            visualize_edge_detection(client, img_id, output_dir)

    print(f"\nOutput saved to: {output_dir.absolute()}")
    print("Legend:")
    print("  BLUE = Convex hull of word corners")
    print("  MAGENTA = Old approach: angled L/R boundary lines")
    print("  CYAN = New approach: vertical lines at min/max hull X")


if __name__ == "__main__":
    main()
