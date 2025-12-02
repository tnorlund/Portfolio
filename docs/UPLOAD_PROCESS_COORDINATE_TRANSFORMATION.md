# Upload Process: Coordinate Transformation for Clustered Receipts

This document explains how the upload process transforms correctly clustered receipts into images with correctly transformed OCR coordinates.

## Current Upload Process (scan.py)

### Step 1: Initial OCR (Image Space)
1. OCR is run on the original image
2. Results stored as `Line`, `Word`, `Letter` entities
3. Coordinates are in **image space** (normalized 0-1 relative to image, OCR space)

### Step 2: Clustering
1. Lines are clustered using:
   - `dbscan_lines_x_axis()` - X-axis clustering
   - `split_clusters_by_angle_consistency()` - Angle-based splitting
   - `merge_clusters_with_agent_logic()` - Smart merging (if needed)
   - `join_overlapping_clusters()` - Final merge step
2. Each cluster represents one receipt
3. Lines remain in **image space**

### Step 3: Receipt Creation (per cluster)
For each cluster:

1. **Calculate Receipt Bounding Box**:
   ```python
   # Collect all corner points from cluster lines (in image space)
   points_abs = []
   for line in cluster_lines:
       for corner in [line.top_left, line.top_right, line.bottom_left, line.bottom_right]:
           x_abs = corner["x"] * image.width
           y_abs = (1 - corner["y"]) * image.height  # Flip Y: OCR -> PIL
           points_abs.append((x_abs, y_abs))

   # Find minimum area rectangle
   (cx, cy), (rw, rh), angle_deg = min_area_rect(points_abs)
   w, h = int(round(rw)), int(round(rh))

   # Ensure portrait orientation
   if w > h:
       angle_deg -= 90.0
       w, h = h, w
       rw, rh = rh, rw

   # Get ordered corners
   box_4 = box_points((cx, cy), (rw, rh), angle_deg)
   box_4_ordered = reorder_box_points(box_4)
   ```

2. **Calculate Affine Transform**:
   ```python
   # Source corners (in image space, PIL coordinates)
   src_tl = box_4_ordered[0]
   src_tr = box_4_ordered[1]
   src_bl = box_4_ordered[3]

   # Build inverse transform (receipt -> image)
   a_i = (src_tr[0] - src_tl[0]) / (w - 1)
   d_i = (src_tr[1] - src_tl[1]) / (w - 1)
   b_i = (src_bl[0] - src_tl[0]) / (h - 1)
   e_i = (src_bl[1] - src_tl[1]) / (h - 1)
   c_i = src_tl[0]
   f_i = src_tl[1]

   # Invert to get forward transform (image -> receipt)
   a_f, b_f, c_f, d_f, e_f, f_f = invert_affine(a_i, b_i, c_i, d_i, e_i, f_i)
   ```

3. **Warp Image**:
   ```python
   affine_img = image.transform(
       (w, h),
       PIL_Image.AFFINE,
       (a_i, b_i, c_i, d_i, e_i, f_i),  # Inverse transform
       resample=PIL_Image.BICUBIC,
   )
   ```

4. **Create Receipt Entity**:
   ```python
   receipt = Receipt(
       receipt_id=cluster_id,
       image_id=image_id,
       width=w,
       height=h,
       top_left={
           "x": box_4_ordered[0][0] / image.width,
           "y": 1 - box_4_ordered[0][1] / image.height,  # Flip Y: PIL -> OCR
       },
       # ... other corners
   )
   ```

5. **Run OCR on Warped Image**:
   - OCR is run on `affine_img` (the warped receipt image)
   - Results are stored as `ReceiptLine`, `ReceiptWord`, `ReceiptLetter` entities
   - Coordinates are **already in receipt space** (normalized 0-1 relative to receipt)
   - This is because OCR is run on the warped image, not the original

## Key Insight

**The receipt-level OCR coordinates are already in receipt space because OCR is run on the warped receipt image, not the original image.**

The warped image (`affine_img`) has dimensions `(w, h)` which match the receipt's bounding box. When OCR is run on this warped image, the coordinates are naturally in receipt space (0-1 relative to the warped image dimensions).

## Alternative: Transforming Image-Level OCR to Receipt Space

If you want to use the image-level OCR results (from Step 1) instead of re-running OCR on the warped image, you need to transform the coordinates:

### Option 1: Use Receipt Transform Methods

```python
# Get transform from receipt to image
transform_coeffs, receipt_width, receipt_height = receipt.get_transform_to_image(
    image_width, image_height
)

# Invert to get image to receipt transform
image_to_receipt_coeffs = invert_warp(*transform_coeffs)

# Transform a line from image space to receipt space
def transform_line_to_receipt_space(line: Line, receipt: Receipt, image_width: int, image_height: int):
    # Get transform coefficients
    transform_coeffs, receipt_width, receipt_height = receipt.get_transform_to_image(
        image_width, image_height
    )
    image_to_receipt_coeffs = invert_warp(*transform_coeffs)

    # Transform each corner
    corners = ['top_left', 'top_right', 'bottom_left', 'bottom_right']
    receipt_corners = {}

    for corner_name in corners:
        corner = getattr(line, corner_name)
        # Convert to absolute image coordinates (PIL space)
        img_x = corner["x"] * image_width
        img_y = (1 - corner["y"]) * image_height  # Flip Y: OCR -> PIL

        # Apply perspective transform
        receipt_x, receipt_y = warp_transform(
            img_x, img_y, image_to_receipt_coeffs
        )

        # Normalize to receipt space (0-1)
        receipt_x_norm = receipt_x / receipt_width
        receipt_y_norm = receipt_y / receipt_height

        # Flip Y back to OCR space
        receipt_y_ocr = 1 - receipt_y_norm

        receipt_corners[corner_name] = {
            "x": receipt_x_norm,
            "y": receipt_y_ocr,
        }

    return receipt_corners
```

### Option 2: Use Affine Transform (for scan.py)

Since `scan.py` uses affine transforms (not perspective), you can use the forward transform directly:

```python
# Forward transform (image -> receipt) was calculated earlier
a_f, b_f, c_f, d_f, e_f, f_f = invert_affine(a_i, b_i, c_i, d_i, e_i, f_i)

# Transform a point from image space to receipt space
def transform_point_to_receipt_space(point_x, point_y, a_f, b_f, c_f, d_f, e_f, f_f, w, h):
    # Convert to absolute image coordinates (PIL space)
    img_x = point_x * image_width
    img_y = (1 - point_y) * image_height  # Flip Y: OCR -> PIL

    # Apply affine transform
    receipt_x = a_f * img_x + b_f * img_y + c_f
    receipt_y = d_f * img_x + e_f * img_y + f_f

    # Normalize to receipt space (0-1)
    receipt_x_norm = receipt_x / w
    receipt_y_norm = receipt_y / h

    # Flip Y back to OCR space
    receipt_y_ocr = 1 - receipt_y_norm

    return receipt_x_norm, receipt_y_ocr
```

## Summary

1. **Current Process**: OCR is run twice - once on original image (image space), once on warped receipt image (receipt space)
2. **Receipt Space Coordinates**: Come from OCR run on the warped image, so they're already correct
3. **If Using Image-Level OCR**: Need to transform coordinates using receipt's transform methods
4. **Transform Methods**: `Receipt.get_transform_to_image()` and `Image.get_transform_to_receipt()` handle the coordinate conversions

## Integration with New Clustering

The new clustering improvements (horizontal span check, opposite-side check) ensure that:
- Side-by-side receipts are correctly separated
- Each cluster represents one receipt
- The receipt bounding box calculation works correctly for each cluster
- OCR coordinates are correctly transformed to receipt space

No changes are needed to the coordinate transformation logic - it already works correctly for the clustered receipts.

