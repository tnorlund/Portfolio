# Split Receipt Implementation

## Overview

The `scripts/split_receipt.py` script implements the complete flow for splitting a single receipt into multiple receipts based on re-clustering, with proper handling of embeddings and labels.

## Implementation Status

✅ **Fully Implemented**

## Flow

### 1. Load and Re-cluster
- Loads original receipt data from DynamoDB
- Loads image-level OCR lines for re-clustering
- Applies two-phase clustering (angle-based splitting + smart merging)
- Creates new receipt clusters

### 2. Create New Receipt Records
- Creates new `Receipt`, `ReceiptLine`, `ReceiptWord` entities
- Recalculates coordinates relative to new receipt bounding boxes
- Migrates `ReceiptWordLabel` entities (mapped to new IDs)
- Saves all records locally first (for rollback)

### 3. Save to DynamoDB (Receipts, Lines, Words)
- Saves new receipts to DynamoDB
- Saves receipt lines and words
- **Does NOT save labels yet** (waiting for compaction)

### 4. Export NDJSON and Queue
- Exports receipt lines/words as NDJSON to S3
- Queues NDJSON for embedding worker
- NDJSON worker will:
  - Create embeddings in realtime
  - Upload deltas to S3
  - Create CompactionRun in DynamoDB

### 5. Wait for Compaction
- Polls for CompactionRun completion
- Waits for both `lines` and `words` collections to be `COMPLETED`
- Handles timeouts and failures gracefully
- Maximum wait: 5 minutes (configurable)

### 6. Add Labels
- Adds `ReceiptWordLabel` entities to DynamoDB
- Labels will update ChromaDB metadata via streams
- Only adds labels after compaction completes (or if embedding was skipped)

## Key Functions

### `wait_for_compaction_complete()`
- Polls DynamoDB for CompactionRun status
- Waits for both collections to be COMPLETED
- Handles initial wait for CompactionRun to be created
- Returns run_id on success
- Raises TimeoutError or RuntimeError on failure

### `export_receipt_ndjson_and_queue()`
- Fetches receipt lines/words from DynamoDB
- Serializes to NDJSON format
- Uploads to S3
- Queues message to SQS for embedding worker

### `create_split_receipt_records()`
- Creates new Receipt entity with recalculated bounding box
- Creates ReceiptLine entities with normalized coordinates
- Creates ReceiptWord entities with normalized coordinates
- Handles coordinate transformation (receipt-relative → image-absolute → new receipt-relative)

### `migrate_receipt_word_labels()`
- Maps old line_id/word_id to new IDs
- Creates new ReceiptWordLabel entities
- Preserves label metadata (validation_status, reasoning, etc.)

## Usage

### Basic Usage (Dry Run)
```bash
python scripts/split_receipt.py \
    --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
    --original-receipt-id 1 \
    --dry-run
```

### Full Run (with Embedding)
```bash
python scripts/split_receipt.py \
    --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
    --original-receipt-id 1 \
    --artifacts-bucket my-artifacts-bucket \
    --embed-ndjson-queue-url https://sqs.us-east-1.amazonaws.com/123456789012/embed-ndjson-queue \
    --output-dir ./local_receipt_splits
```

### Skip Embedding
```bash
python scripts/split_receipt.py \
    --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
    --original-receipt-id 1 \
    --skip-embedding
```

## Configuration

### Environment Variables
- `DYNAMODB_TABLE_NAME` - DynamoDB table name (or uses Pulumi config)
- `AWS_REGION` - AWS region (or uses Pulumi config)

### Arguments
- `--image-id` - Image ID to process (required)
- `--original-receipt-id` - Original receipt ID to split (default: 1)
- `--output-dir` - Directory for local records (default: `./local_receipt_splits`)
- `--artifacts-bucket` - S3 bucket for NDJSON artifacts
- `--embed-ndjson-queue-url` - SQS queue URL for embedding jobs
- `--raw-bucket` - S3 bucket for raw images (optional, uses original receipt's bucket)
- `--site-bucket` - S3 bucket for site images (optional, uses original receipt's bucket)
- `--dry-run` - Don't save to DynamoDB (saves locally only)
- `--skip-embedding` - Skip NDJSON export and embedding queue

## Safety Features

1. **Local Save First**: All records saved locally before DynamoDB
2. **Original Receipt Preserved**: Original receipt kept for rollback
3. **Dry Run Mode**: Test without making changes
4. **Error Handling**: Graceful handling of timeouts and failures
5. **Coordinate Validation**: Ensures coordinates are normalized correctly

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
    │   ├── labels.json
    │   └── id_mappings.json
    └── receipt_00002/
        └── ...
```

### DynamoDB
- New `Receipt` entities
- New `ReceiptLine` entities
- New `ReceiptWord` entities
- New `ReceiptWordLabel` entities (after compaction)
- `CompactionRun` entity (created by NDJSON worker)

## Order of Operations (Verified Safe)

1. ✅ Add Receipt, ReceiptLine, ReceiptWord to DynamoDB
2. ✅ Export NDJSON and queue (creates CompactionRun)
3. ✅ Wait for CompactionRun to complete
4. ✅ Add ReceiptWordLabel entities

This order ensures:
- Embeddings exist in ChromaDB before labels are added
- Labels successfully update ChromaDB metadata
- No race conditions with enhanced compactor

## Testing

### Dry Run Test
```bash
python scripts/split_receipt.py \
    --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
    --original-receipt-id 1 \
    --dry-run
```

### Full Test (with Embedding)
```bash
python scripts/split_receipt.py \
    --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
    --original-receipt-id 1 \
    --artifacts-bucket my-artifacts-bucket \
    --embed-ndjson-queue-url https://sqs.us-east-1.amazonaws.com/123456789012/embed-ndjson-queue
```

## Error Handling

### Compaction Timeout
- If compaction doesn't complete within 5 minutes:
  - Logs warning
  - Still adds labels (they'll update when compaction completes later)
  - Continues with next receipt

### Compaction Failure
- If compaction fails:
  - Logs error
  - Still adds labels (they'll update when compaction retries)
  - Continues with next receipt

### Missing CompactionRun
- If CompactionRun not found after initial wait:
  - Logs warning
  - Continues polling
  - Times out after max_wait_seconds

## Future Enhancements

1. **Parallel Processing**: Process multiple receipts in parallel
2. **Retry Logic**: Retry failed compactions
3. **Progress Tracking**: Better progress indicators
4. **Rollback Support**: Automatic rollback on failure
5. **Validation**: Validate split results before saving


