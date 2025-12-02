# Split Receipt Script - Upload Process Alignment

## Overview

The split receipt script now aligns with the upload process workflow for consistency and audit trail purposes.

## What We've Added

### ✅ NDJSON Export to S3

**Added**: `export_receipt_ndjson_to_s3()` function that:
- Exports `ReceiptLine` and `ReceiptWord` entities to NDJSON files
- Uploads to S3 artifacts bucket: `receipts/{image_id}/receipt-{receipt_id:05d}/lines.ndjson` and `words.ndjson`
- Matches the exact pattern from `process_ocr_results.py::_export_receipt_ndjson_and_queue()`

**Why**:
- Consistency with upload workflow
- Audit trail for debugging
- Allows re-processing via queue if needed later

**When**: NDJSON files are exported **after** saving to DynamoDB but **before** creating embeddings.

## What We're NOT Doing (And Why)

### ❌ CDN Image Upload

**Not Added**: Uploading receipt images to CDN (site bucket)

**Why Not**:
- **Splitting is data-only**: We're re-clustering existing lines/words, not creating new receipt images
- **Original image already in CDN**: The original receipt image is already uploaded during initial processing
- **No new visual content**: We're not cropping/warping new receipt regions - just reorganizing existing OCR data

**When Would We Need It?**:
- If we were creating new receipt images (like `combine_receipts` does when merging)
- If we were cropping individual receipt regions from the original image
- For scans, the original image is already in CDN; individual receipt images are only created during initial processing

### ❌ Queue-Based Embedding

**Not Using**: SQS queue for embedding processing

**Why Not**:
- **Direct embedding is simpler**: We create embeddings immediately and have full control
- **Matches combine_receipts**: The `combine_receipts` logic also uses direct embedding
- **No queue dependency**: Removes infrastructure dependency
- **Faster**: No queue processing delay

**Note**: NDJSON files are still exported for consistency, even though we don't use the queue.

## Current Workflow

```
1. Load original receipt data from DynamoDB
2. Re-cluster using two-phase approach
3. Create new receipt records (save locally first)
4. Migrate ReceiptWordLabels
5. Save to DynamoDB (receipts, lines, words)
6. ✅ Export NDJSON files to S3 (NEW - matches upload process)
7. Create embeddings directly (realtime)
8. Create CompactionRun (triggers compaction via streams)
9. Wait for compaction to complete
10. Add labels (after embeddings exist)
```

## Comparison with Upload Process

| Step | Upload Process | Split Script | Status |
|------|---------------|--------------|--------|
| Save to DynamoDB | ✅ | ✅ | ✅ Matches |
| Export NDJSON to S3 | ✅ | ✅ | ✅ **Now matches** |
| Queue NDJSON for embedding | ✅ | ❌ | ⚠️ Direct embedding instead |
| Upload receipt images to CDN | ✅ | ❌ | ⚠️ Not needed (data-only split) |
| Create embeddings | ✅ (via queue) | ✅ (direct) | ⚠️ Different method, same result |
| Create CompactionRun | ✅ (via queue) | ✅ (direct) | ⚠️ Different method, same result |
| Add labels | ✅ | ✅ | ✅ Matches |

## Configuration

The script now loads `artifacts_bucket_name` from Pulumi (same as upload process):
- Pulumi export: `artifacts_bucket_name`
- Environment variable: `ARTIFACTS_BUCKET`
- Command line: `--artifacts-bucket`

## Future Considerations

If we ever need to:
1. **Create new receipt images**: We would need to add CDN upload logic (like `combine_receipts` does)
2. **Use queue-based embedding**: We could switch to queue processing, but NDJSON files are already exported
3. **Crop receipt regions**: We would need image processing logic (warp/crop) and CDN upload

For now, the split script focuses on **data reorganization** rather than **image processing**.

