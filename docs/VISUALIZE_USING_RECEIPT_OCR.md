# Using Receipt-Level OCR in visualize_final_clusters_cropped.py

## Overview

The `visualize_final_clusters_cropped.py` script has been updated to support using receipt-level OCR entities (`ReceiptLine`) instead of image-level OCR entities (`Line`) for clustering. This provides more accurate clustering results because receipt-level OCR has been perspective-corrected and is more accurate.

## Usage

### Option 1: Use Receipt-Level OCR (Recommended)

```bash
python scripts/visualize_final_clusters_cropped.py \
    --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
    --receipt-id 1 \
    --output-dir split_validation_13da_ocr_export \
    --raw-bucket raw-image-bucket-c779c32
```

**Benefits:**
- More accurate clustering (uses perspective-corrected OCR)
- Consistent with the data that will be split into new receipts
- Better cluster boundaries

### Option 2: Use Image-Level OCR (Fallback)

```bash
python scripts/visualize_final_clusters_cropped.py \
    --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
    --output-dir split_validation_13da_ocr_export \
    --raw-bucket raw-image-bucket-c779c32
```

**When to use:**
- When you don't have a receipt_id yet
- For debugging image-level OCR issues
- When receipt-level OCR is not available

## How It Works

### With Receipt-Level OCR (`--receipt-id` provided):

1. **Load ReceiptLine entities** from DynamoDB
2. **Convert to Line entities** in image space using `transform_receipt_line_to_image_line()`
   - Uses `receipt.get_transform_to_image()` to transform from receipt-relative space to image space
   - Handles perspective correction automatically
3. **Cluster** using the converted Line entities
4. **Visualize** with accurate bounding boxes

### With Image-Level OCR (no `--receipt-id`):

1. **Load Line entities** directly from DynamoDB
2. **Cluster** using image-level OCR
3. **Visualize** with image-level bounding boxes

## Coordinate Transformation

The script uses the same transformation logic as `split_receipt.py`:

1. **ReceiptLine → Image Space**: Uses `receipt.get_transform_to_image()` with perspective transform
2. **Image Space → Warped Receipt Space**: Uses inverse affine transform for visualization

## Comparison with split_receipt.py

| Aspect | `visualize_final_clusters_cropped.py` | `split_receipt.py` |
|--------|--------------------------------------|-------------------|
| **Clustering Input** | ReceiptLine (if `--receipt-id`) or Line | Line (currently) |
| **Data Source** | ReceiptLine entities (more accurate) | Image-level Line entities |
| **Purpose** | Visualization/debugging | Production split |
| **Output** | PNG + JSON | DynamoDB records + S3 images |

## Next Steps

To use receipt-level OCR in `split_receipt.py`:

1. Convert `ReceiptLine` entities to `Line` entities before clustering
2. Use the same `transform_receipt_line_to_image_line()` function
3. Keep existing coordinate transformation logic (already handles receipt → image → new receipt)

See `docs/COMPARISON_VISUALIZE_VS_SPLIT_RECEIPT.md` for detailed step-by-step comparison.

