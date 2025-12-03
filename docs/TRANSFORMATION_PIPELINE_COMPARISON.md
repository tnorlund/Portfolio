# Transformation Pipeline Comparison

## Side-by-Side Code Comparison

### Working Script: `visualize_final_clusters_cropped.py`

```python
# Lines are already in image space (normalized 0-1, OCR space, y=0 at bottom)
for line in cluster_lines:
    # Step 1: Convert to PIL space (y=0 at top)
    corners_img = get_line_corners_image_coords(line, img_width, img_height)
    # This does: y_pil = (1.0 - y_ocr) * img_height

    # Step 2: Apply inverse affine transform
    a_f, b_f, c_f, d_f, e_f, f_f = invert_affine(a_i, b_i, c_i, d_i, e_i, f_i)
    corners_warped = []
    for img_x, img_y in corners_img:  # img_x, img_y are in PIL space
        receipt_x = a_f * img_x + b_f * img_y + c_f
        receipt_y = d_f * img_x + e_f * img_y + f_f
        corners_warped.append((receipt_x, receipt_y))

    draw.polygon(corners_warped, outline=color, width=3)
```

**Key Points:**
- Input: Image-level `Line` entities (already in image space)
- Transformation: OCR space → PIL space → warped space
- Simple 2-step process

### Our Script: `dev.create_split_receipts.py`

```python
# ReceiptOCR coordinates are relative to receipt 2 (normalized 0-1, OCR space, y=0 at bottom)
for receipt_line in receipt_lines:
    corners_warped = []
    for corner_name in ["top_left", "top_right", "bottom_right", "bottom_left"]:
        corner = getattr(receipt_line, corner_name)

        # Step 1: Convert receipt-relative to absolute image coordinates (OCR space)
        img_x_ocr = receipt_min_x_ocr + corner["x"] * receipt_width_ocr
        img_y_ocr = receipt_min_y_ocr + corner["y"] * receipt_height_ocr

        # Step 2: Convert OCR space to PIL space (y=0 at top)
        img_y_normalized = img_y_ocr / image_height
        img_y_pil = (1.0 - img_y_normalized) * image_height

        # Step 3: Apply inverse affine transform
        receipt_x = a_f * img_x_pil + b_f * img_y_pil + c_f  # Both in PIL space
        receipt_y = d_f * img_x_pil + e_f * img_y_pil + f_f  # Both in PIL space
        corners_warped.append((receipt_x, receipt_y))

    draw.polygon(corners_warped, outline=color, width=3)
```

**Key Points:**
- Input: ReceiptOCR entities (relative to receipt 2)
- Transformation: Receipt-relative → image OCR space → PIL space → warped space
- **BUG:** Mixing OCR and PIL coordinates in the affine transform!

## The Bug

**Line 688-690 in `dev.create_split_receipts.py`:**
```python
img_x_pil = img_x_ocr  # X doesn't flip, so they're the same
img_y_normalized = img_y_ocr / image_height  # Normalize to 0-1
img_y_pil = (1.0 - img_y_normalized) * image_height  # Flip Y: OCR -> PIL
```

**Line 694-695:**
```python
receipt_x = a_f * img_x_pil + b_f * img_y_pil + c_f  # Both in PIL space ✓
receipt_y = d_f * img_x_pil + e_f * img_y_pil + f_f  # Both in PIL space ✓
```

**The code is actually correct** - both X and Y are converted to PIL space before applying the affine transform. The issue must be elsewhere in the transformation pipeline.

## The Fix

**Option 1: Use image-level Lines (like working script)**
```python
# Use cluster_lines (image-level Lines) for visualization
for line in cluster_lines:
    corners_img = get_line_corners_image_coords(line, img_width, img_height)
    corners_warped = []
    for img_x, img_y in corners_img:  # Both in PIL space
        receipt_x = a_f * img_x + b_f * img_y + c_f
        receipt_y = d_f * img_x + e_f * img_y + f_f
        corners_warped.append((receipt_x, receipt_y))
    draw.polygon(corners_warped, outline=color, width=3)
```

**Option 2: Fix the coordinate mixing**
```python
# Ensure both X and Y are in PIL space before applying transform
img_x_pil = img_x_ocr  # X doesn't flip, so they're the same
img_y_pil = (1.0 - (img_y_ocr / image_height)) * image_height

# Now both are in PIL space
receipt_x = a_f * img_x_pil + b_f * img_y_pil + c_f
receipt_y = d_f * img_x_pil + e_f * img_y_pil + f_f
```

## Coordinate Space Summary

| Space | X-axis | Y-axis | Notes |
|-------|--------|--------|-------|
| OCR | 0-1 (normalized) or pixels | 0 at bottom, 1 at top | Original OCR coordinate system |
| PIL | 0-1 (normalized) or pixels | 0 at top, 1 at bottom | Image processing coordinate system |
| Conversion | X_ocr = X_pil | Y_ocr = image_height - Y_pil | Y-axis flips |

**Key Insight:** The affine transform operates in PIL space (y=0 at top), so all coordinates must be converted to PIL space before applying the transform.

