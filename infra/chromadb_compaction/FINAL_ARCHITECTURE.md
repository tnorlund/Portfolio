# Final Architecture - Lambda-Based Embedding Flow

## Architecture Decision: Lambda Only ✅

We're using **Lambda (embed_from_ndjson_lambda)** to consume from `EMBED_NDJSON_QUEUE`, not the ECS worker.

## Current Flow

```
┌──────────────────────────────────────────────────────────────┐
│ process_ocr_results.py                                       │
│ - Processes OCR results                                      │
│ - Exports receipt lines/words to NDJSON in S3              │
│ - Enqueues message to EMBED_NDJSON_QUEUE                   │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────┐
│ EMBED_NDJSON_QUEUE (SQS)                                    │
│ Message: {                                                   │
│   "image_id": "...",                                        │
│   "receipt_id": 1,                                          │
│   "artifacts_bucket": "...",                                │
│   "lines_key": "receipts/.../lines.ndjson",                │
│   "words_key": "receipts/.../words.ndjson"                 │
│ }                                                            │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         │ EventSourceMapping (ACTIVE)
                         ▼
┌──────────────────────────────────────────────────────────────┐
│ embed_from_ndjson_lambda (Lambda Function)                  │
│ - Triggered by SQS EventSourceMapping                       │
│ - Reads NDJSON from S3                                      │
│ - Creates embeddings (lines + words)                        │
│ - Uploads delta files to S3                                 │
│ - Writes CompactionRun to DynamoDB                          │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────┐
│ DynamoDB Stream                                              │
│ - Detects CompactionRun INSERT                              │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────┐
│ stream_processor.py (Lambda)                                 │
│ - Sends COMPACTION_RUN to LINES_QUEUE & WORDS_QUEUE        │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         ▼
┌──────────────────────────────────────────────────────────────┐
│ LINES_QUEUE & WORDS_QUEUE (SQS FIFO)                       │
└────────────────────────┬─────────────────────────────────────┘
                         │
                         │ EventSourceMapping
                         ▼
┌──────────────────────────────────────────────────────────────┐
│ enhanced_compaction_handler (Lambda)                         │
│ - Downloads delta files from S3                             │
│ - Merges deltas into main ChromaDB collections on EFS      │
└──────────────────────────────────────────────────────────────┘
```

## Components Status

### ✅ ACTIVE Components

1. **embed_from_ndjson_lambda**

   - Location: `infra/upload_images/infra.py:572-610`
   - EventSourceMapping: `infra/upload_images/infra.py:631-638` (enabled=True)
   - Purpose: Creates embeddings from NDJSON artifacts

2. **EMBED_NDJSON_QUEUE**

   - Location: `infra/upload_images/infra.py:614-626`
   - Consumed by: embed_from_ndjson_lambda

3. **enhanced_compaction_handler**
   - Consumes: LINES_QUEUE & WORDS_QUEUE
   - Purpose: Merges delta files into main collections

### ❌ DISABLED Components

1. **ChromaCompactionWorker (ECS)**

   - Location: `infra/__main__.py:300-318`
   - Status: `desired_count=0` (disabled)
   - Reason: Using Lambda instead for simpler ops

2. **embed_batch_launcher**
   - Location: `infra/upload_images/infra.py:943-959`
   - EventSourceMapping: Commented out (lines 974-982)
   - Status: Lambda exists but not connected to any queue
   - Reason: Using direct Lambda approach instead of Step Functions

## Why Lambda Over ECS Worker?

### Advantages of Lambda Approach

✅ **Simpler Operations**

- No container management
- Auto-scaling built-in
- Pay per invocation (no idle costs)

✅ **Built-in Reliability**

- Automatic retries on failure
- Dead Letter Queue support
- CloudWatch integration

✅ **Current Implementation**

- Already working and deployed
- No additional configuration needed

### When to Consider ECS Worker

Use ECS worker if you need:

- Jobs longer than 15 minutes
- More than 10GB memory
- Custom scaling logic
- Direct EFS access patterns with high I/O

For now, Lambda is sufficient.

## Configuration Files

### infra/upload_images/infra.py

```python
# Line 631-638: ACTIVE EventSourceMapping
aws.lambda_.EventSourceMapping(
    f"{name}-embed-ndjson-direct-mapping",
    event_source_arn=self.embed_ndjson_queue.arn,
    function_name=embed_from_ndjson_lambda.name,
    batch_size=10,
    maximum_batching_window_in_seconds=5,
    enabled=True,  # ← ACTIVE: Lambda consumes the queue
    opts=ResourceOptions(parent=self),
)

# Line 974-982: DISABLED embed_batch_launcher mapping
# (commented out - no longer using Step Functions approach)
```

### infra/**main**.py

```python
# Line 317: ECS Worker DISABLED
ChromaCompactionWorker(
    ...
    desired_count=0,  # DISABLED: Using Lambda instead
)
```

## Environment Variables

### embed_from_ndjson_lambda

```python
"DYNAMO_TABLE_NAME": dynamodb_table.name,
"CHROMADB_BUCKET": chromadb_bucket_name,
"OPENAI_API_KEY": openai_api_key,
"GOOGLE_PLACES_API_KEY": google_places_api_key,
"CHROMA_HTTP_ENDPOINT": chroma_http_endpoint,
```

## Monitoring

### Key Metrics to Watch

1. **SQS Queue Metrics**

   - `ApproximateNumberOfMessagesVisible` in EMBED_NDJSON_QUEUE
   - Should be close to 0 (messages processed quickly)

2. **Lambda Metrics**

   - `Invocations` - number of times Lambda ran
   - `Duration` - how long each invocation takes
   - `Errors` - any failures
   - `Throttles` - if Lambda is being rate-limited

3. **DLQ Metrics**
   - Check dead letter queue for failed messages
   - Investigate any messages that fail after retries

### CloudWatch Logs

```bash
# Lambda logs
aws logs tail /aws/lambda/upload-images-dev-embed-from-ndjson --follow

# Check for errors
aws logs filter-pattern '/aws/lambda/upload-images-dev-embed-from-ndjson' \
  --filter-pattern 'ERROR'
```

## Troubleshooting

### Messages Not Being Processed

1. Check EventSourceMapping is enabled:
   ```bash
   aws lambda list-event-source-mappings \
     --function-name upload-images-dev-embed-from-ndjson
   ```
2. Check Lambda has proper permissions to read from SQS
3. Check Lambda execution role has DynamoDB/S3 permissions

### Lambda Timeout Issues

- Current timeout: 900s (15 minutes)
- If hitting timeout, consider:
  - Reducing batch size
  - Optimizing embedding creation
  - Switching to ECS worker for very large jobs

### Check Queue Configuration

```bash
aws sqs get-queue-attributes \
  --queue-url https://sqs.us-east-1.amazonaws.com/.../embed-ndjson-queue \
  --attribute-names All
```

## Testing

### Manual Test

1. Upload a receipt through your application
2. Watch SQS queue - message should appear
3. Watch Lambda logs - should see invocation
4. Check S3 - delta files should be created
5. Check DynamoDB - CompactionRun record created
6. Watch LINES/WORDS queues - should receive COMPACTION_RUN messages
7. Watch enhanced_compaction_handler logs - should merge deltas

### Automated Test

```python
# Send test message to queue
import boto3
import json

sqs = boto3.client('sqs')
queue_url = 'https://sqs.us-east-1.amazonaws.com/.../embed-ndjson-queue'

message = {
    "image_id": "test-image-id",
    "receipt_id": 1,
    "artifacts_bucket": "artifacts-bucket",
    "lines_key": "receipts/test/lines.ndjson",
    "words_key": "receipts/test/words.ndjson"
}

sqs.send_message(
    QueueUrl=queue_url,
    MessageBody=json.dumps(message)
)
```

## Summary

- ✅ Lambda handles EMBED_NDJSON_QUEUE (active)
- ✅ EventSourceMapping connects queue to Lambda
- ❌ ECS worker disabled (desired_count=0)
- ❌ embed_batch_launcher disconnected (EventSourceMapping commented out)
- ✅ enhanced_compaction_handler handles LINES/WORDS queues separately

Simple, reliable, auto-scaling architecture. No changes needed unless you hit Lambda limits.
