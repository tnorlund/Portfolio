## PhotoReceiptBoundingBox Algorithm

This document outlines the step-by-step process used to detect and draw the receipt boundaries from OCR line data.

1. **Collect All Corner Points**  
   Extract the four corner points of each OCR line bounding box.

2. **Compute the Convex Hull**  
   Use a Graham-scan algorithm to find the minimal convex polygon enclosing all corners.

3. **Compute Hull Centroid**  
   Calculate the average of all hull vertices to find the polygon’s center point.

4. **Estimate Initial Skew from OCR**  
   Calculate each line’s bottom-edge angle:

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

8. **Refine with CW/CCW Neighbor Comparison**  
   For each extreme vertex, compare the two adjacent hull neighbors (clockwise vs. counter‑clockwise) by a weighted cost:

   ```
   cost = w_distance * mean_perpendicular_distance_to_hull
        + w_angle * angular_difference_to_receipt_tilt
   ```

   Pick the neighbor with the lower cost.

9. **Draw Boundary Lines**  
   Through each chosen extreme segment, draw a line oriented at the final receipt tilt, extended across the full SVG height (or diagonal span).

10. **Animate Overlays**  
    Use react-spring transitions to fade and draw each component in sequence:
    - Words → Convex Hull → Centroid → Oriented Axes → Top/Bottom Lines → Left/Right Lines.

---

### Alternative Approaches

_(List the other approaches here if desired)_
