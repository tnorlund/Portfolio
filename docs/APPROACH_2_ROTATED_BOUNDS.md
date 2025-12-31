# Approach 2: Rotated Bounding Box from Top/Bottom Lines

## Summary

Instead of using complex hull analysis for left/right edges, derive the receipt's rotation from the top and bottom lines, then compute a rotated bounding box that naturally handles tilt.

## Current Problem

The simplified approach uses axis-aligned min/max X for left/right edges. This doesn't work for tilted receipts.

## Proposed Solution

1. Use top line's corners to establish the **top edge angle**
2. Use bottom line's corners to establish the **bottom edge angle**
3. Average these angles to get the **receipt tilt**
4. Create left/right edges **perpendicular to the average tilt**
5. Extend left/right edges to intersect with top/bottom edges

This is geometrically simpler than the old approach while still handling tilt.

## Implementation

### File to Modify
`receipt_upload/receipt_upload/receipt_processing/photo.py`

### Proposed Code
```python
import math

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

# Extract key points
top_left_pt = top_line_corners[0]   # (x, y)
top_right_pt = top_line_corners[1]  # (x, y)
bottom_left_pt = bottom_line_corners[2]   # (x, y)
bottom_right_pt = bottom_line_corners[3]  # (x, y)

# Compute angle of top edge
top_dx = top_right_pt[0] - top_left_pt[0]
top_dy = top_right_pt[1] - top_left_pt[1]
top_angle = math.atan2(top_dy, top_dx)

# Compute angle of bottom edge
bottom_dx = bottom_right_pt[0] - bottom_left_pt[0]
bottom_dy = bottom_right_pt[1] - bottom_left_pt[1]
bottom_angle = math.atan2(bottom_dy, bottom_dx)

# Average angle (receipt tilt)
avg_angle = (top_angle + bottom_angle) / 2.0

# Left edge direction: perpendicular to horizontal edges, pointing down
# Perpendicular angle = avg_angle + 90 degrees
left_edge_angle = avg_angle + math.pi / 2

# Unit vector for left edge direction
left_dx = math.cos(left_edge_angle)
left_dy = math.sin(left_edge_angle)

# Find leftmost and rightmost X positions from hull
hull_xs = [p[0] for p in hull]
min_hull_x = min(hull_xs)
max_hull_x = max(hull_xs)

# Project top-left and bottom-left along left edge direction
# Start from top line's left corner, project down along left edge
def line_intersection(p1, d1, p2, d2):
    """Find intersection of two lines defined by point + direction."""
    # Line 1: p1 + t * d1
    # Line 2: p2 + s * d2
    # Solve: p1 + t*d1 = p2 + s*d2
    cross = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(cross) < 1e-9:
        return None  # Parallel
    dp = (p2[0] - p1[0], p2[1] - p1[1])
    t = (dp[0] * d2[1] - dp[1] * d2[0]) / cross
    return (p1[0] + t * d1[0], p1[1] + t * d1[1])

# Top edge direction
top_dir = (top_dx, top_dy)
# Bottom edge direction
bottom_dir = (bottom_dx, bottom_dy)
# Left edge direction (perpendicular, pointing down)
left_dir = (left_dx, left_dy)
# Right edge direction (same as left, just different start point)
right_dir = (left_dx, left_dy)

# Find the leftmost point on hull projected onto left edge direction
# Use hull centroid as reference
centroid = (
    sum(p[0] for p in hull) / len(hull),
    sum(p[1] for p in hull) / len(hull),
)

# Project hull points onto perpendicular axis to find left/right extremes
perp_projections = []
for p in hull:
    # Project (p - centroid) onto left_dir
    rel = (p[0] - centroid[0], p[1] - centroid[1])
    # Perpendicular to left_dir is the horizontal axis direction
    horiz_dir = (math.cos(avg_angle), math.sin(avg_angle))
    proj = rel[0] * horiz_dir[0] + rel[1] * horiz_dir[1]
    perp_projections.append((proj, p))

perp_projections.sort(key=lambda x: x[0])
leftmost_hull_pt = perp_projections[0][1]
rightmost_hull_pt = perp_projections[-1][1]

# Create left edge line through leftmost hull point
# Create right edge line through rightmost hull point

# Final corners: intersect edges
top_left = line_intersection(top_left_pt, top_dir, leftmost_hull_pt, left_dir)
top_right = line_intersection(top_left_pt, top_dir, rightmost_hull_pt, right_dir)
bottom_left = line_intersection(bottom_left_pt, bottom_dir, leftmost_hull_pt, left_dir)
bottom_right = line_intersection(bottom_left_pt, bottom_dir, rightmost_hull_pt, right_dir)

# Handle intersection failures
if any(p is None for p in [top_left, top_right, bottom_left, bottom_right]):
    # Fallback to simple axis-aligned bounds
    top_left = (min_hull_x, top_left_pt[1])
    top_right = (max_hull_x, top_right_pt[1])
    bottom_left = (min_hull_x, bottom_left_pt[1])
    bottom_right = (max_hull_x, bottom_right_pt[1])

receipt_box_corners = [top_left, top_right, bottom_right, bottom_left]
```

## Alternative: Use `min_area_rect` from geometry

The `min_area_rect` function already computes a minimum-area rotated bounding box:

```python
from receipt_upload.geometry import min_area_rect, box_points

# Get minimum area rotated rectangle
center, (width, height), angle_deg = min_area_rect(all_word_corners)

# Ensure portrait orientation (height > width)
if width > height:
    width, height = height, width
    angle_deg -= 90

# Get corners
receipt_box_corners = box_points(center, (width, height), angle_deg)

# Reorder to [TL, TR, BR, BL]
# box_points returns corners in order, but may need reordering based on angle
```

This is the **simplest approach** but may not align perfectly with text lines.

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

- All four edges are derived from line geometry
- Left/right edges are perpendicular to averaged top/bottom edge angle
- Naturally handles tilted receipts
- No need for complex hull extremes analysis

## Complexity Comparison

| Component | Old Approach | Approach 1 (Hybrid) | This Approach |
|-----------|--------------|---------------------|---------------|
| Top/Bottom | Theil-Sen | Line corners | Line corners |
| Left/Right | Hull extremes + refinement | Hull extremes + refinement | Perpendicular projection |
| Functions called | ~12 | ~8 | ~4 |
| External deps | Many geometry functions | Many geometry functions | Just math |

## Pros

- Conceptually simpler: edges derived from line geometry
- No dependency on complex hull refinement
- Easier to port to Swift for Phase 3
- Self-contained math (no external function calls)

## Cons

- May not perfectly align with hull extremes in some cases
- Perpendicular assumption may not hold for very skewed receipts
- Intersection math needs careful handling of edge cases

## Risks

- Line intersection failures (parallel lines)
- Very tilted receipts where top/bottom angles differ significantly
- Hull points may extend beyond the computed edges
