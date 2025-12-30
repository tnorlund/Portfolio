# Data Migration: Dev to Prod

## Overview

This document describes the process for migrating data from the dev DynamoDB table to prod, including considerations for the enhanced compactor and embedding pipeline.

## Key Findings

### Stream Handler Behavior

The DynamoDB stream handler processes events differently based on entity type and event type:

| Entity | INSERT | MODIFY | REMOVE | Notes |
|--------|--------|--------|--------|-------|
| `ReceiptWord` | Ignored | Ignored | Ignored | Embeddings created by step function based on `embedding_status` |
| `ReceiptLine` | Ignored | Ignored | Ignored | Embeddings created by step function based on `embedding_status` |
| `ReceiptPlace` | Ignored | Triggers | Triggers | Context data for embeddings; MODIFY/REMOVE updates embeddings |
| `ReceiptWordLabel` | Ignored | Triggers | Triggers | Training labels; MODIFY/REMOVE updates embeddings |
| `CompactionRun` | Triggers | Triggers | Ignored | INSERT triggers compaction of deltas |

**Key Insight**: INSERT events for `ReceiptPlace` and `ReceiptWordLabel` are intentionally ignored. Initial embeddings come from the step function, not from stream events.

### Embedding Flow

```
┌─────────────────────────────────────────────────────────────────────┐
│                    INITIAL EMBEDDING FLOW                           │
├─────────────────────────────────────────────────────────────────────┤
│  1. OCR Processing creates ReceiptLine/ReceiptWord                  │
│     with embedding_status=NONE                                       │
│  2. Unified Embedding Step Function finds NONE records              │
│  3. Creates embeddings and uploads deltas to S3                     │
│  4. Creates COMPACTION_RUN in DynamoDB                              │
│  5. Stream handler sees COMPACTION_RUN INSERT → triggers compaction │
│  6. Compaction merges deltas into ChromaDB snapshot                 │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│                    UPDATE EMBEDDING FLOW                            │
├─────────────────────────────────────────────────────────────────────┤
│  When ReceiptPlace or ReceiptWordLabel is MODIFIED:                 │
│  1. Stream handler detects relevant field changes                   │
│  2. Sends message to SQS → triggers re-embedding                    │
│  3. New deltas created → compacted into snapshot                    │
└─────────────────────────────────────────────────────────────────────┘
```

## Migration Script

### Location
```
scripts/copy_dynamodb_dev_to_prod.py
```

### What It Does

1. Reads exported JSON files from `dev.export/` directory
2. Checks if each image already exists in prod (skip if `--skip-existing`)
3. Updates S3 bucket names (dev → prod) for Image and Receipt entities
4. **Resets `embedding_status` to `NONE`** for ReceiptLine and ReceiptWord
5. Copies all related entities in batches

### What It Handles Correctly

| Concern | Handling |
|---------|----------|
| S3 bucket names | Updates `raw_s3_bucket` and `cdn_s3_bucket` |
| Embedding status | Resets to `NONE` (triggers re-embedding) |
| Batch limits | Chunks writes to 25 items (DynamoDB limit) |
| Parallelism | Uses ThreadPoolExecutor with 10 workers |
| Idempotency | `--skip-existing` flag avoids duplicates |

### What Is NOT Migrated (by design)

- `CompactionRun` - Compactor will regenerate in prod
- `CompactionLock` - TTL-based, will auto-expire
- `EmbeddingBatchResult` - Intermediate processing state
- `Job` / `JobMetric` - Training job records (SageMaker-specific)

## Migration Workflow

### Step 1: Export from Dev

```bash
# Export specific images
python receipt_dynamo/receipt_dynamo/data/export_image.py <image_id>

# Or export samples
python scripts/export_receipt_data.py --count 100
```

### Step 2: Dry Run

```bash
python scripts/copy_dynamodb_dev_to_prod.py --dry-run
```

Expected output:
```
Mode: DRY RUN
No records will be copied. Use --no-dry-run to actually copy.
...
Entity counts:
  images: 50
  receipts: 50
  lines: 2,340
  words: 15,678
  receipt_lines: 2,340
  receipt_words: 15,678
  receipt_word_labels: 8,456
  ...
```

### Step 3: Execute Migration

```bash
python scripts/copy_dynamodb_dev_to_prod.py --no-dry-run --skip-existing
```

### Step 4: Post-Migration - Trigger Embeddings

**IMPORTANT**: The embedding step functions are NOT automatically scheduled. After migration, you must manually trigger them to process the migrated records.

#### Option A: Trigger via AWS Console
1. Go to AWS Step Functions
2. Find `embedding-infra-prod-line-workflow` and `embedding-infra-prod-word-workflow`
3. Start execution with empty input `{}`

#### Option B: Trigger via CLI
```bash
# Trigger line embedding workflow
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:us-east-1:ACCOUNT:stateMachine:embedding-infra-prod-line-workflow \
  --input '{}'

# Trigger word embedding workflow
aws stepfunctions start-execution \
  --state-machine-arn arn:aws:states:us-east-1:ACCOUNT:stateMachine:embedding-infra-prod-word-workflow \
  --input '{}'
```

### Step 5: Verify

```bash
# Check embedding status distribution
aws dynamodb scan \
  --table-name ReceiptsTable-prod \
  --filter-expression "begins_with(SK, :sk) AND embedding_status = :status" \
  --expression-attribute-values '{":sk":{"S":"RECEIPT#"},":status":{"S":"NONE"}}' \
  --select "COUNT"

# Should show decreasing count as step functions process records
```

## Potential Issues

### Issue 1: Large Volume Overwhelming Step Functions

**Risk**: Migrating thousands of records at once with `embedding_status=NONE` could overwhelm the step functions.

**Mitigation**:
- Migrate in batches (e.g., 100 images at a time)
- Monitor step function executions
- Step functions have built-in retry logic

### Issue 2: ChromaDB Snapshot Size

**Risk**: Large migrations may cause snapshot upload timeouts.

**Mitigation**:
- Compaction Lambda has 14-minute timeout
- Snapshots are uploaded incrementally
- Monitor Lambda logs for errors

### Issue 3: Missing Context Data

**Risk**: If `ReceiptPlace` is missing when embeddings are created, semantic search quality may suffer.

**Mitigation**:
- Ensure `ReceiptPlace` records are migrated BEFORE triggering embedding step functions
- The migration script already handles this order correctly

## Files Reference

| File | Purpose |
|------|---------|
| `scripts/copy_dynamodb_dev_to_prod.py` | Primary migration script |
| `receipt_dynamo/receipt_dynamo/data/export_image.py` | Export entities for an image |
| `infra/chromadb_compaction/lambdas/stream_processor.py` | DynamoDB stream handler |
| `infra/embedding_step_functions/unified_embedding/handlers/find_unembedded.py` | Finds records with `embedding_status=NONE` |
| `receipt_dynamo_stream/receipt_dynamo_stream/parsing/parsers.py` | Entity type detection logic |

## Summary

The current migration script is **correctly designed** for the enhanced compactor:

1. It resets `embedding_status=NONE` which triggers the step function to create embeddings
2. It does NOT migrate `CompactionRun` records (compactor regenerates these)
3. INSERT events for `ReceiptPlace`/`ReceiptWordLabel` being ignored is intentional

**The only manual step required** is triggering the embedding step functions after migration to process the `embedding_status=NONE` records.
