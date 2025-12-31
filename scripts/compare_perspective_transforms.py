#!/usr/bin/env python3
"""
Compare simplified vs complex perspective transform corner detection.

This script compares two approaches for detecting receipt corners in PHOTO images:

1. SIMPLIFIED (current photo.py): Uses top/bottom line corners directly
   - Sort lines by Y position
   - Use top line's TL/TR for receipt top edge
   - Use bottom line's BL/BR for receipt bottom edge
   - Constrain by hull X bounds

2. COMPLEX (original): Uses multiple geometry operations
   - compute_hull_centroid
   - find_hull_extents_relative_to_centroid
   - compute_final_receipt_tilt
   - find_hull_extremes_along_angle
   - refine_hull_extremes_with_hull_edge_alignment
   - find_line_edges_at_secondary_extremes
   - create_horizontal_boundary_line_from_points / theil_sen
   - compute_receipt_box_from_boundaries

Usage:
    python scripts/compare_perspective_transforms.py --stack dev [--limit 10]
    python scripts/compare_perspective_transforms.py --stack prod --image-id <uuid>
"""

import argparse
import json
import logging
import math
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# Add parent directories to path for imports
script_dir = os.path.dirname(os.path.abspath(__file__))
parent_dir = os.path.dirname(script_dir)

sys.path.insert(0, parent_dir)
sys.path.insert(0, os.path.join(parent_dir, "receipt_dynamo"))
sys.path.insert(0, os.path.join(parent_dir, "receipt_upload"))

from receipt_dynamo.constants import ImageType
from receipt_dynamo.data._pulumi import load_env
from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities import Line, Receipt, Word

from receipt_upload.cluster import dbscan_lines
from receipt_upload.geometry import (
    compute_final_receipt_tilt,
    compute_hull_centroid,
    compute_receipt_box_from_boundaries,
    convex_hull,
    create_boundary_line_from_points,
    create_boundary_line_from_theil_sen,
    find_hull_extremes_along_angle,
    find_line_edges_at_secondary_extremes,
    refine_hull_extremes_with_hull_edge_alignment,
    theil_sen,
)

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class CornerComparison:
    """Result of comparing two corner detection approaches."""

    image_id: str
    receipt_id: int
    simplified_corners: List[Tuple[float, float]]
    complex_corners: List[Tuple[float, float]]
    stored_corners: List[Tuple[float, float]]
    corner_distances: List[float]  # Distance between simplified and complex
    max_distance: float
    avg_distance: float


def get_table_name(stack: str) -> str:
    """Get the DynamoDB table name from Pulumi stack."""
    logger.info(f"Getting {stack.upper()} configuration from Pulumi...")
    env = load_env(env=stack)
    table_name = env.get("dynamodb_table_name")
    if not table_name:
        raise ValueError(f"Could not find dynamodb_table_name in Pulumi {stack} stack")
    logger.info(f"{stack.upper()} table: {table_name}")
    return table_name


def compute_simplified_corners(
    cluster_lines: List[Line],
    cluster_words: List[Word],
    image_width: int,
    image_height: int,
) -> Optional[List[Tuple[float, float]]]:
    """
    Compute receipt corners using the SIMPLIFIED approach.

    This mirrors the logic in photo.py:
    - Get corners from all words to build convex hull
    - Sort lines by Y position
    - Use top line's TL/TR for receipt top edge
    - Use bottom line's BL/BR for receipt bottom edge
    - Constrain by hull X bounds
    """
    if len(cluster_lines) < 2 or len(cluster_words) < 4:
        return None

    # Get all word corners for hull
    all_word_corners = []
    for word in cluster_words:
        corners = word.calculate_corners(
            width=image_width,
            height=image_height,
            flip_y=True,
        )
        all_word_corners.extend([(int(x), int(y)) for x, y in corners])

    if len(all_word_corners) < 4:
        return None

    # Compute hull
    hull = convex_hull(all_word_corners)
    if len(hull) < 4:
        return None

    # Find top and bottom lines by Y position
    sorted_lines = sorted(
        cluster_lines,
        key=lambda line: line.top_left["y"],
        reverse=True,
    )
    top_line = sorted_lines[0]
    bottom_line = sorted_lines[-1]

    # Get corners from top and bottom lines
    top_line_corners = top_line.calculate_corners(
        width=image_width, height=image_height, flip_y=True
    )
    bottom_line_corners = bottom_line.calculate_corners(
        width=image_width, height=image_height, flip_y=True
    )

    # Use hull to constrain left/right edges
    hull_xs = [p[0] for p in hull]
    min_hull_x = min(hull_xs)
    max_hull_x = max(hull_xs)

    # Receipt corners
    top_left = (
        max(min_hull_x, top_line_corners[0][0]),
        top_line_corners[0][1],
    )
    top_right = (
        min(max_hull_x, top_line_corners[1][0]),
        top_line_corners[1][1],
    )
    bottom_left = (
        max(min_hull_x, bottom_line_corners[2][0]),
        bottom_line_corners[2][1],
    )
    bottom_right = (
        min(max_hull_x, bottom_line_corners[3][0]),
        bottom_line_corners[3][1],
    )

    return [top_left, top_right, bottom_right, bottom_left]


def compute_complex_corners(
    cluster_lines: List[Line],
    cluster_words: List[Word],
    image_width: int,
    image_height: int,
) -> Optional[List[Tuple[float, float]]]:
    """
    Compute receipt corners using the COMPLEX approach.

    This uses the full pipeline:
    1. Compute convex hull of all word corners
    2. Compute hull centroid
    3. Get average line angle
    4. Compute final receipt tilt
    5. Find hull extremes along angle
    6. Refine with hull edge alignment
    7. Find line edges at secondary extremes
    8. Create boundary lines (Theil-Sen for top/bottom, points for left/right)
    9. Intersect boundaries to get corners
    """
    if len(cluster_lines) < 2 or len(cluster_words) < 4:
        return None

    # Get all word corners for hull
    all_word_corners = []
    for word in cluster_words:
        corners = word.calculate_corners(
            width=image_width,
            height=image_height,
            flip_y=True,
        )
        all_word_corners.extend([(float(x), float(y)) for x, y in corners])

    if len(all_word_corners) < 4:
        return None

    # Compute hull
    hull = convex_hull(all_word_corners)
    if len(hull) < 4:
        return None

    # 1. Compute hull centroid
    centroid = compute_hull_centroid(hull)

    # 2. Get average line angle
    angles = [line.angle_degrees for line in cluster_lines if line.angle_degrees != 0]
    avg_angle = sum(angles) / len(angles) if angles else 0.0

    # 3. Compute final receipt tilt
    final_angle = compute_final_receipt_tilt(cluster_lines, hull, centroid, avg_angle)

    # 4. Find hull extremes along angle
    extremes = find_hull_extremes_along_angle(hull, centroid, final_angle)
    left_extreme = extremes["leftPoint"]
    right_extreme = extremes["rightPoint"]

    # 5. Refine with hull edge alignment
    refined = refine_hull_extremes_with_hull_edge_alignment(
        hull, left_extreme, right_extreme, final_angle
    )

    # 6. Find line edges at secondary extremes (for top/bottom)
    edges = find_line_edges_at_secondary_extremes(
        cluster_lines, hull, centroid, final_angle
    )

    if len(edges["topEdge"]) < 2 or len(edges["bottomEdge"]) < 2:
        return None

    # 7. Create boundary lines
    try:
        top_boundary = create_boundary_line_from_theil_sen(theil_sen(edges["topEdge"]))
        bottom_boundary = create_boundary_line_from_theil_sen(
            theil_sen(edges["bottomEdge"])
        )
    except (ValueError, ZeroDivisionError):
        return None

    left_boundary = create_boundary_line_from_points(
        refined["leftSegment"]["extreme"],
        refined["leftSegment"]["optimizedNeighbor"],
    )
    right_boundary = create_boundary_line_from_points(
        refined["rightSegment"]["extreme"],
        refined["rightSegment"]["optimizedNeighbor"],
    )

    # 8. Compute receipt box from boundaries
    corners = compute_receipt_box_from_boundaries(
        top_boundary, bottom_boundary, left_boundary, right_boundary, centroid
    )

    return corners


def corners_from_receipt(
    receipt: Receipt, image_width: int, image_height: int
) -> List[Tuple[float, float]]:
    """Convert stored normalized corners to pixel coordinates."""
    # Note: stored corners are already normalized (0-1)
    # Convert to pixel coordinates for comparison
    tl = (receipt.top_left["x"] * image_width, receipt.top_left["y"] * image_height)
    tr = (receipt.top_right["x"] * image_width, receipt.top_right["y"] * image_height)
    br = (
        receipt.bottom_right["x"] * image_width,
        receipt.bottom_right["y"] * image_height,
    )
    bl = (
        receipt.bottom_left["x"] * image_width,
        receipt.bottom_left["y"] * image_height,
    )
    return [tl, tr, br, bl]


def compute_corner_distances(
    corners1: List[Tuple[float, float]], corners2: List[Tuple[float, float]]
) -> List[float]:
    """Compute distances between corresponding corners."""
    distances = []
    for c1, c2 in zip(corners1, corners2):
        dist = math.dist(c1, c2)
        distances.append(dist)
    return distances


def compare_image(
    client: DynamoClient,
    image_id: str,
) -> List[CornerComparison]:
    """Compare corner detection approaches for a single image."""
    results = []

    try:
        # Get image details
        image = client.get_image(image_id)
        # Handle both string and enum types for image_type
        img_type = image.image_type.value if hasattr(image.image_type, 'value') else image.image_type
        if img_type != "PHOTO":
            logger.debug(f"Skipping {image_id}: not a PHOTO (is {img_type})")
            return []

        # Get lines and words
        lines = client.list_lines_from_image(image_id)
        if not lines:
            logger.debug(f"Skipping {image_id}: no lines")
            return []

        # Get all words
        all_words = []
        for line in lines:
            words = client.list_words_from_line(image_id, line.line_id)
            all_words.extend(words)

        if not all_words:
            logger.debug(f"Skipping {image_id}: no words")
            return []

        # Get stored receipts
        receipts = client.get_receipts_from_image(image_id)
        if not receipts:
            logger.debug(f"Skipping {image_id}: no receipts")
            return []

        # Cluster the lines using DBSCAN (same as original processing)
        avg_diagonal_length = sum(
            [line.calculate_diagonal_length() for line in lines]
        ) / len(lines)
        clusters = dbscan_lines(lines, eps=avg_diagonal_length * 2, min_samples=10)
        # Drop noise clusters
        clusters = {k: v for k, v in clusters.items() if k != -1}

        if not clusters:
            logger.debug(f"Skipping {image_id}: no valid clusters")
            return []

        # For each cluster, compute corners and compare to stored receipt
        for cluster_id, cluster_lines in clusters.items():
            if len(cluster_lines) < 3:
                continue

            # Get words for this cluster
            line_ids = [line.line_id for line in cluster_lines]
            cluster_words = [w for w in all_words if w.line_id in line_ids]

            if len(cluster_words) < 4:
                continue

            # Compute simplified corners for this cluster
            simplified = compute_simplified_corners(
                cluster_lines, cluster_words, image.width, image.height
            )

            # Compute complex corners for this cluster
            complex_corners = compute_complex_corners(
                cluster_lines, cluster_words, image.width, image.height
            )

            if not simplified or not complex_corners:
                continue

            # Find matching stored receipt by cluster_id (receipt_id)
            matching_receipts = [r for r in receipts if r.receipt_id == cluster_id]
            if not matching_receipts:
                # Try to find by proximity (sometimes receipt_ids differ)
                logger.debug(f"No exact match for cluster {cluster_id}, using first receipt")
                if not receipts:
                    continue
                matching_receipts = [receipts[0]]

            receipt = matching_receipts[0]
            stored = corners_from_receipt(receipt, image.width, image.height)

            distances = compute_corner_distances(simplified, complex_corners)
            max_dist = max(distances)
            avg_dist = sum(distances) / len(distances)

            results.append(
                CornerComparison(
                    image_id=image_id,
                    receipt_id=receipt.receipt_id,
                    simplified_corners=simplified,
                    complex_corners=complex_corners,
                    stored_corners=stored,
                    corner_distances=distances,
                    max_distance=max_dist,
                    avg_distance=avg_dist,
                )
            )

    except Exception as e:
        logger.error(f"Error processing {image_id}: {e}")
        import traceback
        traceback.print_exc()

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Compare perspective transform corner detection approaches"
    )
    parser.add_argument(
        "--stack",
        choices=["dev", "prod"],
        required=True,
        help="Pulumi stack to use",
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
    parser.add_argument(
        "--output",
        type=str,
        default=None,
        help="Output JSON file for results",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )
    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.DEBUG)

    # Get table name
    table_name = get_table_name(args.stack)
    client = DynamoClient(table_name)

    all_results = []

    if args.image_id:
        # Process single image
        logger.info(f"Processing image: {args.image_id}")
        results = compare_image(client, args.image_id)
        all_results.extend(results)
    else:
        # List all PHOTO images
        logger.info("Listing PHOTO images...")
        photos, _ = client.list_images_by_type(ImageType.PHOTO, limit=args.limit)
        logger.info(f"Found {len(photos)} PHOTO images")

        for i, photo in enumerate(photos):
            logger.info(f"Processing {i+1}/{len(photos)}: {photo.image_id}")
            results = compare_image(client, photo.image_id)
            all_results.extend(results)

    # Print summary
    print("\n" + "=" * 80)
    print("COMPARISON SUMMARY")
    print("=" * 80)
    print(f"Total comparisons: {len(all_results)}")

    if all_results:
        max_dists = [r.max_distance for r in all_results]
        avg_dists = [r.avg_distance for r in all_results]

        print(f"\nSimplified vs Complex approach:")
        print(f"  NOTE: Large differences expected because complex approach")
        print(f"        operates on ALL lines (not clustered) - for illustration only")
        print(f"\n  Max corner distance (across all images):")
        print(f"    Min: {min(max_dists):.2f} px")
        print(f"    Max: {max(max_dists):.2f} px")
        print(f"    Avg: {sum(max_dists)/len(max_dists):.2f} px")

        # Also compare simplified to stored corners
        print(f"\nSimplified vs STORED corners (what was actually computed):")
        simplified_vs_stored = []
        for r in all_results:
            dists = compute_corner_distances(r.simplified_corners, r.stored_corners)
            simplified_vs_stored.append({
                'image_id': r.image_id,
                'max': max(dists),
                'avg': sum(dists) / len(dists),
                'dists': dists,
                'simplified': r.simplified_corners,
                'stored': r.stored_corners,
            })

        max_dists_stored = [x['max'] for x in simplified_vs_stored]
        avg_dists_stored = [x['avg'] for x in simplified_vs_stored]

        print(f"  Max corner distance:")
        print(f"    Min: {min(max_dists_stored):.2f} px")
        print(f"    Max: {max(max_dists_stored):.2f} px")
        print(f"    Avg: {sum(max_dists_stored)/len(max_dists_stored):.2f} px")

        print(f"\n  Avg corner distance per image:")
        print(f"    Min: {min(avg_dists_stored):.2f} px")
        print(f"    Max: {max(avg_dists_stored):.2f} px")
        print(f"    Avg: {sum(avg_dists_stored)/len(avg_dists_stored):.2f} px")

        # Top differences simplified vs stored
        sorted_stored = sorted(simplified_vs_stored, key=lambda x: x['max'], reverse=True)
        print(f"\n  Top 5 differences (simplified vs stored):")
        for r in sorted_stored[:5]:
            print(f"    {r['image_id']}: max={r['max']:.2f}px, avg={r['avg']:.2f}px")

        # Detailed comparison
        if sorted_stored:
            top = sorted_stored[0]
            print(f"\n  Detailed comparison for {top['image_id']}:")
            corner_names = ["TL", "TR", "BR", "BL"]
            for i, (s, st, d) in enumerate(
                zip(top['simplified'], top['stored'], top['dists'])
            ):
                print(f"    {corner_names[i]}: simplified=({s[0]:.1f}, {s[1]:.1f}), stored=({st[0]:.1f}, {st[1]:.1f}), diff={d:.2f}px")

    # Save results to JSON if requested
    if args.output:
        output_data = []
        for r in all_results:
            output_data.append(
                {
                    "image_id": r.image_id,
                    "receipt_id": r.receipt_id,
                    "simplified_corners": r.simplified_corners,
                    "complex_corners": r.complex_corners,
                    "stored_corners": r.stored_corners,
                    "corner_distances": r.corner_distances,
                    "max_distance": r.max_distance,
                    "avg_distance": r.avg_distance,
                }
            )
        with open(args.output, "w") as f:
            json.dump(output_data, f, indent=2)
        logger.info(f"Results saved to {args.output}")


if __name__ == "__main__":
    main()
