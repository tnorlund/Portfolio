#!/usr/bin/env python3
"""
Re-cluster receipt lines using calculated angles and X-coordinate analysis,
then visualize the results.

This script:
1. Loads receipt data from local JSON files
2. Calculates angles from corner coordinates (handles axis-aligned bboxes)
3. Re-clusters lines based on angle consistency and X-coordinate proximity
4. Creates a visualization image showing the proposed clusters
5. Saves the visualization for review

Usage:
    python scripts/recluster_and_visualize.py \
        --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
        --output-dir ./local_receipt_data \
        --output-image ./clustering_visualization.png
"""

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Dict, List, Tuple
from collections import defaultdict

# Add repo root to path
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))

try:
    from PIL import Image, ImageDraw, ImageFont
    PIL_AVAILABLE = True
except ImportError:
    PIL_AVAILABLE = False
    print("⚠️  PIL/Pillow not available - visualization will be limited")

from receipt_dynamo.entities import ReceiptLine


def calculate_angle_from_corners(tl: Dict, tr: Dict) -> float:
    """Calculate angle in degrees from top-left and top-right corners.

    Returns angle in degrees, where:
    - 0° = perfectly horizontal (left to right)
    - Positive = rotated counter-clockwise
    - Negative = rotated clockwise
    """
    dx = tr['x'] - tl['x']
    dy = tr['y'] - tl['y']

    # Handle axis-aligned bounding boxes (dx might be very small)
    if abs(dx) < 1e-6:
        # Vertical line
        return 90.0 if dy > 0 else -90.0

    angle_rad = math.atan2(dy, dx)
    angle_deg = math.degrees(angle_rad)
    return angle_deg


def calculate_line_centroid(line_data: Dict) -> Tuple[float, float]:
    """Calculate centroid of a line from its corners."""
    x = (line_data['top_left']['x'] + line_data['top_right']['x'] +
         line_data['bottom_left']['x'] + line_data['bottom_right']['x']) / 4
    y = (line_data['top_left']['y'] + line_data['top_right']['y'] +
         line_data['bottom_left']['y'] + line_data['bottom_right']['y']) / 4
    return x, y


def angle_difference(a1: float, a2: float) -> float:
    """Calculate smallest angle difference between two angles."""
    diff = abs(a1 - a2)
    return min(diff, 360 - diff)


def circular_mean(angles: List[float]) -> float:
    """Calculate mean of angles handling wraparound."""
    angles_rad = [math.radians(a) for a in angles]
    mean_sin = sum(math.sin(a) for a in angles_rad) / len(angles_rad)
    mean_cos = sum(math.cos(a) for a in angles_rad) / len(angles_rad)
    return math.degrees(math.atan2(mean_sin, mean_cos))


def recluster_by_angle_and_x(
    lines: List[Dict],
    angle_tolerance: float = 3.0,
    x_eps: float = 0.08,
    min_samples: int = 2,
) -> Dict[int, List[Dict]]:
    """
    Re-cluster lines based on calculated angles and X-coordinate proximity.

    Strategy:
    1. Calculate angles from corners for each line
    2. Group lines by X-coordinate first (like original DBSCAN)
    3. Within each X-group, check angle consistency
    4. Split groups with inconsistent angles

    Args:
        lines: List of line dictionaries
        angle_tolerance: Maximum angle difference within a cluster (degrees)
        x_eps: Maximum X-coordinate difference for initial grouping (normalized)
        min_samples: Minimum lines per cluster

    Returns:
        Dictionary mapping cluster_id -> list of lines
    """
    if not lines:
        return {}

    # Calculate angles and X coordinates for all lines
    line_data = []
    for line in lines:
        angle = calculate_angle_from_corners(line['top_left'], line['top_right'])
        x, y = calculate_line_centroid(line)
        line_data.append({
            'line': line,
            'angle': angle,
            'x': x,
            'y': y,
        })

    # Sort by X coordinate (like original dbscan_lines_x_axis)
    line_data.sort(key=lambda ld: ld['x'])

    # Step 1: Initial X-axis clustering (similar to dbscan_lines_x_axis)
    x_clusters = []
    current_x_cluster = [line_data[0]]

    for i in range(1, len(line_data)):
        current_x = line_data[i]['x']
        prev_x = line_data[i-1]['x']

        if abs(current_x - prev_x) <= x_eps:
            # Add to current cluster
            current_x_cluster.append(line_data[i])
        else:
            # Start new cluster
            if len(current_x_cluster) >= min_samples:
                x_clusters.append(current_x_cluster)
            current_x_cluster = [line_data[i]]

    # Add last cluster
    if len(current_x_cluster) >= min_samples:
        x_clusters.append(current_x_cluster)

    # Step 2: Split X-clusters by angle consistency
    final_clusters = {}
    cluster_id = 1

    for x_cluster in x_clusters:
        if len(x_cluster) < min_samples:
            continue

        # Calculate mean angle for this X-cluster
        angles = [ld['angle'] for ld in x_cluster]
        mean_angle = circular_mean(angles)

        # Check if all angles are consistent
        max_angle_diff = max(angle_difference(a, mean_angle) for a in angles)

        if max_angle_diff <= angle_tolerance:
            # All angles consistent - keep as single cluster
            final_clusters[cluster_id] = [ld['line'] for ld in x_cluster]
            cluster_id += 1
        else:
            # Angles inconsistent - split by angle
            # Group by angle similarity
            angle_groups = []
            current_angle_group = [x_cluster[0]]

            for i in range(1, len(x_cluster)):
                current_angle = x_cluster[i]['angle']
                prev_angle = x_cluster[i-1]['angle']

                # Check if angle is similar to current group's mean
                group_angles = [ld['angle'] for ld in current_angle_group]
                group_mean = circular_mean(group_angles)

                if angle_difference(current_angle, group_mean) <= angle_tolerance:
                    current_angle_group.append(x_cluster[i])
                else:
                    # Start new angle group
                    if len(current_angle_group) >= min_samples:
                        angle_groups.append(current_angle_group)
                    current_angle_group = [x_cluster[i]]

            # Add last group
            if len(current_angle_group) >= min_samples:
                angle_groups.append(current_angle_group)

            # Assign each angle group to a cluster
            for angle_group in angle_groups:
                final_clusters[cluster_id] = [ld['line'] for ld in angle_group]
                cluster_id += 1

    return final_clusters


def create_visualization(
    original_lines: List[Dict],
    new_clusters: Dict[int, List[Dict]],
    output_path: Path,
    image_width: int = 2000,
    image_height: int = 2800,
) -> None:
    """
    Create a visualization image showing original lines and new clusters.

    Colors:
    - Original lines: Gray
    - New clusters: Different colors per cluster
    """
    if not PIL_AVAILABLE:
        print("⚠️  Cannot create visualization - PIL not available")
        return

    # Create image
    img = Image.new('RGB', (image_width, image_height), color='white')
    draw = ImageDraw.Draw(img)

    # Color palette for clusters
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

    # Draw original lines in light gray
    for line in original_lines:
        tl = line['top_left']
        tr = line['top_right']
        br = line['bottom_right']
        bl = line['bottom_left']

        # Convert normalized coordinates to pixels
        # Note: Y coordinates are in OCR space (0 at bottom), need to flip
        points = [
            (int(tl['x'] * image_width), int((1 - tl['y']) * image_height)),
            (int(tr['x'] * image_width), int((1 - tr['y']) * image_height)),
            (int(br['x'] * image_width), int((1 - br['y']) * image_height)),
            (int(bl['x'] * image_width), int((1 - bl['y']) * image_height)),
        ]

        # Draw bounding box
        draw.polygon(points, outline=(200, 200, 200), width=1)

    # Draw new clusters in different colors
    for cluster_id, cluster_lines in new_clusters.items():
        color = colors[(cluster_id - 1) % len(colors)]

        for line in cluster_lines:
            tl = line['top_left']
            tr = line['top_right']
            br = line['bottom_right']
            bl = line['bottom_left']

            points = [
                (int(tl['x'] * image_width), int((1 - tl['y']) * image_height)),
                (int(tr['x'] * image_width), int((1 - tr['y']) * image_height)),
                (int(br['x'] * image_width), int((1 - br['y']) * image_height)),
                (int(bl['x'] * image_width), int((1 - bl['y']) * image_height)),
            ]

            # Draw bounding box with cluster color
            draw.polygon(points, outline=color, width=3)

            # Draw line ID
            centroid_x = (tl['x'] + tr['x'] + bl['x'] + br['x']) / 4 * image_width
            centroid_y = (1 - (tl['y'] + tr['y'] + bl['y'] + br['y']) / 4) * image_height
            try:
                font = ImageFont.truetype("/System/Library/Fonts/Helvetica.ttc", 12)
            except:
                font = ImageFont.load_default()
            draw.text((centroid_x, centroid_y), f"{line['line_id']}", fill=color, font=font)

    # Add legend
    legend_y = 20
    draw.text((20, legend_y), "Original (gray) vs Re-clustered (colored)", fill=(0, 0, 0), font=ImageFont.load_default())
    legend_y += 30

    for cluster_id in sorted(new_clusters.keys()):
        color = colors[(cluster_id - 1) % len(colors)]
        draw.rectangle([20, legend_y, 40, legend_y + 15], outline=color, width=3)
        draw.text((45, legend_y), f"Cluster {cluster_id}: {len(new_clusters[cluster_id])} lines",
                 fill=(0, 0, 0), font=ImageFont.load_default())
        legend_y += 20

    # Save image
    img.save(output_path)
    print(f"💾 Saved visualization to: {output_path}")


def main():
    parser = argparse.ArgumentParser(
        description="Re-cluster receipt lines and create visualization"
    )
    parser.add_argument(
        "--image-id",
        required=True,
        help="Image ID to process",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("./local_receipt_data"),
        help="Directory containing downloaded receipt data",
    )
    parser.add_argument(
        "--output-image",
        type=Path,
        help="Output path for visualization image (default: <output-dir>/<image-id>_recluster.png)",
    )
    parser.add_argument(
        "--angle-tolerance",
        type=float,
        default=3.0,
        help="Angle tolerance in degrees (default: 3.0)",
    )
    parser.add_argument(
        "--x-eps",
        type=float,
        default=0.08,
        help="X-coordinate epsilon for initial grouping (default: 0.08)",
    )
    parser.add_argument(
        "--min-samples",
        type=int,
        default=2,
        help="Minimum lines per cluster (default: 2)",
    )

    args = parser.parse_args()

    # Load receipt data
    image_dir = args.output_dir / args.image_id
    receipts_file = image_dir / "receipts.json"

    if not receipts_file.exists():
        print(f"❌ Receipts file not found: {receipts_file}")
        sys.exit(1)

    with open(receipts_file) as f:
        receipts_data = json.load(f)

    if not receipts_data:
        print(f"❌ No receipts found for image {args.image_id}")
        sys.exit(1)

    # For now, process the first receipt
    receipt = receipts_data[0]
    receipt_id = receipt['receipt_id']

    print(f"📊 Processing image: {args.image_id}")
    print(f"   Receipt ID: {receipt_id}")
    print(f"   Receipt dimensions: {receipt['width']} x {receipt['height']}")

    # Load lines
    lines_file = image_dir / f"receipt_{receipt_id:05d}" / "lines.json"
    if not lines_file.exists():
        print(f"❌ Lines file not found: {lines_file}")
        sys.exit(1)

    with open(lines_file) as f:
        lines = json.load(f)

    print(f"   Total lines: {len(lines)}")

    # Calculate angles for all lines
    print(f"\n📐 Calculating angles from corners...")
    angles = []
    for line in lines:
        angle = calculate_angle_from_corners(line['top_left'], line['top_right'])
        angles.append(angle)

    import statistics
    print(f"   Angle range: {min(angles):.2f}° to {max(angles):.2f}°")
    print(f"   Mean angle: {statistics.mean(angles):.2f}°")
    print(f"   Std dev: {statistics.stdev(angles):.2f}°")

    # Re-cluster
    print(f"\n🔄 Re-clustering with angle tolerance={args.angle_tolerance}°, x_eps={args.x_eps}...")
    new_clusters = recluster_by_angle_and_x(
        lines,
        angle_tolerance=args.angle_tolerance,
        x_eps=args.x_eps,
        min_samples=args.min_samples,
    )

    print(f"   Original: 1 cluster with {len(lines)} lines")
    print(f"   Re-clustered: {len(new_clusters)} clusters")
    for cluster_id in sorted(new_clusters.keys()):
        print(f"      Cluster {cluster_id}: {len(new_clusters[cluster_id])} lines")

    # Create visualization
    if PIL_AVAILABLE:
        output_image = args.output_image or (image_dir / f"{args.image_id}_recluster.png")
        print(f"\n🎨 Creating visualization...")
        create_visualization(
            lines,
            new_clusters,
            output_image,
            image_width=receipt['width'],
            image_height=receipt['height'],
        )
    else:
        print(f"\n⚠️  Skipping visualization (PIL not available)")

    # Save cluster assignments
    cluster_assignments = {}
    for cluster_id, cluster_lines in new_clusters.items():
        for line in cluster_lines:
            cluster_assignments[line['line_id']] = cluster_id

    assignments_file = image_dir / f"{args.image_id}_cluster_assignments.json"
    with open(assignments_file, 'w') as f:
        json.dump(cluster_assignments, f, indent=2)
    print(f"💾 Saved cluster assignments to: {assignments_file}")


if __name__ == "__main__":
    main()

