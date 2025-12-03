# Visualize Final Clusters Cropped - Correct Implementation

## Overview

The script `scripts/visualize_final_clusters_cropped.py` is the **correct reference implementation** for visualizing clustered receipts with affine-transformed cropped images.

**Status**: ✅ **CORRECT** - This script produces correct clustering (2 clusters) and correctly aligned bounding boxes.

**Key Point**: This script uses image-level OCR data directly, which ensures:
- All lines are included (no missing lines)
- Correct clustering results
- Correct coordinate transformations
- Correctly aligned bounding boxes

## Key Characteristics

### ✅ Correct Clustering Logic
- Uses **image-level `Line` entities** directly from DynamoDB
- Applies `recluster_receipt_lines()` to get correct cluster assignments
- Includes **ALL lines** (156 for `13da1048-3888-429f-b2aa-b3e15341da5e`, 88 for `752cf8e2-cb69-4643-8f28-0ac9e3d2df25`)
- Produces correct 2-cluster results

### ✅ Correct Visualization Logic
- Uses **image-level `Line` entities** directly for drawing bounding boxes
- Line coordinates are already in **image coordinate space** (normalized 0-1, OCR space with y=0 at bottom)
- Transforms line corners from image space to warped receipt space using inverse affine transform
- Uses **all clustered lines** for calculating affine transform bounds

### ✅ Correct Affine Transformation
- Calculates receipt bounds using `min_area_rect` on all line corner points
- Uses `box_points` and `reorder_box_points` to get ordered corners
- Builds affine transform matrix (same as `scan.py` in receipt_upload)
- Warps image using `PIL.Image.transform()` with `AFFINE` mode
- Transforms line coordinates from image space to warped receipt space for visualization

## Data Flow

```
1. Load image-level Line entities (from DynamoDB)
   └─> Coordinates: Image space (normalized 0-1, OCR space, y=0 at bottom)

2. Cluster lines using recluster_receipt_lines()
   └─> Returns: cluster_id -> List[Line] mapping

3. For each cluster:
   a. Calculate bounds from all line corners
      └─> Uses min_area_rect() on all corner points
      └─> Gets affine transform coefficients

   b. Warp original image using affine transform
      └─> Creates perspective-corrected receipt image

   c. Transform line corners from image space to warped receipt space
      └─> Uses inverse affine transform
      └─> Draws bounding boxes on warped image
```

## Why This Is Correct

1. **Uses all lines for clustering**: Ensures correct cluster assignments (2 clusters, not 6)
2. **Uses all lines for bounds calculation**: Ensures correct affine transform that includes all clustered content
3. **Direct coordinate usage**: Image-level Line entities are already in the correct coordinate space
4. **No coordinate transformation errors**: Avoids issues with receipt-to-image coordinate conversion

## Coordinate Systems

### Image-Level Line Coordinates
- **Space**: Image coordinate space
- **Normalization**: 0-1 relative to image dimensions
- **Y-axis**: OCR space (y=0 at bottom, y=1 at top)
- **Usage**: Direct use in clustering and visualization

### Warped Receipt Coordinates
- **Space**: Warped receipt coordinate space
- **Normalization**: 0-1 relative to warped receipt dimensions
- **Y-axis**: PIL space (y=0 at top, y=1 at bottom)
- **Usage**: For drawing bounding boxes on warped image

## Example Usage

```bash
python scripts/visualize_final_clusters_cropped.py \
  --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
  --output-dir ./final_clusters_cropped_13da
```

## Output

- Creates perspective-corrected (affine-transformed) receipt images
- Shows bounding boxes aligned correctly with receipt text
- Produces correct 2-cluster results for both test images

## Notes

- This script uses **image-level OCR data**, which is less accurate than receipt-level OCR
- However, it produces **correct clustering and visualization** because:
  - It uses all lines (no missing lines)
  - It avoids coordinate transformation errors
  - It uses the same coordinate space throughout
  - The bounding boxes align correctly with the receipt text

## Why This Is The Reference Implementation

1. **Proven Correct**: Produces correct 2-cluster results for both test images
2. **Correct Alignment**: Bounding boxes align correctly with receipt text
3. **Complete Data**: Uses all available lines (no missing lines)
4. **Simple Logic**: Direct use of image-level coordinates avoids transformation errors
5. **Same Business Logic**: Uses the same clustering logic (`recluster_receipt_lines`) that works correctly

## Comparison with Other Scripts

### `visualize_final_clusters_from_receipt_ocr.py`
- **Goal**: Use receipt-level OCR (more accurate) for visualization
- **Issue**: Coordinate transformation from receipt space to image space can introduce errors
- **Status**: ⚠️ Work in progress - coordinate transformation needs to match the correct script

### `visualize_final_clusters_cropped.py` (this script)
- **Goal**: Visualize clustered receipts with correct alignment
- **Status**: ✅ **CORRECT** - Use this as the reference implementation

