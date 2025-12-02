# Two-Phase Clustering Approach for Receipt Upload

## Overview

This document describes the two-phase clustering approach for correctly identifying receipts in scan images. The approach addresses edge cases where side-by-side receipts are incorrectly merged into a single receipt.

## Problem Statement

The current X-axis DBSCAN clustering (`dbscan_lines_x_axis`) sometimes incorrectly merges side-by-side receipts because:
1. It only considers X-coordinate proximity
2. It doesn't account for angle differences between receipts
3. It doesn't validate if merged clusters form coherent receipts

## Solution: Two-Phase Approach

### Phase 1: Tight Clustering (Angle-Based)
**Goal**: Create tight, conservative clusters to avoid over-merging

**Technique**:
- Use X-axis clustering (like current approach)
- **Add**: Split clusters by angle consistency
- Calculate angles from TL/TR corners (handles axis-aligned bboxes)
- If angles differ significantly within an X-group, split them

**Why**: Prevents merging receipts that are rotated differently or have different orientations

### Phase 2: Smart Merging (Combine Agent Logic)
**Goal**: Merge clusters that belong together but were split too aggressively

**Technique**:
- Evaluate each cluster pair using combine agent evaluation logic
- Check for:
  - Spatial coherence (lines close together)
  - Completeness (merchant, address, phone, total)
  - Line count (sufficient lines for a receipt)
  - X-proximity (clusters close horizontally)
- Prevent merging if:
  - Clusters are far apart horizontally (>40% of image width)
  - Different merchants detected
  - Large spatial gaps

**Why**: Combines clusters that are part of the same receipt but were split by angle differences

## Performance Considerations

**Important**: The two-phase approach does NOT use LLM calls. It uses pure Python evaluation logic based on the combine agent's scoring functions. This keeps the upload process fast.

**Smart Merging Heuristic**: Before running the expensive merging operation, a fast heuristic (`should_apply_smart_merging()`) checks if merging is needed:
- Skips merging if only 1-2 clusters (likely correct)
- Skips merging if clusters are well-separated spatially
- Skips merging if clusters have very different angles (likely different receipts)
- Only applies merging when 3+ clusters with suspicious patterns

This ensures the expensive merging only runs when necessary, keeping the upload process fast for the majority of images.

## Implementation Details

### Phase 1: Angle-Based Re-Clustering

```python
def recluster_by_angle_and_x(
    lines: List[Line],
    angle_tolerance: float = 3.0,
    x_eps: float = 0.08,
    min_samples: int = 2,
) -> Dict[int, List[Line]]:
    """
    Re-cluster lines based on calculated angles and X-coordinate proximity.

    1. Initial X-axis clustering (like dbscan_lines_x_axis)
    2. Split X-clusters by angle consistency
    """
    # Calculate angles from corners for all lines
    # Sort by X coordinate
    # Group by X proximity
    # Within each X-group, check angle consistency
    # Split if angles differ > tolerance
```

**Key Features**:
- Calculates angles from `top_left` and `top_right` corners
- Handles axis-aligned bounding boxes (OCR default)
- Uses circular mean for angle averaging
- Splits X-groups with inconsistent angles

### Phase 2: Smart Merging (Conditional)

```python
def should_apply_smart_merging(
    cluster_dict: Dict[int, List[Line]],
    total_lines: int,
) -> bool:
    """
    Fast heuristic to determine if smart merging is needed.
    Returns False to skip expensive merging when clustering looks good.
    """

def merge_clusters_with_agent_logic(
    clusters: Dict[int, List[Line]],
    min_score: float = 0.5,
) -> Dict[int, List[Line]]:
    """
    Greedily merge clusters using combine agent evaluation.

    NOTE: This does NOT use LLM calls - pure Python evaluation.

    1. Evaluate all cluster pairs
    2. Merge best pair that makes sense
    3. Repeat until no more good merges
    """
```

**Evaluation Criteria**:
- **Spatial Score** (30%): Lines close together (Y-range)
- **Completeness Score** (50%): Has merchant, address, phone, total
- **Line Count Score** (20%): Sufficient lines (3-10 ideal)
- **X-Proximity Check**: Prevents merging side-by-side receipts

**Merge Decision**:
- ✅ Merge if: coherence > 0.5, same side (X < 0.4 apart), no different merchants
- ❌ Don't merge if: far apart horizontally, different merchants, large gaps

## Integration into Upload Process

### Current Flow (scan.py)

```python
# Current: Single-phase clustering
cluster_dict = dbscan_lines_x_axis(ocr_lines)
cluster_dict = join_overlapping_clusters(
    cluster_dict, image.width, image.height, iou_threshold=0.01
)
```

### New Flow (Two-Phase)

```python
# Phase 1: Tight clustering with angle-based splitting
cluster_dict = dbscan_lines_x_axis(ocr_lines)
cluster_dict = split_clusters_by_angle_consistency(
    cluster_dict,
    angle_tolerance=3.0,  # degrees
)

# Phase 2: Smart merging using combine agent logic (only if needed)
if should_apply_smart_merging(cluster_dict, len(ocr_lines)):
    cluster_dict = merge_clusters_with_agent_logic(
        cluster_dict,
        min_score=0.5,
        x_proximity_threshold=0.4,
    )

# Existing: Join overlapping clusters (still useful for edge cases)
cluster_dict = join_overlapping_clusters(
    cluster_dict, image.width, image.height, iou_threshold=0.01
)
```

## Configuration

```python
class ClusteringConfig:
    # Phase 1: Angle-based splitting
    enable_angle_based_splitting: bool = True
    angle_tolerance: float = 3.0  # degrees
    x_eps: float = 0.08  # X-coordinate epsilon (normalized)
    min_samples: int = 2  # Minimum lines per cluster

    # Phase 2: Smart merging
    enable_smart_merging: bool = True
    min_merge_score: float = 0.5  # Minimum coherence to merge
    x_proximity_threshold: float = 0.4  # Don't merge if >40% apart
    max_y_range: float = 0.8  # Penalize if Y-range > 80%
```

## Benefits

1. **Handles Edge Cases**: Correctly separates side-by-side receipts
2. **Preserves Existing Behavior**: Still works for 500+ existing images
3. **Configurable**: Can be tuned or disabled if needed
4. **Additive**: New phases don't break existing logic
5. **Validated**: Uses proven combine agent evaluation logic

## Testing Strategy

1. **Regression Testing**: Test on 500+ existing images (should produce same or better results)
2. **Edge Case Testing**: Test on the two problem images
3. **Parameter Tuning**: Adjust `angle_tolerance` and `min_merge_score` based on results
4. **Visualization**: Generate images to verify clustering correctness

## Rollout Plan

1. **Phase 1**: Implement angle-based splitting (low risk)
2. **Phase 2**: Add smart merging (moderate risk)
3. **Monitor**: Track clustering results and adjust parameters
4. **Iterate**: Refine based on production feedback

## Files to Modify

1. `receipt_upload/receipt_upload/cluster.py`:
   - Add `split_clusters_by_angle_consistency()`
   - Add `merge_clusters_with_agent_logic()`
   - Add `calculate_angle_from_corners()` helper

2. `receipt_upload/receipt_upload/receipt_processing/scan.py`:
   - Integrate two-phase approach
   - Add configuration options

3. Tests:
   - Update `receipt_upload/test/test_cluster.py`
   - Add tests for angle-based splitting
   - Add tests for smart merging

## Example Results

### Image 1: `13da1048-3888-429f-b2aa-b3e15341da5e`
- **Before**: 1 receipt (incorrect - merged 2 receipts)
- **After Phase 1**: 9 clusters (too tight)
- **After Phase 2**: 2 receipts ✅ (correct)

### Image 2: `752cf8e2-cb69-4643-8f28-0ac9e3d2df25`
- **Before**: 1 receipt (incorrect - merged 2 receipts)
- **After Phase 1**: 3 clusters
- **After Phase 2**: 2 receipts ✅ (correct)

## Future Improvements

1. **Merchant Name Detection**: Use actual metadata to detect different merchants
2. **Adaptive Thresholds**: Adjust based on image characteristics
3. **Machine Learning**: Train model to predict optimal clustering
4. **Feedback Loop**: Learn from combine agent corrections

