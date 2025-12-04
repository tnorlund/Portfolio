# Step-by-Step Comparison: visualize_final_clusters_cropped.py vs split_receipt.py

This document provides a detailed step-by-step comparison of the business logic in both scripts, highlighting key differences and how to use receipt-level OCR entities for more accurate clustering.

## Overview

| Aspect | `visualize_final_clusters_cropped.py` | `split_receipt.py` |
|--------|--------------------------------------|-------------------|
| **Purpose** | Visualization/debugging tool | Production split operation |
| **Data Source** | Image-level `Line` entities | Image-level `Line` entities (currently) |
| **Clustering Input** | `List[Line]` (image-level OCR) | `List[Line]` (image-level OCR) |
| **Accuracy** | Lower (image-level OCR) | Lower (image-level OCR) |
| **Output** | PNG visualizations + JSON exports | New DynamoDB records + S3 images |

## Step-by-Step Business Logic Comparison

### Step 1: Data Loading

#### `visualize_final_clusters_cropped.py` (lines 172-173)
```python
image_entity = client.get_image(image_id)
image_lines = client.list_lines_from_image(image_id)  # Image-level OCR
```

#### `split_receipt.py` (lines 1572-1599)
```python
original_receipt = client.get_receipt(image_id, original_receipt_id)
original_receipt_lines = client.list_receipt_lines_from_receipt(image_id, original_receipt_id)  # Receipt-level OCR
original_receipt_words = client.list_receipt_words_from_receipt(image_id, original_receipt_id)
original_receipt_letters = client.list_receipt_letters_from_receipt(image_id, original_receipt_id)

# BUT THEN uses image-level for clustering:
image_lines = client.list_lines_from_image(image_id)  # Image-level OCR
image_entity = client.get_image(image_id)
```

**Key Difference**:
- Visualization script only has access to image-level OCR
- Split script has access to receipt-level OCR but **doesn't use it for clustering**

### Step 2: Clustering

#### Both scripts use the same function:
```python
cluster_dict = recluster_receipt_lines(
    image_lines,  # Image-level Line entities
    image_entity.width,
    image_entity.height,
)
```

**Problem**: Both scripts use image-level OCR for clustering, which is less accurate than receipt-level OCR.

**Solution**: Convert `ReceiptLine` entities to `Line` entities in image space, then cluster.

### Step 3: Bounds Calculation

#### `visualize_final_clusters_cropped.py` (lines 224-226)
```python
bounds = calculate_receipt_bounds_from_lines(
    cluster_lines,  # Image-level Line entities
    img_width,
    img_height,
)
```

Uses `min_area_rect` on image-level line corners to find bounding box.

#### `split_receipt.py` (lines 480-485)
```python
bounds = calculate_receipt_bounds(
    cluster_receipt_words,  # ReceiptWord entities (receipt-relative)
    original_receipt,       # Original Receipt entity
    image_width,
    image_height,
)
```

Uses `ReceiptWord` entities (receipt-relative coordinates) and transforms them to image space.

**Key Difference**:
- Visualization: Uses image-level lines directly
- Split: Uses receipt-level words, transforms to image space

### Step 4: Image Warping

#### Both scripts use the same affine transform approach:
```python
# Calculate affine transform from bounds
affine_transform = bounds["affine_transform"]

# Warp image
warped_image = original_image.transform(
    (w, h),
    PIL_Image.AFFINE,
    affine_transform,
    resample=PIL_Image.BICUBIC,
)
```

**Same logic**: Both use `min_area_rect` → `box_points` → affine transform.

### Step 5: Coordinate Transformation for Visualization

#### `visualize_final_clusters_cropped.py` (lines 249-269)
```python
for line in cluster_lines:  # Image-level Line entities
    # Get line corners in image coordinate space (PIL space, y=0 at top)
    corners_img = get_line_corners_image_coords(line, img_width, img_height)

    # Transform to warped receipt space using inverse affine transform
    a_f, b_f, c_f, d_f, e_f, f_f = invert_affine(...)

    for img_x, img_y in corners_img:
        receipt_x = a_f * img_x + b_f * img_y + c_f
        receipt_y = d_f * img_x + e_f * img_y + f_f
        corners_warped.append((receipt_x, receipt_y))

    draw.polygon(corners_warped, outline=color, width=3)
```

**Transformation**: Image space (PIL) → Warped receipt space (for drawing)

#### `split_receipt.py` (lines 638-689)
```python
for original_line in cluster_receipt_lines:  # ReceiptLine entities
    # Step 1: Transform from original receipt space to image space
    line_copy = copy.deepcopy(original_line)
    forward_coeffs = invert_warp(*transform_coeffs)
    line_copy.warp_transform(
        *forward_coeffs,
        src_width=image_width,
        src_height=image_height,
        dst_width=orig_receipt_width,
        dst_height=orig_receipt_height,
        flip_y=True,
    )

    # Step 2: Transform from image space to new receipt space
    line_top_left_x, line_top_left_y = img_to_receipt_coord(...)
    # ... create new ReceiptLine entity
```

**Transformation**: Original receipt space → Image space → New receipt space (for new entities)

**Key Difference**:
- Visualization: Only transforms for display (image → warped)
- Split: Transforms for entity creation (receipt → image → new receipt)

## How to Use Receipt-Level OCR for Clustering

### Option 1: Convert ReceiptLine to Line (Recommended)

Use the existing `transform_receipt_line_to_image_line` function from `visualize_final_clusters_from_receipt_ocr.py`:

```python
def convert_receipt_lines_to_image_lines(
    receipt_lines: List[ReceiptLine],
    original_receipt: Receipt,
    image_width: int,
    image_height: int,
) -> List[Line]:
    """Convert ReceiptLine entities to Line entities in image space."""
    image_lines = []
    for receipt_line in receipt_lines:
        line = transform_receipt_line_to_image_line(
            receipt_line,
            original_receipt,
            image_width,
            image_height,
        )
        image_lines.append(line)
    return image_lines
```

### Option 2: Modify recluster_receipt_lines to accept ReceiptLine

Create a new function that works directly with ReceiptLine entities:

```python
def recluster_receipt_lines_from_receipt_ocr(
    receipt_lines: List[ReceiptLine],
    original_receipt: Receipt,
    image_width: int,
    image_height: int,
) -> Dict[int, List[ReceiptLine]]:
    """Re-cluster using receipt-level OCR entities."""
    # Convert to image space for clustering
    image_lines = convert_receipt_lines_to_image_lines(
        receipt_lines, original_receipt, image_width, image_height
    )

    # Cluster in image space
    cluster_dict = recluster_receipt_lines(
        image_lines, image_width, image_height
    )

    # Map back to ReceiptLine entities
    receipt_line_by_id = {rl.line_id: rl for rl in receipt_lines}
    receipt_cluster_dict = {}
    for cluster_id, cluster_lines in cluster_dict.items():
        receipt_cluster_dict[cluster_id] = [
            receipt_line_by_id[line.line_id]
            for line in cluster_lines
            if line.line_id in receipt_line_by_id
        ]

    return receipt_cluster_dict
```

## Recommended Changes

### For `visualize_final_clusters_cropped.py`:

1. **Add receipt_id parameter** to load receipt-level OCR
2. **Convert ReceiptLine to Line** before clustering
3. **Use receipt-level OCR for visualization** (more accurate bounding boxes)

### For `split_receipt.py`:

1. **Use ReceiptLine entities for clustering** instead of image-level Line entities
2. **Keep existing coordinate transformation logic** (already handles receipt → image → new receipt)

## Benefits of Using Receipt-Level OCR

1. **More Accurate**: Receipt-level OCR has been perspective-corrected and is more accurate
2. **Consistent**: Uses the same data that will be split into new receipts
3. **Better Clustering**: More accurate coordinates lead to better cluster boundaries
4. **Fewer Edge Cases**: Receipt-level OCR is already filtered and validated

## Implementation Notes

- The `transform_receipt_line_to_image_line` function already exists in `visualize_final_clusters_from_receipt_ocr.py`
- The transformation uses `receipt.get_transform_to_image()` which handles perspective correction
- Coordinate spaces must be carefully managed:
  - ReceiptLine: Receipt-relative (normalized 0-1, OCR space, y=0 at bottom)
  - Line: Image-relative (normalized 0-1, OCR space, y=0 at bottom)
  - PIL: Image-relative (pixels, y=0 at top)

