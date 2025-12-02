# Clustering Improvements for Edge Cases

## Problem Statement
Two scan images are incorrectly clustering receipts:
- `13da1048-3888-429f-b2aa-b3e15341da5e`
- `752cf8e2-cb69-4643-8f28-0ac9e3d2df25`

We need to add techniques that:
1. Fix edge cases without breaking existing functionality (500+ images working)
2. Use available Line and Word attributes
3. Can be added as post-processing refinements

## Available Attributes

### Line Entity
- `text`: Text content of the line
- `bounding_box`: `{x, y, width, height}` (normalized 0-1)
- `top_left`, `top_right`, `bottom_left`, `bottom_right`: Corner coordinates (normalized)
- `angle_degrees`, `angle_radians`: Rotation angle
- `confidence`: Detection confidence (0-1)
- `calculate_centroid()`: Returns (x, y) centroid (normalized)
- `calculate_diagonal_length()`: Returns diagonal length

### Word Entity
- `text`: Word text
- `bounding_box`: `{x, y, width, height}` (normalized)
- `top_left`, `top_right`, `bottom_left`, `bottom_right`: Corner coordinates
- `angle_degrees`, `angle_radians`: Rotation angle
- `confidence`: Detection confidence
- `line_id`: Parent line ID
- `calculate_centroid()`: Returns (x, y) centroid

## Proposed Techniques

### 1. **Vertical Gap Detection** (High Priority)
**Problem**: Current X-axis clustering ignores Y-coordinate, which can merge vertically separated receipts.

**Solution**: After initial clustering, detect large vertical gaps within clusters and split them.

```python
def split_clusters_by_vertical_gaps(
    cluster_dict: Dict[int, List[Line]],
    image_height: int,
    gap_threshold: float = 0.15,  # 15% of image height
) -> Dict[int, List[Line]]:
    """
    Split clusters that have large vertical gaps between lines.
    This handles cases where two receipts are side-by-side but vertically separated.
    """
    split_clusters = {}
    new_cluster_id = max(cluster_dict.keys()) + 1 if cluster_dict else 1

    for cluster_id, lines in cluster_dict.items():
        if len(lines) < 3:
            # Keep small clusters as-is
            split_clusters[cluster_id] = lines
            continue

        # Sort lines by Y coordinate (top to bottom)
        lines_sorted = sorted(
            lines,
            key=lambda ln: ln.calculate_centroid()[1]
        )

        # Find gaps larger than threshold
        sub_clusters = []
        current_sub_cluster = [lines_sorted[0]]

        for i in range(1, len(lines_sorted)):
            prev_y = lines_sorted[i-1].calculate_centroid()[1]
            curr_y = lines_sorted[i].calculate_centroid()[1]
            gap = (curr_y - prev_y) * image_height  # Convert to pixels

            if gap > gap_threshold * image_height:
                # Large gap detected - start new sub-cluster
                sub_clusters.append(current_sub_cluster)
                current_sub_cluster = [lines_sorted[i]]
            else:
                current_sub_cluster.append(lines_sorted[i])

        sub_clusters.append(current_sub_cluster)

        # Assign sub-clusters to cluster IDs
        if len(sub_clusters) == 1:
            # No split needed
            split_clusters[cluster_id] = lines
        else:
            # Split into multiple clusters
            split_clusters[cluster_id] = sub_clusters[0]
            for sub_cluster in sub_clusters[1:]:
                split_clusters[new_cluster_id] = sub_cluster
                new_cluster_id += 1

    return split_clusters
```

**When to apply**: After `dbscan_lines_x_axis()` but before `join_overlapping_clusters()`

**Edge cases handled**:
- Two receipts side-by-side with vertical separation
- Receipts with large blank spaces in the middle

---

### 2. **Angle Consistency Filter** (Medium Priority)
**Problem**: Lines from the same receipt should have similar angles. If angles differ significantly, they might be from different receipts.

**Solution**: Calculate average angle per cluster and filter out lines with angles too different from the mean.

```python
def filter_lines_by_angle_consistency(
    cluster_dict: Dict[int, List[Line]],
    angle_tolerance: float = 5.0,  # degrees
) -> Dict[int, List[Line]]:
    """
    Filter out lines whose angle differs significantly from cluster mean.
    This helps separate receipts that are rotated differently.
    """
    filtered_clusters = {}

    for cluster_id, lines in cluster_dict.items():
        if len(lines) < 2:
            filtered_clusters[cluster_id] = lines
            continue

        # Calculate mean angle (handle wraparound at 180/-180)
        angles = [ln.angle_degrees for ln in lines]
        mean_angle = _circular_mean(angles)

        # Filter lines within tolerance
        filtered_lines = [
            ln for ln in lines
            if _angle_difference(ln.angle_degrees, mean_angle) <= angle_tolerance
        ]

        if len(filtered_lines) >= 2:
            filtered_clusters[cluster_id] = filtered_lines
        # If too many lines filtered, keep original (might be edge case)
        elif len(filtered_lines) >= len(lines) * 0.5:
            filtered_clusters[cluster_id] = filtered_lines

    return filtered_clusters

def _circular_mean(angles: List[float]) -> float:
    """Calculate mean of angles handling wraparound."""
    import numpy as np
    angles_rad = np.deg2rad(angles)
    mean_sin = np.mean(np.sin(angles_rad))
    mean_cos = np.mean(np.cos(angles_rad))
    return np.rad2deg(np.arctan2(mean_sin, mean_cos))

def _angle_difference(a1: float, a2: float) -> float:
    """Calculate smallest angle difference between two angles."""
    diff = abs(a1 - a2)
    return min(diff, 360 - diff)
```

**When to apply**: After initial clustering, before or after gap detection

---

### 3. **Aspect Ratio Validation** (Medium Priority)
**Problem**: Receipts typically have a portrait aspect ratio (height > width). Clusters that are too wide or too square might be incorrectly merged.

**Solution**: Calculate bounding box aspect ratio for each cluster and split if ratio is unusual.

```python
def split_clusters_by_aspect_ratio(
    cluster_dict: Dict[int, List[Line]],
    image_width: int,
    image_height: int,
    min_aspect_ratio: float = 1.2,  # height/width minimum
    max_aspect_ratio: float = 10.0,  # height/width maximum
) -> Dict[int, List[Line]]:
    """
    Split clusters whose bounding boxes have unusual aspect ratios.
    Receipts are typically portrait (height > width).
    """
    from receipt_upload.geometry import min_area_rect, box_points

    split_clusters = {}
    new_cluster_id = max(cluster_dict.keys()) + 1 if cluster_dict else 1

    for cluster_id, lines in cluster_dict.items():
        # Calculate cluster bounding box
        pts_abs = []
        for ln in lines:
            for corner in [ln.top_left, ln.top_right, ln.bottom_left, ln.bottom_right]:
                x_abs = corner["x"] * image_width
                y_abs = (1.0 - corner["y"]) * image_height
                pts_abs.append((x_abs, y_abs))

        if not pts_abs:
            continue

        (cx, cy), (rw, rh), angle_deg = min_area_rect(pts_abs)
        aspect_ratio = rh / rw if rw > 0 else 0

        # If aspect ratio is reasonable, keep cluster
        if min_aspect_ratio <= aspect_ratio <= max_aspect_ratio:
            split_clusters[cluster_id] = lines
        else:
            # Unusual aspect ratio - try to split by X-axis more aggressively
            # This is a fallback - might need manual review
            split_clusters[cluster_id] = lines  # Keep for now, flag for review

    return split_clusters
```

**When to apply**: After clustering, as a validation step

---

### 4. **Line Density Analysis** (Low Priority)
**Problem**: Receipts should have relatively uniform line density. Clusters with very sparse or very dense regions might be incorrectly merged.

**Solution**: Analyze line spacing within clusters and split if density varies significantly.

```python
def split_clusters_by_line_density(
    cluster_dict: Dict[int, List[Line]],
    image_height: int,
    density_variance_threshold: float = 0.3,
) -> Dict[int, List[Line]]:
    """
    Split clusters where line density varies significantly.
    This helps separate receipts that are close horizontally but have different line spacing.
    """
    import statistics

    split_clusters = {}
    new_cluster_id = max(cluster_dict.keys()) + 1 if cluster_dict else 1

    for cluster_id, lines in cluster_dict.items():
        if len(lines) < 4:
            split_clusters[cluster_id] = lines
            continue

        # Sort by Y coordinate
        lines_sorted = sorted(
            lines,
            key=lambda ln: ln.calculate_centroid()[1]
        )

        # Calculate spacing between consecutive lines
        spacings = []
        for i in range(1, len(lines_sorted)):
            prev_y = lines_sorted[i-1].calculate_centroid()[1]
            curr_y = lines_sorted[i].calculate_centroid()[1]
            spacing = (curr_y - prev_y) * image_height
            spacings.append(spacing)

        if not spacings:
            split_clusters[cluster_id] = lines
            continue

        # Calculate coefficient of variation (CV) of spacing
        mean_spacing = statistics.mean(spacings)
        if mean_spacing == 0:
            split_clusters[cluster_id] = lines
            continue

        std_spacing = statistics.stdev(spacings) if len(spacings) > 1 else 0
        cv = std_spacing / mean_spacing

        # If CV is high, spacing is inconsistent - might be multiple receipts
        if cv > density_variance_threshold:
            # Try to split at largest gaps
            # (Implementation similar to vertical gap detection)
            # For now, keep cluster but could be enhanced
            split_clusters[cluster_id] = lines
        else:
            split_clusters[cluster_id] = lines

    return split_clusters
```

---

### 5. **Confidence-Based Filtering** (Low Priority)
**Problem**: Low-confidence lines might be noise or from a different receipt.

**Solution**: Filter out lines with very low confidence that don't fit the cluster pattern.

```python
def filter_low_confidence_outliers(
    cluster_dict: Dict[int, List[Line]],
    confidence_threshold: float = 0.3,
    min_cluster_confidence: float = 0.5,
) -> Dict[int, List[Line]]:
    """
    Remove low-confidence lines that are outliers in their cluster.
    """
    filtered_clusters = {}

    for cluster_id, lines in cluster_dict.items():
        if len(lines) < 2:
            filtered_clusters[cluster_id] = lines
            continue

        # Calculate mean confidence
        mean_confidence = sum(ln.confidence for ln in lines) / len(lines)

        # Filter lines with very low confidence if cluster mean is high
        if mean_confidence >= min_cluster_confidence:
            filtered_lines = [
                ln for ln in lines
                if ln.confidence >= confidence_threshold
            ]
            if len(filtered_lines) >= 2:
                filtered_clusters[cluster_id] = filtered_lines
            else:
                filtered_clusters[cluster_id] = lines
        else:
            filtered_clusters[cluster_id] = lines

    return filtered_clusters
```

---

### 6. **Text-Based Heuristics** (Experimental)
**Problem**: Receipts often have distinctive text patterns (merchant names, totals, etc.) that can help identify boundaries.

**Solution**: Use text patterns to validate or split clusters.

```python
def validate_clusters_by_text_patterns(
    cluster_dict: Dict[int, List[Line]],
    words: List[Word],  # All words from image
) -> Dict[int, List[Line]]:
    """
    Validate clusters by checking for receipt-like text patterns.
    This is experimental and should be used carefully.
    """
    import re

    validated_clusters = {}

    # Patterns that suggest receipt boundaries
    total_pattern = re.compile(r'(?i)(total|subtotal|tax|amount due|balance)')
    merchant_pattern = re.compile(r'(?i)(receipt|invoice|bill)')

    for cluster_id, lines in cluster_dict.items():
        # Get all text from cluster
        cluster_text = " ".join(ln.text for ln in lines)

        # Check for multiple "total" patterns (might indicate multiple receipts)
        total_matches = len(total_pattern.findall(cluster_text))

        # If multiple totals found, might be multiple receipts
        # But this is heuristic - receipts can have multiple totals
        if total_matches > 3:  # Threshold needs tuning
            # Flag for review or try to split
            pass

        validated_clusters[cluster_id] = lines

    return validated_clusters
```

---

## Implementation Strategy

### Phase 1: Safe Additions (Low Risk)
1. **Vertical Gap Detection** - Most likely to help without breaking existing functionality
2. **Aspect Ratio Validation** - Can be used as a warning/flag system initially

### Phase 2: Moderate Risk
3. **Angle Consistency Filter** - Might filter out valid lines if receipts are rotated
4. **Line Density Analysis** - Needs careful threshold tuning

### Phase 3: Experimental
5. **Confidence-Based Filtering** - Could remove valid low-confidence lines
6. **Text-Based Heuristics** - Most experimental, needs validation

## Recommended Approach

### For the Two Problem Images

1. **Add Vertical Gap Detection** as a post-processing step:
   ```python
   # In process_scan():
   cluster_dict = dbscan_lines_x_axis(ocr_lines)

   # NEW: Split by vertical gaps
   cluster_dict = split_clusters_by_vertical_gaps(
       cluster_dict,
       image.height,
       gap_threshold=0.12  # 12% of image height
   )

   # Existing: Join overlapping clusters
   cluster_dict = join_overlapping_clusters(
       cluster_dict, image.width, image.height, iou_threshold=0.01
   )
   ```

2. **Add Angle Consistency Check** as optional refinement:
   ```python
   # After gap detection, before join_overlapping
   cluster_dict = filter_lines_by_angle_consistency(
       cluster_dict,
       angle_tolerance=3.0  # 3 degrees tolerance
   )
   ```

3. **Add Aspect Ratio Validation** as a warning system:
   ```python
   # After all clustering, validate results
   for cluster_id, lines in cluster_dict.items():
       # Calculate aspect ratio
       # Log warning if unusual
       # Could trigger manual review
   ```

## Testing Strategy

1. **Test on known good images** (500+ working images) - ensure no regressions
2. **Test on the two problem images** - verify fixes
3. **Test on edge cases**:
   - Receipts with large blank spaces
   - Receipts with different rotations
   - Receipts side-by-side
   - Receipts with unusual aspect ratios

## Configuration

Make all new techniques configurable with sensible defaults:

```python
class ClusteringConfig:
    # Vertical gap detection
    enable_vertical_gap_detection: bool = True
    vertical_gap_threshold: float = 0.12  # 12% of image height

    # Angle consistency
    enable_angle_filtering: bool = False  # Disabled by default
    angle_tolerance: float = 5.0

    # Aspect ratio validation
    enable_aspect_ratio_validation: bool = True
    min_aspect_ratio: float = 1.2
    max_aspect_ratio: float = 10.0

    # Line density
    enable_density_analysis: bool = False
    density_variance_threshold: float = 0.3
```

## Next Steps

1. Implement **Vertical Gap Detection** first (highest impact, lowest risk)
2. Test on the two problem images
3. If successful, add **Angle Consistency Filter** as optional
4. Monitor results and adjust thresholds
5. Consider adding other techniques based on results


