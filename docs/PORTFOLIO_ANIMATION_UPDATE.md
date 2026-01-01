# Portfolio Animation Update: Simplified Perspective Transform

## Goal

Update the portfolio website's receipt OCR animation to match the new simplified perspective transform algorithm. The old approach used a complex 9-step geometry pipeline (~800 lines). The new approach is **~70 lines** and produces better results for tilted receipts.

## Algorithm Summary

```
Input: OCR lines and words from a receipt image

1. Compute convex hull of all word corners
2. Sort lines by Y position → get top line and bottom line
3. Get corner points from top/bottom lines
4. Compute top_angle and bottom_angle from line edges
5. avg_angle = circular_mean(top_angle, bottom_angle)
6. side_direction = perpendicular to avg_angle
7. Project hull points along avg_angle → find left/right extremes
8. Intersect 4 lines to get final corners:
   - Top edge (top line's angle) × Left perpendicular
   - Top edge (top line's angle) × Right perpendicular
   - Bottom edge (bottom line's angle) × Left perpendicular
   - Bottom edge (bottom line's angle) × Right perpendicular

Output: 4 corner points [TL, TR, BR, BL]
```

---

## Reference Implementation (Python)

**File:** `receipt_upload/receipt_upload/geometry/utils.py`

### Core Functions

```python
def circular_mean_angle(angle1: float, angle2: float) -> float:
    """Average two angles, handling ±180° wraparound."""
    sin_sum = math.sin(angle1) + math.sin(angle2)
    cos_sum = math.cos(angle1) + math.cos(angle2)
    return math.atan2(sin_sum, cos_sum)


def line_intersection(p1, d1, p2, d2) -> Optional[Tuple[float, float]]:
    """Find intersection of two lines defined by point + direction."""
    cross = d1[0] * d2[1] - d1[1] * d2[0]
    if abs(cross) < 1e-9:
        return None  # Parallel
    dp = (p2[0] - p1[0], p2[1] - p1[1])
    t = (dp[0] * d2[1] - dp[1] * d2[0]) / cross
    return (p1[0] + t * d1[0], p1[1] + t * d1[1])


def _find_hull_extremes(hull, angle_rad):
    """Find leftmost and rightmost hull points projected along an angle."""
    centroid = (sum(p[0] for p in hull) / len(hull),
                sum(p[1] for p in hull) / len(hull))
    cos_a, sin_a = math.cos(angle_rad), math.sin(angle_rad)

    min_proj, max_proj = float("inf"), float("-inf")
    left_pt, right_pt = hull[0], hull[0]

    for p in hull:
        rel = (p[0] - centroid[0], p[1] - centroid[1])
        proj = rel[0] * cos_a + rel[1] * sin_a
        if proj < min_proj:
            min_proj, left_pt = proj, p
        if proj > max_proj:
            max_proj, right_pt = proj, p

    return left_pt, right_pt
```

### Main Algorithm

```python
def compute_rotated_bounding_box_corners(hull, top_line_corners, bottom_line_corners):
    # Extract key points
    top_left_pt = top_line_corners[0]      # TL of top line
    top_right_pt = top_line_corners[1]     # TR of top line
    bottom_left_pt = bottom_line_corners[2]  # BL of bottom line
    bottom_right_pt = bottom_line_corners[3] # BR of bottom line

    # Compute edge angles
    top_angle = math.atan2(top_right_pt[1] - top_left_pt[1],
                          top_right_pt[0] - top_left_pt[0])
    bottom_angle = math.atan2(bottom_right_pt[1] - bottom_left_pt[1],
                             bottom_right_pt[0] - bottom_left_pt[0])

    # Edge direction vectors
    top_dir = (top_right_pt[0] - top_left_pt[0],
               top_right_pt[1] - top_left_pt[1])
    bottom_dir = (bottom_right_pt[0] - bottom_left_pt[0],
                  bottom_right_pt[1] - bottom_left_pt[1])

    # Average angle and perpendicular direction
    avg_angle = circular_mean_angle(top_angle, bottom_angle)
    side_angle = avg_angle + math.pi / 2
    side_dir = (math.cos(side_angle), math.sin(side_angle))

    # Find hull extremes
    left_extreme, right_extreme = _find_hull_extremes(hull, avg_angle)

    # Intersect to get corners
    top_left = line_intersection(top_left_pt, top_dir, left_extreme, side_dir)
    top_right = line_intersection(top_left_pt, top_dir, right_extreme, side_dir)
    bottom_left = line_intersection(bottom_left_pt, bottom_dir, left_extreme, side_dir)
    bottom_right = line_intersection(bottom_left_pt, bottom_dir, right_extreme, side_dir)

    return [top_left, top_right, bottom_right, bottom_left]
```

---

## New Animation Flow (5 Steps)

### Step 1: Convex Hull
- Show all word bounding boxes
- Animate convex hull forming around them
- Draw hull as red polygon outline

### Step 2: Top/Bottom Line Selection
- Highlight all text lines
- Sort by Y position (animate if desired)
- Highlight **top line** (green) - highest Y in OCR coords
- Highlight **bottom line** (yellow) - lowest Y in OCR coords
- Show corner points: TL, TR from top line; BL, BR from bottom line

### Step 3: Edge Angles
- Draw top edge vector (TL → TR of top line)
- Draw bottom edge vector (BL → BR of bottom line)
- Show both angles (θ_top, θ_bottom)
- Animate computing average: `avg_angle = circular_mean(θ_top, θ_bottom)`
- Show perpendicular direction: `side_angle = avg_angle + 90°`

### Step 4: Hull Projection
- Draw axis line through hull centroid at `avg_angle`
- Project all hull points onto this axis
- Highlight leftmost and rightmost projected points
- Draw perpendicular lines through these extremes

### Step 5: Final Corners
- Show 4 infinite lines:
  - Top edge through top line's TL (at top_angle)
  - Bottom edge through bottom line's BL (at bottom_angle)
  - Left edge through left extreme (at side_angle)
  - Right edge through right extreme (at side_angle)
- Animate intersections → 4 corner points appear
- Connect corners to form final quadrilateral

---

## TypeScript Implementation

### Functions to Add

```typescript
function circularMeanAngle(angle1: number, angle2: number): number {
  const sinSum = Math.sin(angle1) + Math.sin(angle2);
  const cosSum = Math.cos(angle1) + Math.cos(angle2);
  return Math.atan2(sinSum, cosSum);
}

function lineIntersection(
  p1: Point, d1: Point,
  p2: Point, d2: Point
): Point | null {
  const cross = d1.x * d2.y - d1.y * d2.x;
  if (Math.abs(cross) < 1e-9) return null;
  const dp = { x: p2.x - p1.x, y: p2.y - p1.y };
  const t = (dp.x * d2.y - dp.y * d2.x) / cross;
  return { x: p1.x + t * d1.x, y: p1.y + t * d1.y };
}

function findHullExtremes(hull: Point[], angle: number): [Point, Point] {
  const cx = hull.reduce((s, p) => s + p.x, 0) / hull.length;
  const cy = hull.reduce((s, p) => s + p.y, 0) / hull.length;
  const cos = Math.cos(angle), sin = Math.sin(angle);

  let minProj = Infinity, maxProj = -Infinity;
  let left = hull[0], right = hull[0];

  for (const p of hull) {
    const proj = (p.x - cx) * cos + (p.y - cy) * sin;
    if (proj < minProj) { minProj = proj; left = p; }
    if (proj > maxProj) { maxProj = proj; right = p; }
  }
  return [left, right];
}
```

### Files to Modify

| File | Action |
|------|--------|
| `portfolio/utils/receipt/boundingBox.ts` | **Delete ~700 lines**, replace with ~50 lines above |
| `portfolio/utils/geometry/receipt.ts` | **Delete most functions** |
| `portfolio/hooks/useReceiptGeometry.ts` | Simplify to use new functions |
| `portfolio/components/ui/Figures/PhotoReceiptBoundingBox.tsx` | Reduce from 9 steps to 5 |

### Animation Components

| Current | Action |
|---------|--------|
| `AnimatedConvexHull.tsx` | KEEP |
| `AnimatedHullCentroid.tsx` | KEEP (used for projection reference) |
| `AnimatedOrientedAxes.tsx` | SIMPLIFY (single axis at avg_angle) |
| `AnimatedTopAndBottom.tsx` | REWRITE (show top/bottom line selection) |
| `AnimatedHullEdgeAlignment.tsx` | DELETE (was 388 lines of CW/CCW neighbor scoring) |
| `AnimatedFinalReceiptBox.tsx` | KEEP (similar logic) |

---

## Key Insight

The old approach tried to find "true perspective" for all 4 edges using complex hull neighbor analysis. The new approach recognizes:

1. **Top/bottom edges**: We have good data from text lines → use their natural angles
2. **Left/right edges**: We don't have reliable data → use perpendicular to average (good approximation)

This creates a quadrilateral where:
- Top and bottom edges can have **different angles** (perspective from text)
- Left and right edges are **parallel** (perpendicular to average)

For most receipt photos, this produces excellent results with 1/10th the code.

---

## Testing

Verify against these two images that had invalid corners with the old approach:
- `2c453d65-edae-4aeb-819a-612b27d99894` (tilted right)
- `8362b52a-60a1-4534-857f-028dd531976e` (tilted left)

Use: `python scripts/visualize_perspective_transforms.py --stack dev --image-id <id>`
