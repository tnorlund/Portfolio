# Comparison: visualize_final_clusters_cropped.py vs dev.create_split_receipts.py

## Overview

This document compares two scripts that both visualize split receipts but use different data sources and transformation approaches:

1. **`scripts/visualize_final_clusters_cropped.py`** - Uses image-level `Line` entities (working script)
2. **`dev.create_split_receipts.py`** - Uses ReceiptOCR entities (`ReceiptLine`, `ReceiptWord`, `ReceiptLetter`)

## Key Differences

### Data Source

**`visualize_final_clusters_cropped.py`:**
- Uses image-level `Line` entities directly from DynamoDB
- Coordinates are already in image space (normalized 0-1, OCR space, y=0 at bottom)
- No coordinate conversion needed for clustering or visualization

**`dev.create_split_receipts.py`:**
- Uses ReceiptOCR entities (`ReceiptLine`, `ReceiptWord`, `ReceiptLetter`)
- Coordinates are relative to receipt (normalized 0-1, OCR space, y=0 at bottom)
- Must convert ReceiptOCR coordinates to image space for visualization

### Clustering

**Both scripts:**
- Use `recluster_receipt_lines()` from `scripts/split_receipt.py`
- This function expects image-level `Line` entities
- Both convert their data source to image-level `Line` entities before clustering

**`visualize_final_clusters_cropped.py`:**
```python
# Lines are already in image space
image_lines = client.list_lines_from_image(image_id)
cluster_dict = recluster_receipt_lines(image_lines, img_width, img_height)
```

**`dev.create_split_receipts.py`:**
```python
# Must convert ReceiptLines to image-level Lines for clustering
# (This happens inside create_receipt_entities, but conceptually...)
# ReceiptLines → image-level Lines → clustering
```

### Bounds Calculation

**`visualize_final_clusters_cropped.py`:**
```python
def calculate_receipt_bounds_from_lines(cluster_lines, img_width, img_height):
    # Uses cluster_lines (image-level Line entities) directly
    # Converts OCR space to PIL space: y_abs = (1 - corner["y"]) * img_height
    # Uses min_area_rect to find bounding box
    # Returns affine_transform and box_4_ordered
```

**`dev.create_split_receipts.py`:**
```python
def calculate_receipt_bounds_from_lines(cluster_lines, img_width, img_height):
    # Same function! Uses cluster_lines (image-level Line entities)
    # Same process: OCR → PIL, min_area_rect, affine transform
    # Returns same structure: width, height, affine_transform, box_4_ordered
```

**Key Point:** Both use the same bounds calculation function, which uses image-level `Line` entities.

### Image Warping

**Both scripts:**
- Use the same affine transform to warp the original image
- Create a perspective-corrected receipt image
- Use `PIL.Image.transform()` with `PIL.Image.AFFINE`

```python
warped_image = original_image.transform(
    (w, h),
    PIL_Image.AFFINE,
    affine_transform,
    resample=PIL_Image.BICUBIC,
)
```

### Coordinate Transformation for Visualization

**`visualize_final_clusters_cropped.py`:**
```python
# Step 1: Get line corners in image coordinate space (PIL space, y=0 at top)
corners_img = get_line_corners_image_coords(line, img_width, img_height)
# This converts: OCR space (normalized 0-1, y=0 at bottom) → PIL space (pixels, y=0 at top)
# Formula: y_pil = (1.0 - y_ocr) * img_height

# Step 2: Apply inverse affine transform to get warped receipt space
a_f, b_f, c_f, d_f, e_f, f_f = invert_affine(a_i, b_i, c_i, d_i, e_i, f_i)
for img_x, img_y in corners_img:
    receipt_x = a_f * img_x + b_f * img_y + c_f
    receipt_y = d_f * img_x + e_f * img_y + f_f
```

**`dev.create_split_receipts.py`:**
```python
# Step 1: ReceiptOCR coordinates are relative to receipt 2 (normalized 0-1, OCR space, y=0 at bottom)
# Step 2: Convert to absolute image coordinates (OCR space) using receipt 2's bounds
img_x_ocr = receipt_min_x_ocr + corner["x"] * receipt_width_ocr
img_y_ocr = receipt_min_y_ocr + corner["y"] * receipt_height_ocr

# Step 3: Convert from OCR space to PIL space (y=0 at top)
img_y_normalized = img_y_ocr / image_height
img_y_pil = (1.0 - img_y_normalized) * image_height

# Step 4: Apply inverse affine transform to get warped receipt space
receipt_x = a_f * img_x_ocr + b_f * img_y_pil + c_f
receipt_y = d_f * img_x_ocr + e_f * img_y_pil + f_f
```

**Key Difference:**
- `visualize_final_clusters_cropped.py` uses image-level `Line` entities directly (already in image space)
- `dev.create_split_receipts.py` must convert ReceiptOCR coordinates (receipt-relative) → image space → PIL space → warped space

### Receipt Entity Bounds

**`visualize_final_clusters_cropped.py`:**
- Does NOT create Receipt entities
- Only visualizes clusters
- Uses bounds calculated from `cluster_lines` (image-level Lines)

**`dev.create_split_receipts.py`:**
- Creates Receipt entities with bounds
- Uses `bounds_from_lines` (calculated from `cluster_lines` using `min_area_rect`)
- Converts bounds from PIL space to normalized OCR space for Receipt entity:
  ```python
  # Convert box_4_ordered from PIL space to OCR space
  box_4_ocr = []
  for corner in box_4_ordered:
      x_pil, y_pil = corner
      x_ocr = x_pil
      y_ocr = image_height - y_pil  # Flip Y: PIL -> OCR
      box_4_ocr.append((x_ocr, y_ocr))

  # Use box_4_ocr to set Receipt entity's top_left, top_right, etc.
  ```

## The Problem

The issue with `dev.create_split_receipts.py` is in the coordinate transformation pipeline:

1. **ReceiptOCR coordinates** are relative to receipt 2 (the new receipt)
2. **Receipt 2's bounds** are calculated from `cluster_lines` (image-level Lines)
3. **Transformation** converts ReceiptOCR → image space using receipt 2's bounds
4. **But** ReceiptOCR coordinates were originally relative to receipt 1 (the original receipt)

**The mismatch:** ReceiptOCR entities are created relative to receipt 2, but the transformation assumes they're relative to receipt 2's bounds (which are correct). However, the ReceiptOCR coordinates themselves might have been incorrectly transformed when creating the ReceiptOCR entities.

## Solution

The working script (`visualize_final_clusters_cropped.py`) works because:
- It uses image-level `Line` entities directly
- These are already in the correct coordinate space (image space)
- No conversion needed - just transform image space → warped space

To fix `dev.create_split_receipts.py`, we should:
1. Use image-level `Line` entities for visualization (like the working script)
2. OR ensure ReceiptOCR coordinates are correctly transformed when creating ReceiptOCR entities
3. OR use the same transformation pipeline as the working script (image-level Lines → warped space)

## Code Comparison

### Working Script (`visualize_final_clusters_cropped.py`)

```python
# Lines are already in image space
for line in cluster_lines:
    # Get corners in PIL space
    corners_img = get_line_corners_image_coords(line, img_width, img_height)
    # Transform to warped space
    corners_warped = []
    for img_x, img_y in corners_img:
        receipt_x = a_f * img_x + b_f * img_y + c_f
        receipt_y = d_f * img_x + e_f * img_y + f_f
        corners_warped.append((receipt_x, receipt_y))
    draw.polygon(corners_warped, outline=color, width=3)
```

### Our Script (`dev.create_split_receipts.py`)

```python
# ReceiptOCR coordinates are relative to receipt 2
for receipt_line in receipt_lines:
    # Convert receipt-relative to absolute image coordinates
    img_x_ocr = receipt_min_x_ocr + receipt_line.top_left["x"] * receipt_width_ocr
    img_y_ocr = receipt_min_y_ocr + receipt_line.top_left["y"] * receipt_height_ocr
    # Convert OCR space to PIL space
    img_y_pil = (1.0 - (img_y_ocr / image_height)) * image_height
    # Transform to warped space
    receipt_x = a_f * img_x_ocr + b_f * img_y_pil + c_f
    receipt_y = d_f * img_x_ocr + e_f * img_y_pil + f_f
```

## Recommendation

**Use image-level `Line` entities for visualization** (like the working script):
- More accurate (no coordinate conversion errors)
- Simpler transformation pipeline
- Proven to work correctly

**Use ReceiptOCR entities for DynamoDB records** (for accuracy):
- ReceiptOCR entities are more accurate (after perspective correction)
- But use image-level Lines for visualization to match the working script

This hybrid approach gives us:
- Accurate ReceiptOCR data in DynamoDB
- Correct visualizations using image-level Lines
- No coordinate transformation errors

