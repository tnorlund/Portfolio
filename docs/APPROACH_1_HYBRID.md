# Approach 1: Hybrid - Simple Top/Bottom + Old Left/Right

## Summary

Keep the simplified top/bottom edge detection (using top/bottom line corners) but restore the old angled left/right edge detection to handle tilted receipts.

## Current Problem

The simplified approach uses `min(hull_x)` and `max(hull_x)` for left/right edges, creating **vertical boundaries**. For tilted receipts, this cuts off corners or includes extra background.

## Proposed Solution

**Top/Bottom edges**: Keep the simple approach
- Sort lines by Y position
- Use top line's TL/TR corners for top edge
- Use bottom line's BL/BR corners for bottom edge

**Left/Right edges**: Restore the old complex approach
- Use `find_hull_extremes_along_angle()` to find extreme points along receipt tilt
- Use `refine_hull_extremes_with_hull_edge_alignment()` to get boundary segments
- Use `create_boundary_line_from_points()` to create angled boundary lines
- Intersect with top/bottom to get final corners

## Implementation

### File to Modify
`receipt_upload/receipt_upload/receipt_processing/photo.py`

### Current Code (lines 184-230)
```python
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
    width=image.width, height=image.height, flip_y=True
)
bottom_line_corners = bottom_line.calculate_corners(
    width=image.width, height=image.height, flip_y=True
)

# Use hull to constrain left/right edges (PROBLEM: creates vertical edges)
hull_xs = [p[0] for p in hull]
min_hull_x = min(hull_xs)
max_hull_x = max(hull_xs)

# Receipt corners using vertical constraints
top_left = (max(min_hull_x, top_line_corners[0][0]), top_line_corners[0][1])
top_right = (min(max_hull_x, top_line_corners[1][0]), top_line_corners[1][1])
bottom_left = (max(min_hull_x, bottom_line_corners[2][0]), bottom_line_corners[2][1])
bottom_right = (min(max_hull_x, bottom_line_corners[3][0]), bottom_line_corners[3][1])
```

### Proposed Code
```python
from receipt_upload.geometry import (
    compute_hull_centroid,
    compute_final_receipt_tilt,
    find_hull_extremes_along_angle,
    refine_hull_extremes_with_hull_edge_alignment,
    create_boundary_line_from_points,
    create_horizontal_boundary_line_from_points,
)

# Find top and bottom lines by Y position (KEEP THIS)
sorted_lines = sorted(
    cluster_lines,
    key=lambda line: line.top_left["y"],
    reverse=True,
)
top_line = sorted_lines[0]
bottom_line = sorted_lines[-1]

# Get corners from top and bottom lines (KEEP THIS)
top_line_corners = top_line.calculate_corners(
    width=image.width, height=image.height, flip_y=True
)
bottom_line_corners = bottom_line.calculate_corners(
    width=image.width, height=image.height, flip_y=True
)

# Create top/bottom boundary lines from line corners
top_boundary = create_horizontal_boundary_line_from_points([
    top_line_corners[0],  # TL
    top_line_corners[1],  # TR
])
bottom_boundary = create_horizontal_boundary_line_from_points([
    bottom_line_corners[2],  # BL
    bottom_line_corners[3],  # BR
])

# Compute hull centroid and receipt tilt angle (RESTORE FROM OLD)
centroid = compute_hull_centroid(hull)
angles = [l.angle_degrees for l in cluster_lines if l.angle_degrees != 0]
avg_angle = sum(angles) / len(angles) if angles else 0.0
final_angle = compute_final_receipt_tilt(cluster_lines, hull, centroid, avg_angle)

# Find left/right extreme points along tilt angle (RESTORE FROM OLD)
extremes = find_hull_extremes_along_angle(hull, centroid, final_angle)
left_extreme = extremes["leftPoint"]
right_extreme = extremes["rightPoint"]

# Refine with hull edge alignment (RESTORE FROM OLD)
refined = refine_hull_extremes_with_hull_edge_alignment(
    hull, left_extreme, right_extreme, final_angle
)

# Create angled left/right boundary lines (RESTORE FROM OLD)
left_boundary = create_boundary_line_from_points(
    refined["leftSegment"]["extreme"],
    refined["leftSegment"]["optimizedNeighbor"],
)
right_boundary = create_boundary_line_from_points(
    refined["rightSegment"]["extreme"],
    refined["rightSegment"]["optimizedNeighbor"],
)

# Intersect boundaries to get final corners (NEW - using helper)
from receipt_upload.geometry.edge_detection import _find_line_intersection

top_left = _find_line_intersection(top_boundary, left_boundary, centroid)
top_right = _find_line_intersection(top_boundary, right_boundary, centroid)
bottom_left = _find_line_intersection(bottom_boundary, left_boundary, centroid)
bottom_right = _find_line_intersection(bottom_boundary, right_boundary, centroid)

receipt_box_corners = [top_left, top_right, bottom_right, bottom_left]
```

## Imports to Add

```python
from receipt_upload.geometry import (
    compute_hull_centroid,
    compute_final_receipt_tilt,
    find_hull_extremes_along_angle,
    refine_hull_extremes_with_hull_edge_alignment,
    create_boundary_line_from_points,
    create_horizontal_boundary_line_from_points,
)
```

## Testing

1. Run the comparison script:
```bash
source .venv312/bin/activate
python scripts/compare_perspective_transforms.py --stack dev
```

2. Run the visualization:
```bash
python scripts/visualize_perspective_transforms.py --stack dev --output-dir viz_output
```

3. Focus on the two problematic images:
- `2c453d65-edae-4aeb-819a-612b27d99894`
- `8362b52a-60a1-4534-857f-028dd531976e`

## Expected Outcome

- Top/bottom edges: Simple, based on top/bottom line positions
- Left/right edges: Angled, following receipt tilt
- Should match stored corners more closely for tilted receipts
- Reduces complexity vs full old approach (no Theil-Sen for top/bottom)

## Complexity Comparison

| Component | Old Approach | This Hybrid | Simplified |
|-----------|--------------|-------------|------------|
| Top/Bottom | Theil-Sen + edge detection | Line corners | Line corners |
| Left/Right | Hull extremes + refinement | Hull extremes + refinement | min/max X |
| Functions called | ~12 | ~8 | ~3 |

## Risks

- Line intersection may fail for degenerate cases (parallel lines)
- Need to handle fallback to simple bounds if intersection fails
