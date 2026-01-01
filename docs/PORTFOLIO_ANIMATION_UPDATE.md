# Portfolio Animation Update: Simplified Perspective Transform

## Goal

Update the portfolio website's receipt OCR animation to match the new simplified perspective transform algorithm. The old approach used a complex 9-step geometry pipeline (~800 lines). The new approach is much simpler (~50 lines) and produces better results for tilted receipts.

## Overview of Changes

### Old Approach (Current Animation)
1. Compute convex hull of all word corners
2. Find hull centroid
3. Estimate receipt tilt from line angles
4. Project hull onto primary axis → find left/right extreme points
5. Project hull onto secondary axis → find top/bottom boundary points
6. Compute final receipt tilt using `computeFinalReceiptTilt()`
7. Find hull extremes along final tilt angle
8. Refine extremes with CW/CCW neighbor scoring (388 lines of complex logic)
9. Intersect 4 boundary lines to get final corners

### New Approach (To Implement)
1. Compute convex hull of all word corners
2. Sort lines by Y position → top line (highest Y) and bottom line (lowest Y)
3. Get corners directly from top/bottom lines
4. Compute average edge angle using circular mean
5. Use hull min/max X as left/right constraints
6. Compute final corners via line intersection with perpendicular edges

---

## Reference Implementations

### Python (New - Source of Truth)

**Main processing function:**
- File: `receipt_upload/receipt_upload/receipt_processing/photo.py`
- Look for the section that calls `compute_rotated_bounding_box_corners()`

**Core algorithm:**
- File: `receipt_upload/receipt_upload/geometry/utils.py`
- Function: `compute_rotated_bounding_box_corners(hull, top_line_corners, bottom_line_corners)`
- Function: `circular_mean_angle(angle1, angle2)` - handles ±180° wraparound
- Function: `line_intersection(p1, d1, p2, d2)` - intersect two lines

**Key logic in `compute_rotated_bounding_box_corners()`:**
```python
# 1. Extract corners from top/bottom lines
top_left_pt = top_line_corners[0]   # TL
top_right_pt = top_line_corners[1]  # TR
bottom_left_pt = bottom_line_corners[2]   # BL
bottom_right_pt = bottom_line_corners[3]  # BR

# 2. Compute edge angles
top_angle = atan2(top_right_pt[1] - top_left_pt[1], top_right_pt[0] - top_left_pt[0])
bottom_angle = atan2(bottom_right_pt[1] - bottom_left_pt[1], bottom_right_pt[0] - bottom_left_pt[0])

# 3. Average angle using circular mean (handles ±180° correctly)
avg_angle = circular_mean_angle(top_angle, bottom_angle)

# 4. Left edge is perpendicular to horizontal edges
left_edge_angle = avg_angle + π/2

# 5. Project hull points onto horizontal axis to find left/right extremes
# (relative to centroid, along avg_angle direction)

# 6. Intersect edges to get final corners
top_left = line_intersection(top_left_pt, top_dir, leftmost_hull_pt, left_dir)
# ... etc for other 3 corners
```

**Visualization script (shows both old and new):**
- File: `scripts/visualize_perspective_transforms.py`
- Function: `compute_simplified_corners()` - standalone implementation of new approach

---

### TypeScript (Old - Needs Updating)

**Animation orchestrator:**
- File: `portfolio/components/ui/Figures/PhotoReceiptBoundingBox.tsx`
- Renders 9 sequential animation steps

**Geometry hook (computes data for animations):**
- File: `portfolio/hooks/useReceiptGeometry.ts`
- Calls all the complex geometry functions
- **This needs major restructuring**

**Complex geometry functions to REMOVE/SIMPLIFY:**
- File: `portfolio/utils/receipt/boundingBox.ts` (802 lines)
  - `findLineEdgesAtSecondaryExtremes()` → REMOVE (use sorted line corners)
  - `refineHullExtremesWithHullEdgeAlignment()` → REMOVE (388 lines of neighbor scoring)
  - `findHullExtremesAlongAngle()` → SIMPLIFY (use hull projection along avg_angle)
  - `computeReceiptBoxFromBoundaries()` → KEEP (intersects 4 boundary lines)
  - `createBoundaryLineFromPoints()` → KEEP

- File: `portfolio/utils/geometry/receipt.ts` (303 lines)
  - `computeFinalReceiptTilt()` → REMOVE (no longer needed)
  - `computeHullEdge()` → REMOVE
  - `computeEdge()` → REMOVE
  - `findLineEdgesAtPrimaryExtremes()` → REMOVE

**Animation components to update:**

| File | Current Purpose | New Purpose |
|------|-----------------|-------------|
| `AnimatedConvexHull.tsx` | Draw hull | KEEP AS-IS |
| `AnimatedHullCentroid.tsx` | Show centroid | KEEP (still used for projection reference) |
| `AnimatedOrientedAxes.tsx` | Show primary/secondary axes | UPDATE: Show single avg_angle axis |
| `AnimatedTopAndBottom.tsx` | Show secondary projection | UPDATE: Show top/bottom line selection |
| `AnimatedHullEdgeAlignment.tsx` | Show CW/CCW neighbor scoring | REMOVE or UPDATE: Show hull projection for L/R |
| `AnimatedFinalReceiptBox.tsx` | Show line intersections | KEEP (logic similar) |

---

## New Animation Flow (Suggested)

1. **Step 1-2: Convex Hull** (unchanged)
   - Animate hull vertices appearing
   - Connect with red polygon outline

2. **Step 3: Top/Bottom Line Selection** (NEW - replaces complex projection)
   - Highlight all lines
   - Sort by Y position (animate sorting?)
   - Highlight top line (green) and bottom line (yellow)
   - Show their corner points: TL, TR for top; BL, BR for bottom

3. **Step 4: Edge Angle Computation** (simplified)
   - Draw top edge vector (from TL to TR)
   - Draw bottom edge vector (from BL to BR)
   - Show both angles
   - Animate averaging to get `avg_angle`

4. **Step 5: Left/Right Edge Direction** (NEW)
   - Show perpendicular direction (avg_angle + 90°)
   - This is the direction of left/right edges

5. **Step 6: Hull Projection** (simplified from old step 7-8)
   - Project hull points onto horizontal axis (along avg_angle)
   - Find leftmost and rightmost hull points
   - Draw vertical lines at these extremes (in direction of left_edge_angle)

6. **Step 7: Final Corner Intersection** (similar to old step 9)
   - Top edge line through top line's TL-TR
   - Bottom edge line through bottom line's BL-BR
   - Left edge line through leftmost hull point (perpendicular)
   - Right edge line through rightmost hull point (perpendicular)
   - Animate intersections → 4 corner points
   - Draw final quadrilateral

---

## Key Functions to Add (TypeScript)

```typescript
/**
 * Circular mean of two angles (handles ±π wraparound)
 */
function circularMeanAngle(angle1: number, angle2: number): number {
  const sinSum = Math.sin(angle1) + Math.sin(angle2);
  const cosSum = Math.cos(angle1) + Math.cos(angle2);
  return Math.atan2(sinSum, cosSum);
}

/**
 * Intersect two lines defined by point + direction
 */
function lineIntersection(
  p1: Point, d1: Point,
  p2: Point, d2: Point
): Point | null {
  const cross = d1.x * d2.y - d1.y * d2.x;
  if (Math.abs(cross) < 1e-9) return null; // parallel
  const dp = { x: p2.x - p1.x, y: p2.y - p1.y };
  const t = (dp.x * d2.y - dp.y * d2.x) / cross;
  return { x: p1.x + t * d1.x, y: p1.y + t * d1.y };
}

/**
 * Compute receipt corners using rotated bounding box approach
 */
function computeRotatedBoundingBoxCorners(
  hull: Point[],
  topLineCorners: [Point, Point, Point, Point],  // [TL, TR, BL, BR]
  bottomLineCorners: [Point, Point, Point, Point]
): [Point, Point, Point, Point] {
  // See Python implementation for full logic
}
```

---

## Testing

After updating, verify against:
- `scripts/visualize_perspective_transforms.py` - generates comparison images
- Two problematic images that motivated this change:
  - `2c453d65-edae-4aeb-819a-612b27d99894`
  - `8362b52a-60a1-4534-857f-028dd531976e`

The animation should now show:
1. Simple top/bottom line selection (not complex projection)
2. Direct use of line corners (not secondary axis extremes)
3. Single perpendicular direction for left/right edges (not CW/CCW neighbor scoring)
4. Much less visual complexity overall

---

## Files Summary

### Python (Reference - Don't Modify)
- `receipt_upload/receipt_upload/geometry/utils.py` - Core algorithm
- `receipt_upload/receipt_upload/receipt_processing/photo.py` - Usage context
- `scripts/visualize_perspective_transforms.py` - Visualization

### TypeScript (To Update)
- `portfolio/hooks/useReceiptGeometry.ts` - **Major restructure**
- `portfolio/utils/receipt/boundingBox.ts` - **Remove ~600 lines**
- `portfolio/utils/geometry/receipt.ts` - **Remove most functions**
- `portfolio/components/ui/animations/*.tsx` - **Update 4-5 components**
- `portfolio/components/ui/Figures/PhotoReceiptBoundingBox.tsx` - **Update step flow**
