#!/usr/bin/env python3
"""Analyze clustering for image 752cf8e2-cb69-4643-8f28-0ac9e3d2df25"""

import sys
from pathlib import Path

repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))

# Fix import path for receipt_upload
sys.path.insert(0, str(repo_root / "receipt_upload"))

from receipt_dynamo import DynamoClient
from receipt_upload.cluster import (
    dbscan_lines_x_axis,
    split_clusters_by_angle_consistency,
    calculate_angle_from_corners,
)

client = DynamoClient('ReceiptsTable-dc5be22')
image_id = '752cf8e2-cb69-4643-8f28-0ac9e3d2df25'

image_lines = client.list_lines_from_image(image_id)
image_entity = client.get_image(image_id)

print(f'📊 Step-by-step clustering analysis for {image_id}')
print('=' * 60)

# Check angles of lines on left vs right
left_lines = [l for l in image_lines if l.calculate_centroid()[0] < 0.5]
right_lines = [l for l in image_lines if l.calculate_centroid()[0] >= 0.5]

print(f'\nLeft side ({len(left_lines)} lines):')
left_angles = [calculate_angle_from_corners(l) for l in left_lines]
if left_angles:
    avg_left_angle = sum(left_angles) / len(left_angles)
    min_left_angle = min(left_angles)
    max_left_angle = max(left_angles)
    print(f'  Angles: {min_left_angle:.2f}° - {max_left_angle:.2f}° (avg: {avg_left_angle:.2f}°)')

print(f'\nRight side ({len(right_lines)} lines):')
right_angles = [calculate_angle_from_corners(l) for l in right_lines]
if right_angles:
    avg_right_angle = sum(right_angles) / len(right_angles)
    min_right_angle = min(right_angles)
    max_right_angle = max(right_angles)
    print(f'  Angles: {min_right_angle:.2f}° - {max_right_angle:.2f}° (avg: {avg_right_angle:.2f}°)')
    print(f'  Angle difference from left: {abs(avg_right_angle - avg_left_angle):.2f}°')

# Step 1: X-axis clustering
cluster_dict_x = dbscan_lines_x_axis(image_lines, eps=0.08)
print(f'\n1. After X-axis clustering: {len(cluster_dict_x)} clusters')
for cid, lines in sorted(cluster_dict_x.items()):
    x_coords = [l.calculate_centroid()[0] for l in lines]
    avg_x = sum(x_coords) / len(x_coords)
    angles = [calculate_angle_from_corners(l) for l in lines]
    avg_angle = sum(angles) / len(angles) if angles else 0
    print(f'   Cluster {cid}: {len(lines)} lines, avg X: {avg_x:.3f}, avg angle: {avg_angle:.2f}°')

# Step 2: Angle splitting
cluster_dict_angle = split_clusters_by_angle_consistency(
    cluster_dict_x,
    angle_tolerance=3.0,
    min_samples=2,
)
print(f'\n2. After angle splitting: {len(cluster_dict_angle)} clusters')
for cid, lines in sorted(cluster_dict_angle.items()):
    x_coords = [l.calculate_centroid()[0] for l in lines]
    y_coords = [l.calculate_centroid()[1] for l in lines]
    avg_x = sum(x_coords) / len(x_coords)
    min_x, max_x = min(x_coords), max(x_coords)
    angles = [calculate_angle_from_corners(l) for l in lines]
    avg_angle = sum(angles) / len(angles) if angles else 0
    print(f'   Cluster {cid}: {len(lines)} lines')
    print(f'      X: {min_x:.3f}-{max_x:.3f} (avg: {avg_x:.3f})')
    print(f'      Angle: {avg_angle:.2f}°')
    # Count left vs right
    left_count = sum(1 for l in lines if l.calculate_centroid()[0] < 0.5)
    right_count = sum(1 for l in lines if l.calculate_centroid()[0] >= 0.5)
    print(f'      Left side: {left_count}, Right side: {right_count}')

    # Show sample lines
    print(f'      Sample lines:')
    for i, line in enumerate(lines[:5]):
        x, y = line.calculate_centroid()
        print(f'        {i+1}. X={x:.3f}, Y={y:.3f}, \"{line.text[:50]}...\"')
    if len(lines) > 5:
        print(f'        ... and {len(lines) - 5} more')

