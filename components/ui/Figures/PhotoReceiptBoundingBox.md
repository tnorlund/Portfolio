## PhotoReceiptBoundingBox Algorithm

This document outlines the step-by-step process used to detect and draw the receipt boundaries from OCR line data.

1. **Collect All Corner Points**  
   Extract the four corner points of each OCR line bounding box.

2. **Compute the Convex Hull**  
   Use a Graham-scan algorithm to find the minimal convex polygon enclosing all corners.

3. **Compute Hull Centroid**  
   Calculate the average of all hull vertices to find the polygon's center point.

4. **Estimate Initial Skew from OCR**  
   Calculate each line's bottom-edge angle:

   ```
   angle = atan2(dy, dx) * 180 / Math.PI
   ```

   Filter out near-zero angles and average the rest to get a preliminary tilt.

5. **Find Top and Bottom Boundary Candidates**

   - Project hull vertices onto the axis perpendicular to the preliminary tilt.
   - Sort by projection and take the two smallest and two largest points as bottom and top extremes.

6. **Compute Final Receipt Tilt**  
   Fit lines through those top and bottom extremes, compute their angles, and average them.

7. **Find Left and Right Extremes Along Receipt Tilt**  
   Project hull vertices onto the axis defined by the final receipt tilt.  
   The minimum and maximum projections yield the raw left and right extreme vertices.

8. **Refine with Hull Edge Alignment (CW/CCW Neighbor Comparison)**  
   For each extreme vertex, compare the two adjacent hull neighbors (clockwise vs. counter‑clockwise) using Hull Edge Alignment scoring:

   ```
   alignment_score = (edge1_alignment + edge2_alignment) * 0.7 + target_alignment * 0.3
   ```

   Where:

   - `edge1_alignment` = how parallel the line is to the incoming hull edge
   - `edge2_alignment` = how parallel the line is to the outgoing hull edge
   - `target_alignment` = how aligned the line is with the final receipt tilt

   This approach ensures boundary lines "hug" the hull contour naturally, choosing the neighbor that creates the most parallel line to adjacent hull edges.

9. **Compute Final Receipt Quadrilateral**
   Intersect the refined left and right boundary lines (from step 8) with the top and bottom edges (from step 5) to create the final receipt bounding box:

   - Use the optimal left and right segments determined by Hull Edge Alignment
   - Convert segments to line equations (handling vertical lines as special cases)
   - Find intersections with top and bottom edge lines using Theil-Sen fitted slopes
   - Return the four corner points in clockwise order: top-left, top-right, bottom-right, bottom-left

   This represents the culmination of the multi-step algorithm, producing a receipt boundary that combines:

   - Robust top/bottom edge fitting from OCR text lines
   - Optimal left/right boundaries that align with hull geometry
   - Mathematical precision through line intersection calculations

---

### Alternative Approaches

_(List the other approaches here if desired)_

### Test Coverage

The accompanying `receipt.fixture.test.ts` exercises the full pipeline with OCR
fixtures. It verifies hull extremes, centroid, and the final bounding box while
ensuring Hull Edge Alignment picks the correct clockwise or counter‑clockwise
neighbor.

React tests (for example `PhotoReceiptBoundingBox.test.tsx`) mount the component
with mocked animations. They confirm that each animated overlay receives the
calculated props such as hull points, angles, and delay values.
