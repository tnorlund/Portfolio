# Queue Configuration Fix - Summary of Changes

## Problem

The worker was incorrectly consuming from all three queues (`EMBED_NDJSON_QUEUE`, `LINES_QUEUE`, `WORDS_QUEUE`), causing it to receive `COMPACTION_RUN` messages that it couldn't process.

## Root Cause

- **Worker code** (`worker.py`) was polling from all 3 queues
- **Infrastructure** (`ecs_compaction_worker.py`) was providing access to all 3 queues
- **Environment variables** included all 3 queue URLs
- The worker should **only** consume from `EMBED_NDJSON_QUEUE` to create embeddings

## Solution Overview

The correct flow is:

```
process_ocr_results.py → EMBED_NDJSON_QUEUE → worker.py → S3 deltas → DynamoDB CompactionRun
                                                              ↓
                                                    DynamoDB Stream
                                                              ↓
                                              LINES_QUEUE & WORDS_QUEUE
                                                              ↓
                                              enhanced_compaction_handler
```

## Files Changed

### 1. `/infra/chromadb_compaction/worker/worker.py`

#### Changes to `main()` function:

```python
# BEFORE: Polling from all 3 queues ❌
def main():
    while True:
        total = 0
        total += drain_queue(EMBED_NDJSON_QUEUE_URL, "embed_ndjson")
        total += drain_queue(LINES_QUEUE_URL, "lines")  # ❌ Wrong!
        total += drain_queue(WORDS_QUEUE_URL, "words")  # ❌ Wrong!
        if total == 0:
            time.sleep(SLEEP_SECONDS)

# AFTER: Only polling EMBED_NDJSON_QUEUE ✅
def main():
    """
    ECS worker main loop - consumes ONLY from EMBED_NDJSON_QUEUE.
    """
    if not EMBED_NDJSON_QUEUE_URL:
        raise RuntimeError("EMBED_NDJSON_QUEUE_URL environment variable is required")

    while True:
        # This worker ONLY processes embed-from-ndjson messages
        total = drain_queue(EMBED_NDJSON_QUEUE_URL, "embed_ndjson")
        if total == 0:
            time.sleep(SLEEP_SECONDS)
```

#### Changes to `_process_payload_dict()`:

- Removed handling for `COMPACTION_RUN` messages (those go to enhanced_compaction_handler)
- Added strict validation for required keys
- Raises clear errors if message structure is wrong

#### Added debugging:

- Logs raw SQS message body (first 500 chars)
- Handles potential double-encoding
- Shows exact message keys being processed

### 2. `/infra/chromadb_compaction/components/ecs_compaction_worker.py`

#### Updated module docstring:

```python
# BEFORE:
"""ECS Fargate compaction worker that mounts EFS and consumes FIFO SQS.

Consumes lines and words FIFO queues, processes compaction messages...
"""

# AFTER:
"""ECS Fargate worker that creates embeddings from NDJSON artifacts.

Consumes EMBED_NDJSON_QUEUE to process receipt data exported to NDJSON format,
creates embeddings, uploads delta files to S3, and writes CompactionRun records...
"""
```

#### IAM Policy - Only EMBED_NDJSON_QUEUE access:

```python
# BEFORE: Access to all 3 queues
"Resource": [lines_queue_arn, words_queue_arn, embed_ndjson_queue_arn]

# AFTER: Only EMBED_NDJSON_QUEUE
"Resource": [embed_ndjson_queue_arn]  # Only this queue
```

#### Environment Variables - Removed LINES/WORDS queues:

```python
# BEFORE:
"environment": [
    {"name": "LINES_QUEUE_URL", "value": "..."},     # ❌ Removed
    {"name": "WORDS_QUEUE_URL", "value": "..."},     # ❌ Removed
    {"name": "EMBED_NDJSON_QUEUE_URL", "value": "..."},  # ✅ Keep
    ...
]

# AFTER:
"environment": [
    {"name": "EMBED_NDJSON_QUEUE_URL", "value": "..."},  # ✅ Only this
    {"name": "DYNAMODB_TABLE_NAME", "value": "..."},
    {"name": "CHROMADB_BUCKET", "value": "..."},
    ...
]
```

### 3. Documentation

#### Created `/infra/chromadb_compaction/worker/QUEUE_CONFIGURATION.md`

Complete documentation of:

- Architecture flow diagram
- Before/after code comparisons
- Infrastructure configuration
- Testing steps
- Environment variables reference

## What You Need to Do Next

### 1. Enable the Worker (Currently Disabled)

In `infra/__main__.py` line 308:

```python
desired_count=1,  # Changed from 0 to 1
```

### 2. Deploy the Changes

```bash
cd infra
pulumi up
```

This will:

1. Update the worker Docker image with new code
2. Update the ECS task definition with new environment variables
3. Update the IAM policy to only grant EMBED_NDJSON_QUEUE access
4. Restart the worker with `desired_count=1` (if you enabled it)

### 3. Test

1. Upload a receipt through your application
2. Check CloudWatch Logs for the worker
3. You should see:

   ```
   Worker starting - consuming from EMBED_NDJSON_QUEUE only
   Processing message from queue=embed_ndjson with keys=['image_id', 'receipt_id', 'artifacts_bucket', 'lines_key', 'words_key']
   Embedding from NDJSON: image_id=xxx, receipt_id=1
   Completed embed_from_ndjson: {...}
   ```

4. **No more "Skipping unsupported message"** errors

### 4. Verify the Flow

After a successful embedding:

1. Check S3 for delta files at `lines/delta/{run_id}/` and `words/delta/{run_id}/`
2. Check DynamoDB for the CompactionRun record
3. Check that `enhanced_compaction_handler` Lambda processed the COMPACTION_RUN messages
4. Check EFS for merged embeddings

## Benefits

✅ **Clearer separation of concerns:**

- Worker: Creates embeddings from NDJSON
- enhanced_compaction_handler: Merges deltas into collections

✅ **Better error handling:**

- Clear validation errors for malformed messages
- No more silently skipping messages

✅ **Improved security:**

- Worker only has access to the queue it actually uses
- Principle of least privilege

✅ **Better logging:**

- Shows exact message structure
- Helps debug future issues quickly

✅ **Documentation:**

- Clear architecture diagrams
- Complete configuration reference

## Rollback Plan

If issues occur:

1. Set `desired_count=0` in `infra/__main__.py`
2. Run `pulumi up` to stop the worker
3. Messages will queue in `EMBED_NDJSON_QUEUE` until worker is fixed
4. Review CloudWatch Logs to identify the issue
