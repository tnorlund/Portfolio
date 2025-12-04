# Clustering Parameters Documentation

This document describes the parameters used in the receipt clustering algorithm and their default values.

## Overview

The clustering algorithm uses a multi-phase approach:
1. **Phase 1**: X-axis DBSCAN clustering
2. **Phase 1b**: Split by angle consistency and vertical gaps
3. **Phase 1c**: Reassign lines by X-proximity within similar Y regions
4. **Phase 1d**: Reassign lines by vertical proximity (vertical stacking)
5. **Phase 2**: Smart merging using agent logic
6. **Final**: Join overlapping clusters

## Parameters

### Phase 1: Initial Clustering

#### `x_eps` (default: 0.08)
- **Type**: float
- **Description**: Epsilon parameter for X-axis DBSCAN clustering
- **Units**: Fraction of image width (normalized 0-1)
- **Effect**: Larger values create fewer, larger clusters
- **Range**: Typically 0.05-0.20
- **For receipt-level OCR**: Try 0.12-0.15 (more accurate coordinates may need larger epsilon)

### Phase 1b: Angle Consistency Splitting

#### `angle_tolerance` (default: 3.0)
- **Type**: float
- **Description**: Angle difference tolerance in degrees for splitting clusters
- **Units**: Degrees
- **Effect**: Larger values are less aggressive about splitting by angle
- **Range**: Typically 2.0-5.0

#### `vertical_gap_threshold` (default: 0.15)
- **Type**: float
- **Description**: Split cluster if vertical gap > this fraction of image height
- **Units**: Fraction of image height (normalized 0-1)
- **Effect**: Larger values are less aggressive about splitting by vertical gaps
- **Range**: Typically 0.10-0.25
- **For receipt-level OCR**: Try 0.20-0.25 (more accurate coordinates may reveal gaps that shouldn't split)

### Phase 1c: X-Proximity Reassignment

#### `reassign_x_proximity_threshold` (default: 0.1)
- **Type**: float
- **Description**: X-proximity threshold for reassignment phase
- **Units**: Fraction of image width (normalized 0-1)
- **Effect**: Lines within this horizontal distance can be reassigned
- **Range**: Typically 0.05-0.15

#### `reassign_y_proximity_threshold` (default: 0.15)
- **Type**: float
- **Description**: Y-proximity threshold for reassignment phase
- **Units**: Fraction of image height (normalized 0-1)
- **Effect**: Lines must be within this vertical distance to be reassigned
- **Range**: Typically 0.10-0.20

### Phase 1d: Vertical Proximity Reassignment

#### `vertical_proximity_threshold` (default: 0.05)
- **Type**: float
- **Description**: Vertical proximity threshold for vertical stacking
- **Units**: Fraction of image height (normalized 0-1)
- **Effect**: Lines within this vertical distance can be reassigned
- **Range**: Typically 0.03-0.10

#### `vertical_x_proximity_threshold` (default: 0.1)
- **Type**: float
- **Description**: X-proximity threshold for vertical stacking
- **Units**: Fraction of image width (normalized 0-1)
- **Effect**: Lines must be within this horizontal distance for vertical reassignment
- **Range**: Typically 0.05-0.15

### Phase 2: Smart Merging

#### `merge_min_score` (default: 0.5)
- **Type**: float
- **Description**: Minimum coherence score to merge clusters
- **Units**: Score (0.0-1.0)
- **Effect**: Lower values allow more aggressive merging
- **Range**: Typically 0.3-0.7

#### `merge_x_proximity_threshold` (default: 0.4)
- **Type**: float
- **Description**: Don't merge clusters if > this fraction apart horizontally
- **Units**: Fraction of image width (normalized 0-1)
- **Effect**: Larger values allow merging clusters further apart
- **Range**: Typically 0.3-0.6
- **For receipt-level OCR**: Try 0.5-0.6 (more accurate coordinates may have clusters that appear further apart but should merge)

### Final: Join Overlapping Clusters

#### `join_iou_threshold` (default: 0.1)
- **Type**: float
- **Description**: IoU threshold for joining overlapping clusters
- **Units**: Intersection over Union (0.0-1.0)
- **Effect**: Larger values require more overlap to join
- **Range**: Typically 0.05-0.20

## Current Default Values

These are the values currently used for image-level OCR:

```python
{
    "x_eps": 0.08,                          # 8% of image width
    "angle_tolerance": 3.0,                 # 3.0 degrees
    "vertical_gap_threshold": 0.15,         # 15% of image height
    "reassign_x_proximity_threshold": 0.1,  # 10% of image width
    "reassign_y_proximity_threshold": 0.15, # 15% of image height
    "vertical_proximity_threshold": 0.05,   # 5% of image height
    "vertical_x_proximity_threshold": 0.1,  # 10% of image width
    "merge_min_score": 0.5,                 # 0.5 score
    "merge_x_proximity_threshold": 0.4,     # 40% of image width
    "join_iou_threshold": 0.1,              # 0.1 IoU
}
```

## Recommendations for Receipt-Level OCR

When using receipt-level OCR (more accurate coordinates), consider:

1. **Increase `x_eps`** to 0.12-0.15 (larger initial clusters)
2. **Increase `merge_x_proximity_threshold`** to 0.5-0.6 (allow merging clusters further apart)
3. **Increase `vertical_gap_threshold`** to 0.20-0.25 (less aggressive splitting)
4. **Decrease `merge_min_score`** to 0.3-0.4 (more aggressive merging)

## Usage Examples

### Basic usage (image-level OCR):
```bash
python scripts/visualize_final_clusters_cropped.py \
    --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
    --output-dir output
```

### Receipt-level OCR with adjusted parameters:
```bash
python scripts/visualize_final_clusters_cropped.py \
    --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
    --receipt-id 1 \
    --x-eps 0.15 \
    --merge-x-proximity-threshold 0.6 \
    --vertical-gap-threshold 0.25 \
    --output-dir output
```

### Parameter sweeping:
```bash
for eps in 0.10 0.12 0.15 0.18; do
  for merge_thresh in 0.4 0.5 0.6; do
    python scripts/visualize_final_clusters_cropped.py \
        --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
        --receipt-id 1 \
        --x-eps $eps \
        --merge-x-proximity-threshold $merge_thresh \
        --output-dir "output_eps${eps}_merge${merge_thresh}"
  done
done
```

## Parameter Impact Summary

| Parameter | Increase Effect | Decrease Effect |
|-----------|----------------|----------------|
| `x_eps` | Fewer, larger clusters | More, smaller clusters |
| `angle_tolerance` | Less splitting by angle | More splitting by angle |
| `vertical_gap_threshold` | Less splitting by gaps | More splitting by gaps |
| `merge_x_proximity_threshold` | More merging allowed | Less merging allowed |
| `merge_min_score` | Stricter merging | More aggressive merging |

