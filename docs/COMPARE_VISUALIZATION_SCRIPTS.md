# Comparing Visualization Scripts

## Correct Script: `visualize_final_clusters_cropped.py`

### Data Flow
1. **Loads image-level `Line` entities** directly from DynamoDB
   - Coordinates: Already in image space (normalized 0-1, OCR space, y=0 at bottom)
   - All 156 lines included (no missing lines)

2. **Clusters using `recluster_receipt_lines()`**
   - Uses image-level lines directly
   - Produces correct 2 clusters

3. **For each cluster:**
   - Calculates bounds from all line corners using `min_area_rect`
   - Gets affine transform coefficients
   - Warps image using `PIL.Image.transform()` with `AFFINE` mode
   - Transforms line corners from image space to warped receipt space:
     ```python
     corners_img = get_line_corners_image_coords(line, img_width, img_height)
     # Converts OCR space (y=0 at bottom) to PIL space (y=0 at top)
     # Then applies inverse affine transform to get warped receipt coordinates
     ```

### Key Points
- ✅ Uses image-level lines directly (no coordinate transformation needed)
- ✅ All lines included (156 lines)
- ✅ Correct clustering (2 clusters)
- ✅ Correctly aligned bounding boxes

## Incorrect Script: `visualize_final_clusters_from_receipt_ocr.py`

### Data Flow
1. **Loads receipt-level `ReceiptLine` entities** from DynamoDB
   - Coordinates: In receipt-relative space (normalized 0-1, OCR space, y=0 at bottom)
   - Only 142 lines (14 missing)

2. **Transforms receipt lines to image-space lines:**
   ```python
   transform_receipt_line_to_image_line()
   # Uses warp_transform to convert receipt space -> image space
   # Then flips Y: PIL -> OCR
   ```

3. **Clusters using `recluster_receipt_lines()`**
   - Uses image-level lines for clustering (all 156 lines)
   - Produces correct 2 clusters

4. **For visualization:**
   - Uses transformed receipt lines when available
   - Falls back to image-level lines for missing lines
   - Transforms to warped receipt space same as correct script

### Issue
The problem is in `transform_receipt_line_to_image_line()`:
- After `warp_transform` with `flip_y=True`, coordinates are in normalized image space
- But the Y coordinate space is unclear - the code assumes PIL space and flips to OCR space
- However, `warp_transform` with `flip_y=True` may already output in OCR space
- This causes incorrect coordinate transformation

### Comparison Test
When comparing a transformed receipt line to the corresponding image line:
- X coordinates don't match (0.7386 vs 0.1315) - suggests transformation is wrong
- Y coordinates don't match even after Y-flip (0.1747 vs 0.2256)

This indicates the `warp_transform` approach may not be producing the correct image-space coordinates.

## Root Cause

The issue is that `transform_receipt_line_to_image_line()` is trying to transform receipt coordinates to image coordinates, but:
1. The transformation may not be accurate
2. The coordinate space assumptions may be wrong
3. The Y-flip logic may be incorrect

The correct script avoids this entirely by using image-level lines directly, which are already in the correct coordinate space.

## Solution

To fix `visualize_final_clusters_from_receipt_ocr.py`, we need to:
1. Verify what coordinate space `warp_transform` actually outputs
2. Ensure the Y-flip logic matches the actual output space
3. Compare transformed coordinates with image-level line coordinates to verify correctness
4. Or, use the same approach as the correct script but map receipt lines to image lines by `line_id` for visualization

