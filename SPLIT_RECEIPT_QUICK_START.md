# Split Receipt Script - Quick Start Guide

## Overview

The **`scripts/split_receipt.py`** script is the working script that:
1. Takes one receipt image
2. Re-clusters the lines using hard-coded rules (two-phase clustering)
3. Creates separate receipt images with proper bounding boxes for each split
4. Saves new receipt records to DynamoDB

## Key Function: `create_split_receipt_image()`

This function (line 349) is what creates the individual receipt images:
- Takes the original full image
- Calculates bounding boxes from word coordinates
- Crops the image to create separate receipt images
- Returns PIL Image objects for each split receipt

## Usage

### Basic Dry Run (Test Without Saving)
```bash
python scripts/split_receipt.py \
    --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
    --original-receipt-id 1 \
    --dry-run
```

### Full Run (Creates Images and Saves to DynamoDB)
```bash
python scripts/split_receipt.py \
    --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
    --original-receipt-id 1 \
    --raw-bucket YOUR_RAW_BUCKET \
    --site-bucket YOUR_SITE_BUCKET \
    --output-dir ./local_receipt_splits
```

### Skip Embedding (Just Create Images and Records)
```bash
python scripts/split_receipt.py \
    --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
    --original-receipt-id 1 \
    --skip-embedding \
    --raw-bucket YOUR_RAW_BUCKET \
    --site-bucket YOUR_SITE_BUCKET
```

## What It Does

1. **Loads Data**: Original receipt, lines, words from DynamoDB
2. **Re-clusters**: Uses two-phase clustering (same as upload process)
3. **Creates Images**: `create_split_receipt_image()` crops each receipt from original
4. **Uploads Images**: Saves cropped images to S3 (raw bucket and CDN)
5. **Creates Records**: New Receipt, ReceiptLine, ReceiptWord entities
6. **Saves Locally**: All records saved to `local_receipt_splits/` for rollback
7. **Saves to DynamoDB**: (if not dry-run)

## Image Creation Process

The `create_split_receipt_image()` function:
- Calculates bounding box from all words in the cluster
- Converts coordinates from OCR space to image space
- Crops the original image using PIL
- Returns a cropped PIL Image for each split receipt

These images are then:
- Uploaded to raw S3 bucket
- Processed into all CDN formats (JPEG, WebP, AVIF, thumbnails)
- Uploaded to site/CDN bucket
- Referenced in the Receipt entity

## Requirements

- PIL/Pillow (for image processing) ✅ Installed
- receipt_dynamo (DynamoDB client)
- receipt_upload (clustering and transforms)
- AWS credentials configured
- DynamoDB table name (or Pulumi config)

## Output

### Local Files
```
local_receipt_splits/
└── {image_id}/
    ├── original_receipt.json
    ├── receipt_00001/
    │   ├── receipt.json
    │   ├── lines.json
    │   ├── words.json
    │   └── id_mappings.json
    └── receipt_00002/
        └── ...
```

### S3 Images
- Raw images: `s3://{raw_bucket}/raw/{image_id}_RECEIPT_{receipt_id:05d}.png`
- CDN images: `s3://{site_bucket}/assets/{image_id}_RECEIPT_{receipt_id:05d}.*`

## Related Scripts

- `scripts/visualize_split_receipts_with_boxes.py` - Visualizes split receipts with bounding boxes drawn on original image
- `scripts/rollback_split_receipt.py` - Rollback script if needed

## Documentation

- `docs/SPLIT_RECEIPT_IMPLEMENTATION.md` - Full implementation details
- `docs/SPLIT_RECEIPT_CAPABILITIES.md` - What the script can do
- `docs/SPLIT_RECEIPTS_STRATEGY.md` - Strategy and approach

