# Testing New Embedding Format

## Overview

This document explains how to test the new embedding format on the `feat/word-embedding-format-update` branch. The new format uses a simpler context-only approach with on-the-fly embedding support.

## Background

The new embedding format:
- Replaces XML-style format with simple space-separated format
- Uses configurable context size (default: 2 words on each side)
- Uses multiple `<EDGE>` tags for better edge detection
- Adds agent tools for on-the-fly embedding and search

**Breaking Change**: New embeddings will use different format, but queries remain compatible with existing embeddings.

## Testing Workflow

### Step 1: Clean Up Environment

Run the cleanup script to prepare the environment for re-embedding:

```bash
# Dry run first to see what will be done
python scripts/dev.cleanup_for_word_ingest_testing.py \
  --env dev \
  --dry-run

# Actual cleanup (this will):
# 1. Delete all WORD_EMBEDDING batch summaries
# 2. Reset all words' embedding_status to NONE
# 3. Clean S3 snapshots (words and lines)
# 4. Remove all compaction locks
python scripts/dev.cleanup_for_word_ingest_testing.py \
  --env dev \
  --delete-batches \
  --batch-type WORD_EMBEDDING
```

**Options**:
- `--delete-batches`: Delete batch summaries instead of resetting to PENDING
- `--skip-words`: Skip resetting word embedding statuses (not recommended)
- `--skip-s3`: Skip S3 snapshot cleanup
- `--skip-locks`: Skip lock removal
- `--skip-export`: Skip exporting batch summaries before deletion (not recommended)

### Step 2: Submit Words for Embedding

After cleanup, run the submit word step function to resubmit words with the new embedding format:

```bash
# Trigger the submit word step function
# This will create new batch summaries with PENDING status
# and set words' embedding_status to PENDING
```

The submit step function will:
1. Query all words with `embedding_status = NONE`
2. Format words using the new embedding format
3. Submit batches to OpenAI
4. Create batch summaries with `PENDING` status
5. Update words' `embedding_status` to `PENDING`

### Step 3: Ingest Word Embeddings

After submission, run the ingest word step function to process the embeddings:

```bash
# Trigger the ingest word step function
# This will poll OpenAI batches and save embeddings to ChromaDB
```

The ingest step function will:
1. Poll OpenAI batch status
2. Download completed embeddings
3. Save embeddings to ChromaDB as deltas
4. Update words' `embedding_status` to `SUCCESS`
5. Update batch summaries to `COMPLETED`

## What Gets Reset

### Batch Summaries
- **Deleted** (if `--delete-batches` is used): All `WORD_EMBEDDING` batch summaries are removed
- **Reset** (default): All batch summaries are set to `PENDING` status

### Word Embedding Statuses
- All words with status `PENDING`, `SUCCESS`, `FAILED`, or `NOISE` are reset to `NONE`
- This allows words to be re-embedded with the new format

### S3 Snapshots
- All ChromaDB snapshots for `words` and `lines` collections are deleted
- This ensures fresh snapshots are created after re-embedding

### Compaction Locks
- All compaction locks are removed
- This allows compaction to run on the new embeddings

## Safety Features

### Dry Run Mode
Always run with `--dry-run` first to see what will be changed:

```bash
python scripts/dev.cleanup_for_word_ingest_testing.py --env dev --dry-run
```

### Export Before Deletion
By default, batch summaries are exported to a local NDJSON file before deletion:

```
dev.batch_summaries_word_embedding_YYYYMMDD_HHMMSS.ndjson
```

This allows you to restore batch summaries if needed.

### Incremental Operations
- Batch summaries are processed in chunks (25 at a time)
- Words are processed by status type
- Progress is logged throughout

## Background Process Safety

**Important**: The cleanup script does NOT affect the running background process (`test_all_needs_review_merchant_names.py`) because:

1. The background process uses **existing embeddings** from ChromaDB (downloaded from S3)
2. The cleanup script only resets **DynamoDB** statuses and deletes **S3 snapshots**
3. The background process doesn't create new embeddings - it only queries existing ones
4. The commit message states: "queries remain compatible" - existing embeddings can still be queried

However, after re-embedding:
- New embeddings will use the new format
- The background process will continue to work with both old and new embeddings
- Similarity search will work across both formats

## Verification

After cleanup and re-embedding, verify:

1. **Batch Summaries**: Check that new batch summaries are created with `PENDING` status
2. **Word Statuses**: Check that words are being processed (status changes from `NONE` → `PENDING` → `SUCCESS`)
3. **ChromaDB**: Verify that new embeddings are stored in ChromaDB
4. **S3 Snapshots**: Verify that new snapshots are created

## Troubleshooting

### Batch Summaries Not Deleted
- Check that `--delete-batches` flag is used
- Verify batch type matches (`--batch-type WORD_EMBEDDING`)

### Words Not Resetting
- Check that `--skip-words` is NOT used
- Verify words have non-NONE status before reset

### S3 Cleanup Fails
- Check S3 bucket permissions
- Verify bucket name is correct in Pulumi config

### Locks Not Removed
- Check that `--skip-locks` is NOT used
- Verify DynamoDB permissions

## Next Steps

After cleanup:
1. Run submit word step function
2. Monitor batch summaries (should show `PENDING` status)
3. Run ingest word step function
4. Monitor word embedding statuses (should change to `SUCCESS`)
5. Verify ChromaDB has new embeddings
6. Test similarity search with new embeddings

