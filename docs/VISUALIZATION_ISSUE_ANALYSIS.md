# Visualization Issue Analysis

## Problem

The script `visualize_final_clusters_from_receipt_ocr.py` produces incorrectly aligned bounding boxes, while `visualize_final_clusters_cropped.py` produces correctly aligned bounding boxes.

## Root Cause

The issue is in `transform_receipt_line_to_image_line()` function. When transforming receipt-level OCR coordinates to image-space coordinates:

1. **Input**: ReceiptLine coordinates are in receipt-relative space (normalized 0-1, OCR space, y=0 at bottom)
2. **Transformation**: Uses `warp_transform` with `flip_y=True` to transform to image space
3. **Output**: The transformed coordinates don't match the corresponding image-level Line coordinates

### Test Results

For line_id=85:
- **ImageLine** (correct): `top_left: {'x': 0.1315, 'y': 0.2256}`
- **ReceiptLine transformed**: `top_left: {'x': 0.7386, 'y': 0.1747}`
- **X coordinate is completely wrong** (0.7386 vs 0.1315)
- **Y coordinate is also wrong** (0.1747 vs 0.2256)

This indicates the `warp_transform` approach is not producing correct image-space coordinates.

## Why the Correct Script Works

`visualize_final_clusters_cropped.py` works because:
1. It uses **image-level Line entities directly** - no transformation needed
2. Coordinates are already in the correct space (image-relative, normalized 0-1, OCR space)
3. It avoids all coordinate transformation errors

## The Coordinate Transformation Problem

The `warp_transform` function with `flip_y=True`:
- Takes receipt-relative coordinates (normalized 0-1, OCR space)
- Transforms them using perspective coefficients
- Outputs coordinates in some space, but the space is unclear

Looking at `inverse_perspective_transform`:
- If `flip_y=True`, it does: `corner["y"] = 1.0 - corner["y"]` at the end
- This suggests output is in OCR space (y=0 at bottom)
- But the coordinates don't match image-level lines, suggesting the transformation itself is wrong

## Possible Solutions

1. **Use image-level lines for visualization too** (defeats the purpose of using receipt OCR)
2. **Fix the coordinate transformation** - but this requires understanding exactly what `warp_transform` outputs
3. **Use a different transformation approach** - maybe use `Receipt.get_transform_to_image()` differently
4. **Map receipt lines to image lines by line_id** - use receipt OCR data but transform it the same way the correct script does

## Recommendation

Since the correct script (`visualize_final_clusters_cropped.py`) works perfectly, and the goal is to use receipt-level OCR for more accurate bounding boxes, we should:

1. Keep using image-level lines for clustering (to get correct clusters)
2. For visualization, use receipt-level OCR but transform it the same way the correct script transforms image-level lines
3. This means: transform receipt coordinates to image space, then use the same visualization logic as the correct script

But the challenge is: how do we correctly transform receipt coordinates to image space?

