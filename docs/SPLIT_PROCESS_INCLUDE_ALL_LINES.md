# Split Process: Include All Lines (Including Noise)

## Problem

The split receipt process was excluding some lines from the final clusters:
- **8 lines** were being dropped during angle-based splitting (lines that didn't meet `min_samples=2`)
- These lines were marked as "noise" and excluded from clusters
- This resulted in missing lines in the split receipts (e.g., receipt 3 was missing 41 lines)

## Root Cause

The clustering process has multiple phases that can exclude lines:

1. **X-axis clustering** (`dbscan_lines_x_axis`): Marks lines as noise (cluster_id = -1) if they don't meet `min_samples=2`
2. **Angle-based splitting** (`split_clusters_by_angle_consistency`): Drops lines that don't meet `min_samples=2` after splitting
3. **Smart merging**: Only merges existing clusters, doesn't handle unassigned lines
4. **Join overlapping**: Only merges existing clusters, doesn't handle unassigned lines

## Solution

Added a **post-processing step** in `recluster_receipt_lines()` that assigns any unassigned lines to the nearest cluster based on X-coordinate proximity.

### Implementation

```python
# Post-processing: Assign any unassigned lines to nearest cluster
# This ensures ALL lines are included, even noise lines
all_clustered_line_ids = set()
for cluster_lines in cluster_dict.values():
    for line in cluster_lines:
        all_clustered_line_ids.add(line.line_id)

unassigned_lines = [
    line for line in image_lines if line.line_id not in all_clustered_line_ids
]

if unassigned_lines:
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
        else:
            # No clusters exist, create a new one
            new_cluster_id = max(cluster_dict.keys()) + 1 if cluster_dict else 1
            cluster_dict[new_cluster_id] = [unassigned_line]
```

## Results

### Before Fix
- **148 lines** included in clusters (8 missing)
- Receipt 2: 101 lines
- Receipt 3: 36 lines
- **Total: 137 lines** (5 lines missing from original receipt's 142 ReceiptLines)

### After Fix
- **156 lines** included in clusters (all lines)
- Cluster 1: 102 lines
- Cluster 2: 54 lines (includes the 8 previously unassigned lines)
- **All lines assigned to clusters**

## Why Include Noise Lines?

The user clarified: **"We want to include all lines, including noise. The noise is what we ignore for embedding, not visualizing."**

This means:
- All lines should be included in the receipt structure (for visualization)
- Noise lines are only excluded from embedding (handled separately in the embedding process)
- The split receipt process should preserve all lines for display purposes

## Testing

Use `scripts/trace_split_clustering.py` to verify all lines are included:

```bash
python scripts/trace_split_clustering.py --image-id 13da1048-3888-429f-b2aa-b3e15341da5e
```

The output should show:
- ✅ All 156 lines assigned to clusters
- Missing: 0 lines

