#!/usr/bin/env python3
"""
Visualize the clustering phases for split receipt debugging.

Shows:
1. Original receipt lines
2. X-axis clustering results
3. Angle-based splitting results
4. Final clusters (after merging and post-processing)
5. Lines assigned during post-processing
"""

import math
import sys
from pathlib import Path
from typing import Dict, List, Set

# Add repo root to path
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "receipt_upload"))

from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import Line, Image, Receipt
from receipt_upload.cluster import (
    dbscan_lines_x_axis,
    split_clusters_by_angle_consistency,
    reassign_lines_by_x_proximity,
    reassign_lines_by_vertical_proximity,
    should_apply_smart_merging,
    merge_clusters_with_agent_logic,
    join_overlapping_clusters,
)
# No need for transforms - we'll use image-level Lines directly

try:
    from PIL import Image as PIL_Image, ImageDraw, ImageFont
except ImportError:
    print("⚠️  PIL not available, install pillow")
    sys.exit(1)

import boto3
import os


def get_image_from_s3(bucket: str, key: str) -> PIL_Image.Image:
    """Download image from S3."""
    s3 = boto3.client("s3")
    response = s3.get_object(Bucket=bucket, Key=key)
    return PIL_Image.open(response["Body"])


def get_cluster_color(cluster_id: int, total_clusters: int) -> str:
    """Get a bright, high-contrast color for each cluster."""
    # Use brighter, more saturated colors for better visibility
    colors = [
        "#FF0000",  # Bright Red
        "#00FF00",  # Bright Green
        "#0000FF",  # Bright Blue
        "#FF00FF",  # Magenta
        "#00FFFF",  # Cyan
        "#FFFF00",  # Yellow
        "#FF8000",  # Orange
        "#8000FF",  # Purple
        "#FF0080",  # Pink
        "#0080FF",  # Sky Blue
    ]
    return colors[cluster_id % len(colors)]


def get_line_corners_image_coords(line: Line, image_width: int, image_height: int) -> List[tuple]:
    """Get line corners in image coordinates (PIL space, y=0 at top)."""
    # Image-level Lines are already in normalized image coordinates (0-1, OCR space y=0 at bottom)
    # Convert to PIL space (y=0 at top) and absolute pixels
    corners = [
        (line.top_left["x"] * image_width, (1 - line.top_left["y"]) * image_height),
        (line.top_right["x"] * image_width, (1 - line.top_right["y"]) * image_height),
        (line.bottom_right["x"] * image_width, (1 - line.bottom_right["y"]) * image_height),
        (line.bottom_left["x"] * image_width, (1 - line.bottom_left["y"]) * image_height),
    ]
    return corners


def visualize_clustering_phases(
    image_id: str,
    receipt_id: int,
    output_path: Path,
    raw_bucket: str,
):
    """Create visualization of clustering phases."""
    client = DynamoClient("ReceiptsTable-dc5be22")

    # Load data
    image_entity = client.get_image(image_id)
    original_receipt = client.get_receipt(image_id, receipt_id)
    image_lines = client.list_lines_from_image(image_id)

    # Download original image
    original_image = get_image_from_s3(
        raw_bucket, image_entity.raw_s3_key
    )
    img_width, img_height = original_image.size

    # Create visualization with multiple panels
    panel_width = img_width
    panel_height = img_height
    num_panels = 8  # Steps 1-3, 3.5, 3.6, 3.7, 3.8, and final result
    total_width = panel_width
    total_height = panel_height * num_panels + 50  # Extra space for labels

    canvas = PIL_Image.new("RGB", (total_width, total_height), color="white")
    draw = ImageDraw.Draw(canvas)

    try:
        font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 24)
        small_font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 16)
    except:
        font = ImageFont.load_default()
        small_font = ImageFont.load_default()

    y_offset = 0

    # Panel 1: Original receipt lines
    print("📊 Panel 1: Original receipt lines...")
    panel1 = original_image.copy()
    draw1 = ImageDraw.Draw(panel1)

    original_receipt_lines = client.list_receipt_lines_from_receipt(image_id, receipt_id)
    receipt_line_ids = {rl.line_id for rl in original_receipt_lines}
    for image_line in image_lines:
        if image_line.line_id in receipt_line_ids:
            corners = get_line_corners_image_coords(image_line, img_width, img_height)
            draw1.polygon(corners, outline="gray", width=2)

    canvas.paste(panel1, (0, y_offset))
    draw.text((10, y_offset + 10), "1. Original Receipt Lines", fill="black", font=font)
    y_offset += panel_height

    # Panel 2: X-axis clustering
    print("📊 Panel 2: X-axis clustering...")
    panel2 = original_image.copy()
    draw2 = ImageDraw.Draw(panel2)

    cluster_dict_x = dbscan_lines_x_axis(image_lines)
    for cluster_id, cluster_lines in cluster_dict_x.items():
        color = get_cluster_color(cluster_id, len(cluster_dict_x))
        for line in cluster_lines:
            corners = get_line_corners_image_coords(line, img_width, img_height)
            draw2.polygon(corners, outline=color, width=2)

    canvas.paste(panel2, (0, y_offset))
    draw.text(
        (10, y_offset + 10),
        f"2. X-axis Clustering ({len(cluster_dict_x)} clusters)",
        fill="black",
        font=font,
    )
    y_offset += panel_height

    # Panel 3: After angle splitting
    print("📊 Panel 3: After angle splitting...")
    panel3 = original_image.copy()
    draw3 = ImageDraw.Draw(panel3)

    cluster_dict_angle = split_clusters_by_angle_consistency(
        cluster_dict_x, angle_tolerance=3.0, min_samples=2
    )

    # Track which lines are in clusters
    clustered_line_ids = set()
    for cluster_id, cluster_lines in cluster_dict_angle.items():
        color = get_cluster_color(cluster_id, len(cluster_dict_angle))
        for line in cluster_lines:
            clustered_line_ids.add(line.line_id)
            corners = get_line_corners_image_coords(line, img_width, img_height)
            draw3.polygon(corners, outline=color, width=2)

    # Highlight dropped lines
    dropped_line_ids = set(l.line_id for l in image_lines) - clustered_line_ids
    for line in image_lines:
        if line.line_id in dropped_line_ids:
            corners = get_line_corners_image_coords(line, img_width, img_height)
            draw3.polygon(corners, outline="red", width=3)

    canvas.paste(panel3, (0, y_offset))
    dropped_text = f" ({len(dropped_line_ids)} dropped)" if dropped_line_ids else ""
    draw.text(
        (10, y_offset + 10),
        f"3. After Angle Splitting ({len(cluster_dict_angle)} clusters){dropped_text}",
        fill="black",
        font=font,
    )
    if dropped_line_ids:
        draw.text(
            (10, y_offset + 40),
            f"Dropped line IDs: {sorted(list(dropped_line_ids))[:10]}...",
            fill="red",
            font=small_font,
        )

    y_offset += panel_height

    # Panel 3.5: After X-proximity reassignment
    print("📊 Panel 3.5: After X-proximity reassignment...")
    panel3_5 = original_image.copy()
    draw3_5 = ImageDraw.Draw(panel3_5)

    cluster_dict_x_reassigned = reassign_lines_by_x_proximity(
        cluster_dict_angle,
        x_proximity_threshold=0.1,
        y_proximity_threshold=0.15,
    )

    for cluster_id, cluster_lines in cluster_dict_x_reassigned.items():
        color = get_cluster_color(cluster_id, len(cluster_dict_x_reassigned))
        for line in cluster_lines:
            corners = get_line_corners_image_coords(line, img_width, img_height)
            draw3_5.polygon(corners, outline=color, width=2)

    canvas.paste(panel3_5, (0, y_offset))
    draw.text(
        (10, y_offset + 10),
        f"3.5. After X-Proximity Reassignment ({len(cluster_dict_x_reassigned)} clusters)",
        fill="black",
        font=font,
    )

    # Add cluster summary for panel 3.5
    summary_y = y_offset + 40
    for cluster_id, cluster_lines in sorted(cluster_dict_x_reassigned.items()):
        color = get_cluster_color(cluster_id, len(cluster_dict_x_reassigned))
        draw.text(
            (10, summary_y),
            f"  Cluster {cluster_id}: {len(cluster_lines)} lines",
            fill=color,
            font=small_font,
        )
        summary_y += 20
    y_offset += panel_height

    # Panel 3.6: After vertical proximity reassignment
    print("📊 Panel 3.6: After vertical proximity reassignment...")
    panel3_6 = original_image.copy()
    draw3_6 = ImageDraw.Draw(panel3_6)

    cluster_dict_vertical = reassign_lines_by_vertical_proximity(
        cluster_dict_x_reassigned,
        vertical_proximity_threshold=0.05,
        x_proximity_threshold=0.1,  # 10% of image width - must be horizontally close
    )

    for cluster_id, cluster_lines in cluster_dict_vertical.items():
        color = get_cluster_color(cluster_id, len(cluster_dict_vertical))
        for line in cluster_lines:
            corners = get_line_corners_image_coords(line, img_width, img_height)
            draw3_6.polygon(corners, outline=color, width=2)

    canvas.paste(panel3_6, (0, y_offset))
    draw.text(
        (10, y_offset + 10),
        f"3.6. After Vertical Proximity Reassignment ({len(cluster_dict_vertical)} clusters)",
        fill="black",
        font=font,
    )

    summary_y = y_offset + 40
    for cluster_id, cluster_lines in sorted(cluster_dict_vertical.items()):
        color = get_cluster_color(cluster_id, len(cluster_dict_vertical))
        draw.text(
            (10, summary_y),
            f"  Cluster {cluster_id}: {len(cluster_lines)} lines",
            fill=color,
            font=small_font,
        )
        summary_y += 20
    y_offset += panel_height

    # Panel 3.7: After smart merging
    print("📊 Panel 3.7: After smart merging...")
    panel3_7 = original_image.copy()
    draw3_7 = ImageDraw.Draw(panel3_7)

    should_merge = should_apply_smart_merging(cluster_dict_vertical, len(image_lines))
    print(f"   Should apply smart merging: {should_merge}")

    if should_merge:
        cluster_dict_merged = merge_clusters_with_agent_logic(
            cluster_dict_vertical, min_score=0.5, x_proximity_threshold=0.4
        )
    else:
        cluster_dict_merged = cluster_dict_vertical

    for cluster_id, cluster_lines in cluster_dict_merged.items():
        color = get_cluster_color(cluster_id, len(cluster_dict_merged))
        for line in cluster_lines:
            corners = get_line_corners_image_coords(line, img_width, img_height)
            draw3_7.polygon(corners, outline=color, width=2)

    canvas.paste(panel3_7, (0, y_offset))
    draw.text(
        (10, y_offset + 10),
        f"3.7. After Smart Merging ({len(cluster_dict_merged)} clusters)",
        fill="black",
        font=font,
    )

    summary_y = y_offset + 40
    for cluster_id, cluster_lines in sorted(cluster_dict_merged.items()):
        color = get_cluster_color(cluster_id, len(cluster_dict_merged))
        draw.text(
            (10, summary_y),
            f"  Cluster {cluster_id}: {len(cluster_lines)} lines",
            fill=color,
            font=small_font,
        )
        summary_y += 20
    y_offset += panel_height

    # Panel 3.8: After join overlapping
    print("📊 Panel 3.8: After join overlapping...")
    panel3_8 = original_image.copy()
    draw3_8 = ImageDraw.Draw(panel3_8)

    if len(cluster_dict_merged) > 2:
        cluster_dict_join = join_overlapping_clusters(
            cluster_dict_merged, img_width, img_height, iou_threshold=0.1
        )
    else:
        cluster_dict_join = cluster_dict_merged

    for cluster_id, cluster_lines in cluster_dict_join.items():
        color = get_cluster_color(cluster_id, len(cluster_dict_join))
        for line in cluster_lines:
            corners = get_line_corners_image_coords(line, img_width, img_height)
            draw3_8.polygon(corners, outline=color, width=2)

    canvas.paste(panel3_8, (0, y_offset))
    draw.text(
        (10, y_offset + 10),
        f"3.8. After Join Overlapping ({len(cluster_dict_join)} clusters)",
        fill="black",
        font=font,
    )

    summary_y = y_offset + 40
    for cluster_id, cluster_lines in sorted(cluster_dict_join.items()):
        color = get_cluster_color(cluster_id, len(cluster_dict_join))
        draw.text(
            (10, summary_y),
            f"  Cluster {cluster_id}: {len(cluster_lines)} lines",
            fill=color,
            font=small_font,
        )
        summary_y += 20
    y_offset += panel_height

    # Panel 4: Final clusters (after post-processing)
    print("📊 Panel 4: Final clusters (after post-processing)...")
    panel4 = original_image.copy()
    draw4 = ImageDraw.Draw(panel4)

    cluster_dict_final = cluster_dict_join.copy()

    # Post-processing: Assign any unassigned lines
    all_clustered_line_ids = set()
    for cluster_lines in cluster_dict_final.values():
        for line in cluster_lines:
            all_clustered_line_ids.add(line.line_id)

    unassigned_lines = [
        line for line in image_lines if line.line_id not in all_clustered_line_ids
    ]

    # Assign to nearest cluster using 2D distance
    for unassigned_line in unassigned_lines:
        unassigned_centroid = unassigned_line.calculate_centroid()
        unassigned_x, unassigned_y = unassigned_centroid
        nearest_cluster_id = None
        min_distance = float("inf")

        for cluster_id, cluster_lines in cluster_dict_final.items():
            cluster_x_coords = [
                line.calculate_centroid()[0] for line in cluster_lines
            ]
            cluster_y_coords = [
                line.calculate_centroid()[1] for line in cluster_lines
            ]
            if cluster_x_coords and cluster_y_coords:
                cluster_mean_x = sum(cluster_x_coords) / len(cluster_x_coords)
                cluster_mean_y = sum(cluster_y_coords) / len(cluster_y_coords)

                dx = unassigned_x - cluster_mean_x
                dy = unassigned_y - cluster_mean_y
                distance = math.sqrt(dx * dx + dy * dy)

                if distance < min_distance:
                    min_distance = distance
                    nearest_cluster_id = cluster_id

        if nearest_cluster_id is not None:
            cluster_dict_final[nearest_cluster_id].append(unassigned_line)

    # Draw final clusters
    for cluster_id, cluster_lines in cluster_dict_final.items():
        color = get_cluster_color(cluster_id, len(cluster_dict_final))
        for line in cluster_lines:
            corners = get_line_corners_image_coords(line, img_width, img_height)
            draw4.polygon(corners, outline=color, width=3)

    canvas.paste(panel4, (0, y_offset))
    draw.text(
        (10, y_offset + 10),
        f"4. Final Clusters ({len(cluster_dict_final)} clusters)",
        fill="black",
        font=font,
    )

    # Add cluster summary for final panel
    summary_y = y_offset + 40
    for cluster_id, cluster_lines in sorted(cluster_dict_final.items()):
        color = get_cluster_color(cluster_id, len(cluster_dict_final))
        draw.text(
            (10, summary_y),
            f"  Cluster {cluster_id}: {len(cluster_lines)} lines",
            fill=color,
            font=small_font,
        )
        summary_y += 20

    # Save
    canvas.save(output_path)
    print(f"💾 Saved clustering visualization to: {output_path}")
    print(f"   Total lines: {len(image_lines)}")
    print(f"   After angle splitting: {len(cluster_dict_angle)} clusters")
    for cluster_id, cluster_lines in sorted(cluster_dict_angle.items()):
        print(f"      Cluster {cluster_id}: {len(cluster_lines)} lines")
    print(f"   After X-proximity reassignment: {len(cluster_dict_x_reassigned)} clusters")
    for cluster_id, cluster_lines in sorted(cluster_dict_x_reassigned.items()):
        print(f"      Cluster {cluster_id}: {len(cluster_lines)} lines")
    print(f"   After vertical proximity: {len(cluster_dict_vertical)} clusters")
    for cluster_id, cluster_lines in sorted(cluster_dict_vertical.items()):
        print(f"      Cluster {cluster_id}: {len(cluster_lines)} lines")
    print(f"   After smart merging: {len(cluster_dict_merged)} clusters")
    for cluster_id, cluster_lines in sorted(cluster_dict_merged.items()):
        print(f"      Cluster {cluster_id}: {len(cluster_lines)} lines")
    print(f"   After join overlapping: {len(cluster_dict_join)} clusters")
    for cluster_id, cluster_lines in sorted(cluster_dict_join.items()):
        print(f"      Cluster {cluster_id}: {len(cluster_lines)} lines")
    print(f"   Final clusters: {len(cluster_dict_final)} clusters")
    for cluster_id, cluster_lines in sorted(cluster_dict_final.items()):
        print(f"      Cluster {cluster_id}: {len(cluster_lines)} lines")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Visualize clustering phases for split receipt debugging"
    )
    parser.add_argument("--image-id", required=True, help="Image ID")
    parser.add_argument("--receipt-id", type=int, required=True, help="Original receipt ID")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("clustering_phases_visualization.png"),
        help="Output image path",
    )
    parser.add_argument(
        "--raw-bucket",
        help="Raw S3 bucket name (or set RAW_BUCKET env var)",
        default=None,
    )
    args = parser.parse_args()

    raw_bucket = args.raw_bucket or os.environ.get("RAW_BUCKET")
    if not raw_bucket:
        print("⚠️  RAW_BUCKET not set; use --raw-bucket or set RAW_BUCKET env var")
        sys.exit(1)

    visualize_clustering_phases(
        args.image_id, args.receipt_id, args.output, raw_bucket
    )

