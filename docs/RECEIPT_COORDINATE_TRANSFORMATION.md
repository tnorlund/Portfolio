# Receipt Coordinate Transformation

This document explains how OCR coordinates are transformed from image space to receipt space during the upload process.

## Coordinate Systems

### Image Space
- **Coordinate system**: Normalized 0-1 relative to the original image dimensions
- **Y-axis**: OCR space (y=0 at bottom, y=1 at top)
- **Origin**: Bottom-left corner
- **Used for**: `Line`, `Word`, `Letter` entities (image-level OCR)

### Receipt Space
- **Coordinate system**: Normalized 0-1 relative to the receipt's bounding box
- **Y-axis**: OCR space (y=0 at bottom, y=1 at top)
- **Origin**: Bottom-left corner of the receipt's bounding box
- **Used for**: `ReceiptLine`, `ReceiptWord`, `ReceiptLetter` entities (receipt-level OCR)

## Upload Process Flow

### 1. Initial OCR (Image Space)
- OCR is run on the original image
- Results are stored as `Line`, `Word`, `Letter` entities
- Coordinates are normalized 0-1 relative to the original image
- Y-axis: OCR space (y=0 at bottom)

### 2. Clustering
- Lines are clustered using DBSCAN and angle-based splitting
- Clusters represent individual receipts
- Lines remain in image space

### 3. Receipt Bounding Box Calculation
For each cluster, the receipt bounding box is calculated:

```python
# Collect all corner points from cluster lines
points_abs = []
for line in cluster_lines:
    for corner in [line.top_left, line.top_right, line.bottom_left, line.bottom_right]:
        x_abs = corner["x"] * image.width
        y_abs = (1 - corner["y"]) * image.height  # Flip Y: OCR -> PIL
        points_abs.append((x_abs, y_abs))

# Find minimum area rectangle
(cx, cy), (rw, rh), angle_deg = min_area_rect(points_abs)
w = int(round(rw))
h = int(round(rh))

# Ensure portrait orientation
if w > h:
    angle_deg -= 90.0
    w, h = h, w
    rw, rh = rh, rw

# Get ordered corners
box_4 = box_points((cx, cy), (rw, rh), angle_deg)
box_4_ordered = reorder_box_points(box_4)
```

### 4. Affine Transform Calculation
An affine transform is calculated to map from receipt space to image space:

```python
# Source corners (in image space, PIL coordinates)
src_tl = box_4_ordered[0]  # Top-left
src_tr = box_4_ordered[1]  # Top-right
src_bl = box_4_ordered[3]  # Bottom-left

# Destination corners (in receipt space, normalized 0-1)
dst_tl = (0, 0)
dst_tr = (w - 1, 0)
dst_bl = (0, h - 1)

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

### 5. Image Warping
The original image is warped to create the receipt image:

```python
affine_img = image.transform(
    (w, h),
    PIL_Image.AFFINE,
    (a_i, b_i, c_i, d_i, e_i, f_i),  # Inverse transform
    resample=PIL_Image.BICUBIC,
)
```

### 6. Receipt OCR (Receipt Space)
- OCR is run on the warped receipt image (`affine_img`)
- Results are stored as `ReceiptLine`, `ReceiptWord`, `ReceiptLetter` entities
- Coordinates are already in receipt space (normalized 0-1 relative to receipt)
- Y-axis: OCR space (y=0 at bottom)

### 7. Receipt Entity Creation
The `Receipt` entity stores the bounding box corners in image space:

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
    top_right={
        "x": box_4_ordered[1][0] / image.width,
        "y": 1 - box_4_ordered[1][1] / image.height,
    },
    bottom_right={
        "x": box_4_ordered[2][0] / image.width,
        "y": 1 - box_4_ordered[2][1] / image.height,
    },
    bottom_left={
        "x": box_4_ordered[3][0] / image.width,
        "y": 1 - box_4_ordered[3][1] / image.height,
    },
    # ... other fields
)
```

## Transforming Coordinates

### From Image Space to Receipt Space

To transform coordinates from image space to receipt space:

1. **Get the receipt's transform coefficients**:
   ```python
   transform_coeffs, receipt_width, receipt_height = receipt.get_transform_to_image(
       image_width, image_height
   )
   # Invert to get image -> receipt transform
   image_to_receipt_coeffs = invert_warp(*transform_coeffs)
   ```

2. **Transform the coordinates**:
   ```python
   # For a point in image space (normalized 0-1, OCR space)
   img_x = point["x"] * image_width
   img_y = (1 - point["y"]) * image_height  # Flip Y: OCR -> PIL

   # Apply perspective transform
   receipt_x, receipt_y = warp_transform(
       img_x, img_y, image_to_receipt_coeffs
   )

   # Normalize to receipt space (0-1)
   receipt_x_norm = receipt_x / receipt_width
   receipt_y_norm = receipt_y / receipt_height

   # Flip Y back to OCR space
   receipt_y_ocr = 1 - receipt_y_norm
   ```

### From Receipt Space to Image Space

To transform coordinates from receipt space to image space:

1. **Get the receipt's transform coefficients**:
   ```python
   transform_coeffs, receipt_width, receipt_height = receipt.get_transform_to_image(
       image_width, image_height
   )
   ```

2. **Transform the coordinates**:
   ```python
   # For a point in receipt space (normalized 0-1, OCR space)
   receipt_x = point["x"] * receipt_width
   receipt_y = (1 - point["y"]) * receipt_height  # Flip Y: OCR -> PIL

   # Apply perspective transform
   img_x, img_y = warp_transform(
       receipt_x, receipt_y, transform_coeffs
   )

   # Normalize to image space (0-1)
   img_x_norm = img_x / image_width
   img_y_norm = img_y / image_height

   # Flip Y back to OCR space
   img_y_ocr = 1 - img_y_norm
   ```

## Key Points

1. **OCR coordinates are always in OCR space** (y=0 at bottom), even when stored in DynamoDB
2. **Image warping uses PIL space** (y=0 at top), so Y-flips are needed during transformation
3. **Receipt OCR is run on the warped image**, so coordinates are already in receipt space
4. **The Receipt entity stores corners in image space** (normalized 0-1, OCR space)
5. **Transform methods** (`Receipt.get_transform_to_image`, `Image.get_transform_to_receipt`) handle the coordinate system conversions

## Example: Splitting a Receipt

When splitting a receipt (as in `split_receipt.py`):

1. **Load original receipt and lines** (in receipt space)
2. **Transform lines to image space** using `Receipt.get_transform_to_image()`
3. **Re-cluster lines** (in image space)
4. **Create new receipt bounding boxes** for each cluster
5. **Transform lines to new receipt space** using the new receipt's transform
6. **Create new ReceiptLine/ReceiptWord entities** with transformed coordinates

This ensures that coordinates are correctly normalized relative to each receipt's bounding box.

