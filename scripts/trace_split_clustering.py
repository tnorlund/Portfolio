#!/usr/bin/env python3
"""
Trace through the split clustering process to debug missing lines.

This script shows what happens at each phase of clustering:
1. X-axis clustering
2. Angle-based splitting
3. Smart merging
4. Final join overlapping
"""

import sys
from pathlib import Path

# Add repo root to path
repo_root = Path(__file__).parent.parent
sys.path.insert(0, str(repo_root))
sys.path.insert(0, str(repo_root / "receipt_upload"))

from receipt_dynamo import DynamoClient
from receipt_upload.cluster import (
    dbscan_lines_x_axis,
    split_clusters_by_angle_consistency,
    should_apply_smart_merging,
    merge_clusters_with_agent_logic,
    join_overlapping_clusters,
)


def trace_clustering(image_id: str):
    """Trace through the clustering process step by step."""
    client = DynamoClient('ReceiptsTable-dc5be22')

    # Load image-level Lines (what clustering uses)
    image_lines = client.list_lines_from_image(image_id)
    image_entity = client.get_image(image_id)

    print(f"📊 Tracing clustering for image: {image_id}")
    print(f"   Total image lines: {len(image_lines)}")
    print()

    # Phase 1: X-axis clustering
    print("Phase 1: X-axis clustering (dbscan_lines_x_axis)")
    cluster_dict = dbscan_lines_x_axis(image_lines)
    print(f"   Result: {len(cluster_dict)} clusters")

    all_clustered_line_ids = set()
    for cluster_id, cluster_lines in cluster_dict.items():
        line_ids = {line.line_id for line in cluster_lines}
        all_clustered_line_ids.update(line_ids)
        print(f"      Cluster {cluster_id}: {len(cluster_lines)} lines (IDs: {sorted(line_ids)[:10]}...)")

    noise_line_ids = set(range(1, len(image_lines) + 1)) - all_clustered_line_ids
    if noise_line_ids:
        print(f"   ⚠️  Noise lines (excluded): {len(noise_line_ids)} lines")
        print(f"      Noise line IDs: {sorted(noise_line_ids)[:20]}...")
    print()

    # Phase 1b: Split by angle consistency
    print("Phase 1b: Split by angle consistency")
    cluster_dict = split_clusters_by_angle_consistency(
        cluster_dict,
        angle_tolerance=3.0,
        min_samples=2,
    )
    print(f"   Result: {len(cluster_dict)} clusters")

    all_clustered_line_ids = set()
    for cluster_id, cluster_lines in cluster_dict.items():
        line_ids = {line.line_id for line in cluster_lines}
        all_clustered_line_ids.update(line_ids)
        print(f"      Cluster {cluster_id}: {len(cluster_lines)} lines")

    noise_line_ids = set(range(1, len(image_lines) + 1)) - all_clustered_line_ids
    if noise_line_ids:
        print(f"   ⚠️  Lines dropped during angle splitting: {len(noise_line_ids)} lines")
        print(f"      Dropped line IDs: {sorted(noise_line_ids)[:20]}...")
    print()

    # Phase 2: Smart merging
    print("Phase 2: Smart merging (if needed)")
    should_merge = should_apply_smart_merging(cluster_dict, len(image_lines))
    print(f"   Should apply smart merging: {should_merge}")

    if should_merge:
        cluster_dict = merge_clusters_with_agent_logic(
            cluster_dict,
            min_score=0.5,
            x_proximity_threshold=0.4,
        )
        print(f"   Result: {len(cluster_dict)} clusters")

        all_clustered_line_ids = set()
        for cluster_id, cluster_lines in cluster_dict.items():
            line_ids = {line.line_id for line in cluster_lines}
            all_clustered_line_ids.update(line_ids)
            print(f"      Cluster {cluster_id}: {len(cluster_lines)} lines")
    print()

    # Final: Join overlapping clusters
    print("Final: Join overlapping clusters")
    cluster_dict = join_overlapping_clusters(
        cluster_dict, image_entity.width, image_entity.height, iou_threshold=0.01
    )
    print(f"   Result: {len(cluster_dict)} clusters")

    all_clustered_line_ids = set()
    for cluster_id, cluster_lines in cluster_dict.items():
        line_ids = {line.line_id for line in cluster_lines}
        all_clustered_line_ids.update(line_ids)
        print(f"      Cluster {cluster_id}: {len(cluster_lines)} lines")

    noise_line_ids = set(range(1, len(image_lines) + 1)) - all_clustered_line_ids
    if noise_line_ids:
        print(f"   ⚠️  Missing lines before post-processing: {len(noise_line_ids)} lines")
        print(f"      Missing line IDs: {sorted(noise_line_ids)}")
    print()

    # Post-processing: Assign unassigned lines to nearest cluster
    print("Post-processing: Assign unassigned lines to nearest cluster")
    unassigned_lines = [
        line for line in image_lines if line.line_id not in all_clustered_line_ids
    ]

    if unassigned_lines:
        print(f"   Found {len(unassigned_lines)} unassigned lines")
        # Assign each unassigned line to the nearest cluster based on X-coordinate
        for unassigned_line in unassigned_lines:
            unassigned_x = unassigned_line.calculate_centroid()[0]

            # Find nearest cluster by X-coordinate
            nearest_cluster_id = None
            min_x_distance = float('inf')

            for cluster_id, cluster_lines in cluster_dict.items():
                cluster_x_coords = [
                    line.calculate_centroid()[0] for line in cluster_lines
                ]
                if cluster_x_coords:
                    cluster_mean_x = sum(cluster_x_coords) / len(cluster_x_coords)
                    x_distance = abs(unassigned_x - cluster_mean_x)
                    if x_distance < min_x_distance:
                        min_x_distance = x_distance
                        nearest_cluster_id = cluster_id

            # Assign to nearest cluster (or create new cluster if none exist)
            if nearest_cluster_id is not None:
                cluster_dict[nearest_cluster_id].append(unassigned_line)
                print(f"      Assigned line {unassigned_line.line_id} to cluster {nearest_cluster_id} (X distance: {min_x_distance:.4f})")
            else:
                # No clusters exist, create a new one
                new_cluster_id = max(cluster_dict.keys()) + 1 if cluster_dict else 1
                cluster_dict[new_cluster_id] = [unassigned_line]
                print(f"      Created new cluster {new_cluster_id} for line {unassigned_line.line_id}")
    else:
        print("   No unassigned lines")
    print()

    # Final check
    all_clustered_line_ids = set()
    for cluster_id, cluster_lines in cluster_dict.items():
        line_ids = {line.line_id for line in cluster_lines}
        all_clustered_line_ids.update(line_ids)
        print(f"      Cluster {cluster_id}: {len(cluster_lines)} lines")

    noise_line_ids = set(range(1, len(image_lines) + 1)) - all_clustered_line_ids
    if noise_line_ids:
        print(f"   ⚠️  Still missing lines: {len(noise_line_ids)} lines")
        print(f"      Missing line IDs: {sorted(noise_line_ids)}")
    else:
        print(f"   ✅ All {len(image_lines)} lines assigned to clusters")

    print()
    print(f"✅ Final clustering: {len(cluster_dict)} clusters, {len(all_clustered_line_ids)} lines included")
    print(f"   Missing: {len(noise_line_ids)} lines")

    return cluster_dict, noise_line_ids


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Trace split clustering process")
    parser.add_argument("--image-id", required=True, help="Image ID")
    args = parser.parse_args()

    trace_clustering(args.image_id)

