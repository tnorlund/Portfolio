# Combine Receipts Step Function

AWS Step Function infrastructure for combining multiple receipts into single receipts based on LLM analysis.

## Overview

This Step Function processes images that contain multiple receipts that should be combined. It:

1. **Lists images** with LLM-recommended combinations (from analysis JSON or DynamoDB query)
2. **Combines receipts** in parallel (container Lambda with image processing)
3. **Creates embeddings** and ChromaDB deltas for the new combined receipt
4. **Aggregates results** and generates summary reports

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│              Combine Receipts Step Function                 │
└─────────────────────────────────────────────────────────────┘
                            │
        ┌───────────────────┼───────────────────┐
        ▼                   ▼                   ▼
  ListImages        CombineReceipts      AggregateResults
  (Zip Lambda)      (Container Lambda)  (Zip Lambda)
        │                   │                   │
        │                   │                   │
        ▼                   ▼                   ▼
   DynamoDB          Image Processing      S3 Results
   S3 Analysis       Embeddings            Summary
                     ChromaDB Deltas
```

## Components

### Lambda Functions

1. **list_images** (Zip Lambda)
   - Queries DynamoDB or loads from LLM analysis JSON in S3
   - Filters to images where LLM recommended combining
   - Returns list of images with receipt IDs to combine

2. **combine_receipts** (Container Lambda)
   - Fetches receipt data from DynamoDB
   - Combines words, lines, and letters from specified receipts
   - Creates new receipt image (cropped/combined)
   - Migrates labels to new receipt
   - Creates ReceiptMetadata
   - Creates embeddings and ChromaDB deltas
   - Saves to DynamoDB (unless dry_run)

3. **aggregate_results** (Zip Lambda)
   - Aggregates results from all combine operations
   - Generates summary report
   - Saves to S3

### Step Function Workflow

```
Initialize
  ↓
ListImages
  ↓
HasImages? (Choice)
  ├─ Yes → CombineReceipts (Map, MaxConcurrency: 5)
  │         └─ CombineReceipt (per image)
  │             └─ Create embeddings & ChromaDB deltas
  └─ No → NoImagesToProcess (End)
  ↓
AggregateResults
  ↓
Done
```

## Usage

### Input Format

```json
{
  "dry_run": true,
  "llm_analysis_s3_key": "s3://bucket/path/to/analysis.json",
  "limit": 10
}
```

### Output Format

```json
{
  "summary": {
    "execution_id": "abc123",
    "total_images": 3,
    "successful": 2,
    "failed": 1,
    "dry_run": true,
    "successful_images": [
      {
        "image_id": "image-uuid",
        "new_receipt_id": 4,
        "original_receipt_ids": [1, 2],
        "compaction_run_id": "run-uuid"
      }
    ]
  },
  "results_s3_key": "results/abc123/summary.json"
}
```

## Configuration

- **max_concurrency**: Number of images to process in parallel (default: 5)
- **dry_run**: If true, creates records but doesn't save to DynamoDB (default: true)

## Dependencies

- DynamoDB table with receipt data
- S3 buckets: batch bucket, ChromaDB bucket, raw image bucket, site bucket
- OpenAI API key (for embeddings)
- Ollama API key (for LLM analysis)
- LangSmith API key (optional, for tracing)

## Deployment

The Step Function is deployed via Pulumi as part of the main infrastructure stack.

```bash
pulumi up
```

## Testing

To test locally, use the `dev.combine_receipts.py` script:

```bash
python dev.combine_receipts.py \
  --image-id <image_id> \
  --receipt-ids 1,2 \
  --dry-run
```

