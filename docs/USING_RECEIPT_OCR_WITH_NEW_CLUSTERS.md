# Using Receipt OCR Data with New Clusters

This document walks through how to use existing receipt OCR data (which is more accurate) with the new clustering to create new receipts, without re-running OCR.

## Overview

The process:
1. **Cluster lines** using the new clustering algorithm (in image space)
2. **Map clusters to existing receipt OCR data** (ReceiptLine, ReceiptWord, ReceiptLetter)
3. **Calculate new receipt bounding boxes** for each cluster
4. **Transform coordinates** from original receipt space → image space → new receipt space
5. **Create new Receipt entities** with transformed OCR data

## Step-by-Step Process

### Step 1: Load Existing Data

```python
from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import Line, Receipt, ReceiptLine, ReceiptWord, ReceiptLetter

client = DynamoClient("ReceiptsTable-dc5be22")
image_id = "13da1048-3888-429f-b2aa-b3e15341da5e"
original_receipt_id = 1

# Load image-level lines (for clustering)
image_lines = client.list_lines_from_image(image_id)
image_entity = client.get_image(image_id)

# Load existing receipt OCR data (more accurate)
original_receipt = client.get_receipt(image_id, original_receipt_id)
original_receipt_lines = client.list_receipt_lines_from_receipt(image_id, original_receipt_id)
original_receipt_words = client.list_receipt_words_from_receipt(image_id, original_receipt_id)
original_receipt_letters = client.list_receipt_letters_from_receipt(image_id, original_receipt_id)
```

### Step 2: Cluster Lines (Image Space)

```python
from receipt_upload.cluster import (
    dbscan_lines_x_axis,
    split_clusters_by_angle_consistency,
    reassign_lines_by_x_proximity,
    reassign_lines_by_vertical_proximity,
    merge_clusters_with_agent_logic,
    should_apply_smart_merging,
    join_overlapping_clusters,
)

# Phase 1: X-axis clustering
cluster_dict = dbscan_lines_x_axis(image_lines, eps=0.08)

# Phase 1b: Split by angle consistency
cluster_dict = split_clusters_by_angle_consistency(
    cluster_dict,
    angle_tolerance=3.0,
    min_samples=2,
)

# Phase 1c: X-proximity reassignment
cluster_dict = reassign_lines_by_x_proximity(
    cluster_dict,
    x_proximity_threshold=0.1,
)

# Phase 1d: Vertical proximity reassignment
cluster_dict = reassign_lines_by_vertical_proximity(
    cluster_dict,
    vertical_proximity_threshold=0.05,
    x_proximity_threshold=0.1,
)

# Phase 2: Smart merging (if needed)
if should_apply_smart_merging(cluster_dict, len(image_lines)):
    cluster_dict = merge_clusters_with_agent_logic(
        cluster_dict,
        min_score=0.5,
        x_proximity_threshold=0.4,
    )

# Final: Join overlapping clusters
if len(cluster_dict) > 2:
    cluster_dict = join_overlapping_clusters(
        cluster_dict,
        image_entity.width,
        image_entity.height,
        iou_threshold=0.1,
        x_proximity_threshold=0.3,
    )
```

### Step 3: Map Clusters to Receipt OCR Data

```python
# Get line_ids in each cluster
cluster_line_ids = {}
for cluster_id, cluster_lines in cluster_dict.items():
    cluster_line_ids[cluster_id] = {line.line_id for line in cluster_lines}

# Filter receipt OCR data by cluster
cluster_receipt_data = {}
for cluster_id, line_ids in cluster_line_ids.items():
    cluster_receipt_data[cluster_id] = {
        "lines": [rl for rl in original_receipt_lines if rl.line_id in line_ids],
        "words": [rw for rw in original_receipt_words if rw.line_id in line_ids],
        "letters": [rl for rl in original_receipt_letters if rl.line_id in line_ids],
    }
```

### Step 4: Calculate New Receipt Bounding Boxes

For each cluster, calculate the bounding box from the receipt words:

```python
def calculate_receipt_bounds(
    words: List[ReceiptWord],
    original_receipt: Receipt,
    image_width: int,
    image_height: int,
) -> Dict[str, Any]:
    """
    Calculate bounding box for a receipt from its words.

    ReceiptWord coordinates are normalized (0-1) relative to the original receipt.
    We need to convert to absolute image coordinates, then calculate new bounds.
    """
    # Get original receipt bounds in absolute image coordinates
    receipt_min_x = original_receipt.top_left["x"] * image_width
    receipt_max_x = original_receipt.top_right["x"] * image_width
    receipt_min_y = original_receipt.bottom_left["y"] * image_height  # Bottom in OCR space
    receipt_max_y = original_receipt.top_left["y"] * image_height  # Top in OCR space

    receipt_width = receipt_max_x - receipt_min_x
    receipt_height = receipt_max_y - receipt_min_y

    all_x_coords = []
    all_y_coords = []

    for word in words:
        # Convert from receipt-relative (0-1) to absolute image coordinates
        word_top_left_x = receipt_min_x + word.top_left["x"] * receipt_width
        word_top_left_y = receipt_min_y + word.top_left["y"] * receipt_height
        # ... repeat for all corners

        all_x_coords.extend([word_top_left_x, word_top_right_x, ...])
        all_y_coords.extend([word_top_left_y, word_top_right_y, ...])

    min_x = min(all_x_coords)
    max_x = max(all_x_coords)
    min_y = min(all_y_coords)  # Bottom in OCR space
    max_y = max(all_y_coords)  # Top in OCR space

    # Normalize back to image space (0-1)
    return {
        "top_left": {"x": min_x / image_width, "y": max_y / image_height},
        "top_right": {"x": max_x / image_width, "y": max_y / image_height},
        "bottom_left": {"x": min_x / image_width, "y": min_y / image_height},
        "bottom_right": {"x": max_x / image_width, "y": min_y / image_height},
    }
```

### Step 5: Transform Coordinates

This is the critical step. For each ReceiptLine, ReceiptWord, and ReceiptLetter:

**Step 5a: Transform from Original Receipt Space → Image Space**

```python
from receipt_upload.geometry.transformations import invert_warp
import copy

# Get transform coefficients from original receipt to image
transform_coeffs, orig_receipt_width, orig_receipt_height = original_receipt.get_transform_to_image(
    image_entity.width,
    image_entity.height,
)

# Invert to get forward transform (image -> receipt), then invert again to get receipt -> image
forward_coeffs = invert_warp(*transform_coeffs)  # This gives us receipt -> image

# Transform a ReceiptLine from original receipt space to image space
line_copy = copy.deepcopy(original_line)
line_copy.warp_transform(
    *forward_coeffs,
    src_width=image_entity.width,
    src_height=image_entity.height,
    dst_width=orig_receipt_width,
    dst_height=orig_receipt_height,
    flip_y=True,  # Receipt coords are in OCR space (y=0 at bottom), need to flip to PIL space
)

# After warp_transform, line_copy coordinates are normalized (0-1) in image space
# Convert to absolute image coordinates
line_tl_img = {
    "x": line_copy.top_left["x"] * image_entity.width,
    "y": line_copy.top_left["y"] * image_entity.height,
}
# ... repeat for all corners
```

**Step 5b: Transform from Image Space → New Receipt Space**

```python
# Calculate new receipt bounds in image coordinates
new_receipt_min_x_abs = bounds["top_left"]["x"] * image_entity.width
new_receipt_max_x_abs = bounds["top_right"]["x"] * image_entity.width
new_receipt_min_y_abs = bounds["bottom_left"]["y"] * image_entity.height  # Bottom in OCR space
new_receipt_max_y_abs = bounds["top_left"]["y"] * image_entity.height  # Top in OCR space
new_receipt_width_abs = new_receipt_max_x_abs - new_receipt_min_x_abs
new_receipt_height_abs = new_receipt_max_y_abs - new_receipt_min_y_abs

def img_to_receipt_coord(img_x, img_y):
    """
    Convert from image coordinates (absolute pixels, PIL space) to new receipt space.
    """
    # Convert from PIL space (y=0 at top) to OCR space (y=0 at bottom)
    ocr_y = image_entity.height - img_y

    # Normalize relative to new receipt bounds
    receipt_x = (img_x - new_receipt_min_x_abs) / new_receipt_width_abs if new_receipt_width_abs > 0 else 0.0
    receipt_y = (ocr_y - new_receipt_min_y_abs) / new_receipt_height_abs if new_receipt_height_abs > 0 else 0.0

    return receipt_x, receipt_y

# Transform line corners
line_top_left_x, line_top_left_y = img_to_receipt_coord(line_tl_img["x"], line_tl_img["y"])
line_top_right_x, line_top_right_y = img_to_receipt_coord(line_tr_img["x"], line_tr_img["y"])
# ... repeat for all corners
```

### Step 6: Create New Receipt Entities

```python
from datetime import datetime, timezone

# Create Receipt entity
new_receipt = Receipt(
    image_id=image_id,
    receipt_id=new_receipt_id,
    width=receipt_width,
    height=receipt_height,
    timestamp_added=datetime.now(timezone.utc),
    raw_s3_bucket=raw_bucket,
    raw_s3_key=f"raw/{image_id}_RECEIPT_{new_receipt_id:05d}.png",
    top_left=bounds["top_left"],
    top_right=bounds["top_right"],
    bottom_left=bounds["bottom_left"],
    bottom_right=bounds["bottom_right"],
    # ... other fields
)

# Create ReceiptLine entity with transformed coordinates
new_receipt_line = ReceiptLine(
    receipt_id=new_receipt_id,
    image_id=image_id,
    line_id=new_line_id,
    text=original_line.text,
    bounding_box={
        "x": line_min_x_receipt * new_receipt_width_abs,
        "y": line_min_y_receipt * new_receipt_height_abs,
        "width": (line_max_x_receipt - line_min_x_receipt) * new_receipt_width_abs,
        "height": (line_max_y_receipt - line_min_y_receipt) * new_receipt_height_abs,
    },
    top_left={"x": line_top_left_x, "y": line_top_left_y},
    top_right={"x": line_top_right_x, "y": line_top_right_y},
    bottom_left={"x": line_bottom_left_x, "y": line_bottom_left_y},
    bottom_right={"x": line_bottom_right_x, "y": line_bottom_right_y},
    angle_degrees=original_line.angle_degrees,
    angle_radians=original_line.angle_radians,
    confidence=original_line.confidence,
)

# Repeat for ReceiptWord and ReceiptLetter entities
```

## Complete Example

See `scripts/split_receipt.py` for a complete implementation of this process. The key function is `create_split_receipt_records()` which:

1. Filters receipt OCR data by cluster
2. Calculates new receipt bounds
3. Transforms coordinates from original receipt space → image space → new receipt space
4. Creates new Receipt, ReceiptLine, ReceiptWord, and ReceiptLetter entities

## Key Points

1. **Receipt OCR is more accurate** because it's run on the warped receipt image, which is cleaner and more focused
2. **No need to re-run OCR** - we reuse the existing ReceiptLine, ReceiptWord, ReceiptLetter data
3. **Two-step transformation**:
   - Original receipt space → Image space (using `Receipt.get_transform_to_image()`)
   - Image space → New receipt space (using bounding box normalization)
4. **Coordinate systems**:
   - Receipt coordinates: Normalized 0-1 relative to receipt, OCR space (y=0 at bottom)
   - Image coordinates: Normalized 0-1 relative to image, OCR space (y=0 at bottom)
   - PIL coordinates: Absolute pixels, PIL space (y=0 at top) - used internally for transforms

## Why This Works

The receipt OCR data already has accurate text recognition and coordinate information. By:
1. Using the new clustering to identify which lines belong to which receipt
2. Transforming coordinates to the new receipt's coordinate space
3. Preserving all the OCR data (text, confidence, angles, etc.)

We get the benefits of:
- More accurate OCR (from receipt-level OCR)
- Correct clustering (from new algorithm)
- Proper coordinate normalization (relative to each receipt's bounding box)

