# Worker Queue Configuration Fix

## Problem Summary

The worker was incorrectly configured to consume from **all three queues** (`EMBED_NDJSON_QUEUE`, `LINES_QUEUE`, `WORDS_QUEUE`), causing it to receive `COMPACTION_RUN` messages that it couldn't process.

## Architecture (Correct Flow)

```
┌─────────────────────────────────────────────────────────────────────┐
│ process_ocr_results.py                                              │
│ - Exports lines/words to NDJSON in S3                              │
│ - Sends message to EMBED_NDJSON_QUEUE                              │
└────────────────────────┬────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────────┐
│ ECS Worker (worker.py)                                              │
│ - Consumes EMBED_NDJSON_QUEUE ONLY                                 │
│ - Creates embeddings from NDJSON                                    │
│ - Uploads delta files to S3                                         │
│ - Writes CompactionRun to DynamoDB                                  │
└────────────────────────┬────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────────┐
│ DynamoDB Streams → stream_processor.py                              │
│ - Detects CompactionRun INSERT                                      │
│ - Sends COMPACTION_RUN messages to LINES_QUEUE & WORDS_QUEUE       │
└────────────────────────┬────────────────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────────────────┐
│ enhanced_compaction_handler Lambda                                  │
│ - Consumes LINES_QUEUE & WORDS_QUEUE                               │
│ - Downloads delta files from S3                                     │
│ - Merges deltas into main ChromaDB collections on EFS               │
└─────────────────────────────────────────────────────────────────────┘
```

## Changes Made to `worker.py`

### 1. Main Loop - Only Consume EMBED_NDJSON_QUEUE

**Before:**

```python
def main():
    while True:
        total = 0
        total += drain_queue(EMBED_NDJSON_QUEUE_URL, "embed_ndjson")
        total += drain_queue(LINES_QUEUE_URL, "lines")  # ❌ WRONG
        total += drain_queue(WORDS_QUEUE_URL, "words")  # ❌ WRONG
        if total == 0:
            time.sleep(SLEEP_SECONDS)
```

**After:**

```python
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

### 2. Message Processing - Validate Structure

**Before:**

- Checked for EmbedFromNdjson format
- Had fallback handling for COMPACTION_RUN messages
- Logged unsupported messages

**After:**

- Validates required keys are present
- Raises clear error if message structure is wrong
- Only processes embed-from-ndjson format

## Infrastructure Configuration

### Queue Creation

The `EMBED_NDJSON_QUEUE` is created in `infra/upload_images/infra.py`:

```python
self.embed_ndjson_queue = Queue(
    f"{name}-embed-ndjson-queue",
    name=f"{name}-{stack}-embed-ndjson-queue",
    visibility_timeout_seconds=1200,
    message_retention_seconds=1209600,
    ...
)
```

### Worker Configuration

The ECS worker is configured in `infra/__main__.py`:

```python
ChromaCompactionWorker(
    name=f"chroma-compaction-worker-{pulumi.get_stack()}-v2",
    ...
    embed_ndjson_queue_url=upload_images.embed_ndjson_queue.url,
    embed_ndjson_queue_arn=upload_images.embed_ndjson_queue.arn,
    ...
    desired_count=0,  # ⚠️ CURRENTLY DISABLED
)
```

### IAM Permissions (in `ecs_compaction_worker.py`)

The worker IAM policy has been updated to **only grant access to EMBED_NDJSON_QUEUE**:

```python
# Only grant access to EMBED_NDJSON_QUEUE (not LINES/WORDS queues)
{
    "Effect": "Allow",
    "Action": [
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage",
        "sqs:ChangeMessageVisibility",
        "sqs:GetQueueAttributes",
        "sqs:GetQueueUrl",
    ],
    "Resource": [embed_ndjson_queue_arn]  # Only this queue
}
```

### Environment Variables (in `ecs_compaction_worker.py`)

The container environment has been simplified to only include:

```python
"environment": [
    {"name": "DYNAMODB_TABLE_NAME", "value": "..."},
    {"name": "CHROMA_ROOT", "value": "/mnt/chroma"},
    {"name": "CHROMADB_BUCKET", "value": "..."},
    {"name": "EMBED_NDJSON_QUEUE_URL", "value": "..."},  # Only queue needed
    {"name": "LOG_LEVEL", "value": "INFO"},
]
```

**Note:** `LINES_QUEUE_URL` and `WORDS_QUEUE_URL` have been removed from the environment since the worker doesn't consume from those queues.

## ⚠️ Important: Enable the Worker

The worker is currently **disabled** (`desired_count=0`). To enable it:

**In `infra/__main__.py` line 308:**

```python
desired_count=1,  # Changed from 0 to 1
```

Then deploy:

```bash
pulumi up
```

## Testing

After deploying the fix:

1. **Upload a receipt** - this triggers OCR processing
2. **Check logs** - you should see:

   ```
   Worker starting - consuming from EMBED_NDJSON_QUEUE only
   Processing message from queue=embed_ndjson with keys=['image_id', 'receipt_id', 'artifacts_bucket', 'lines_key', 'words_key']
   Embedding from NDJSON: image_id=xxx, receipt_id=1
   Completed embed_from_ndjson: {...}
   ```

3. **No more "Skipping unsupported message"** errors for COMPACTION_RUN

## Environment Variables

The worker requires these environment variables (configured in ECS task definition):

```bash
# Required - the queue this worker consumes from
EMBED_NDJSON_QUEUE_URL=https://sqs.us-east-1.amazonaws.com/.../embed-ndjson-queue

# Required - for accessing DynamoDB and S3
DYNAMODB_TABLE_NAME=portfolio-dev
CHROMADB_BUCKET=chromadb-bucket-name

# Optional - for merchant resolution
GOOGLE_PLACES_API_KEY=xxx
OPENAI_API_KEY=xxx

# EFS mount point for reading existing embeddings
CHROMA_ROOT=/mnt/chroma
```

## Summary

- ✅ Worker now ONLY consumes from `EMBED_NDJSON_QUEUE`
- ✅ `LINES_QUEUE` and `WORDS_QUEUE` are consumed by `enhanced_compaction_handler` Lambda
- ✅ Clear error messages if wrong message format is received
- ⚠️ Worker is currently disabled (`desired_count=0`) - needs to be enabled
