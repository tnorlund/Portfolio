# Clustering Phases Visualization Comparison

## Overview

This document compares two approaches to visualizing clustering phases:

1. **`scripts/visualize_clustering_phases.py`** - Uses image-level `Line` entities
2. **`dev.visualize_clustering_phases_receipt_ocr.py`** - Uses ReceiptOCR entities (`ReceiptLine`, `ReceiptWord`, `ReceiptLetter`)

## Key Differences

### Data Source

**Working Script (`visualize_clustering_phases.py`):**
- Uses image-level `Line` entities directly from DynamoDB
- Coordinates are already in image space (normalized 0-1, OCR space, y=0 at bottom)
- No coordinate conversion needed for clustering

**New Script (`dev.visualize_clustering_phases_receipt_ocr.py`):**
- Uses ReceiptOCR entities (`ReceiptLine`, `ReceiptWord`, `ReceiptLetter`) from DynamoDB
- Coordinates are relative to receipt (normalized 0-1, OCR space, y=0 at bottom)
- Converts ReceiptOCR coordinates to image space for clustering
- **Retains ReceiptOCR accuracy** while using proven clustering algorithms

### Coordinate Conversion

**Working Script:**
```python
# Lines are already in image space
for line in image_lines:
    corners = get_line_corners_image_coords(line, img_width, img_height)
    # corners are in PIL space (y=0 at top) for visualization
```

**New Script:**
```python
# Step 1: Convert ReceiptLine to image-space Line
def convert_receipt_line_to_image_line(receipt_line, receipt, image_width, image_height):
    # Get receipt bounds in absolute image coordinates (OCR space)
    receipt_min_x_ocr = receipt.top_left["x"] * image_width
    receipt_max_x_ocr = receipt.top_right["x"] * image_width
    receipt_min_y_ocr = receipt.bottom_left["y"] * image_height
    receipt_max_y_ocr = receipt.top_left["y"] * image_height

    # Convert ReceiptLine corners from receipt-relative to absolute image coordinates
    def receipt_to_image_coord(corner):
        img_x_ocr = receipt_min_x_ocr + corner["x"] * receipt_width_ocr
        img_y_ocr = receipt_min_y_ocr + corner["y"] * receipt_height_ocr
        return {"x": img_x_ocr / image_width, "y": img_y_ocr / image_height}

    # Create Line entity with image-space coordinates
    return Line(...)

# Step 2: Use converted Lines for clustering (same as working script)
for line in image_lines:
    corners = get_line_corners_image_coords(line, img_width, img_height)
    # corners are in PIL space (y=0 at top) for visualization
```

### Clustering Process

Both scripts use the **same clustering functions**:
1. `dbscan_lines_x_axis()` - X-axis clustering
2. `split_clusters_by_angle_consistency()` - Angle-based splitting
3. `reassign_lines_by_x_proximity()` - X-proximity reassignment
4. `reassign_lines_by_vertical_proximity()` - Vertical proximity reassignment
5. `should_apply_smart_merging()` + `merge_clusters_with_agent_logic()` - Smart merging
6. `join_overlapping_clusters()` - Join overlapping clusters

The only difference is the **input data source**:
- Working script: image-level `Line` entities
- New script: ReceiptOCR entities converted to image-space `Line` entities

### Visualization

Both scripts create the same visualization panels:
1. Original receipt lines
2. X-axis clustering results
3. After angle splitting
4. After X-proximity reassignment
5. After vertical proximity
6. After smart merging
7. After join overlapping
8. Final clusters

The visualization uses the same coordinate transformation:
- Convert to PIL space (y=0 at top) for drawing
- Use `get_line_corners_image_coords()` function

## Why Use ReceiptOCR?

**Advantages:**
1. **More accurate** - ReceiptOCR entities are created after perspective correction and are more precise
2. **Consistent with production** - Production code uses ReceiptOCR entities
3. **Better for debugging** - Can see exactly what coordinates are stored in DynamoDB

**Trade-offs:**
1. Requires coordinate conversion (receipt-relative → image space)
2. Slightly more complex transformation pipeline

## Usage

**Working Script:**
```bash
python scripts/visualize_clustering_phases.py \
    --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
    --receipt-id 1 \
    --output clustering_phases_visualization.png \
    --raw-bucket raw-image-bucket-c779c32
```

**New Script (ReceiptOCR):**
```bash
python dev.visualize_clustering_phases_receipt_ocr.py \
    --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
    --receipt-id 1 \
    --output clustering_phases_receipt_ocr_visualization.png
```

## Next Steps

1. Compare the visualizations side-by-side to verify they produce the same clusters
2. Use the ReceiptOCR version to debug coordinate transformation issues
3. Once verified, use ReceiptOCR entities in the split receipt process for accuracy

