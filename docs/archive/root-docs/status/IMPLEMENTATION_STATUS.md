# Implementation Status: Automated Receipt Processing

## Date: October 24, 2025

## Summary

**Great news!** Most of the automation is **already implemented**. We just need to wire the final pieces together.

## What's Already Working ✅

### 1. NDJSON Export & Queuing (Already Implemented!)

**File:** `infra/upload_images/process_ocr_results.py`

The `_export_receipt_ndjson_and_queue` function (lines 66-101) already:
- ✅ Exports receipt lines/words to NDJSON files in S3
- ✅ Queues messages to `embed_ndjson_queue_url`
- ✅ Is called for NATIVE receipts (line 238)
- ✅ Is called for REFINEMENT receipts (line 169)

```python
def _export_receipt_ndjson_and_queue(image_id: str, receipt_id: int):
    if not embed_ndjson_queue_url:
        logger.info("EMBED_NDJSON_QUEUE_URL not set; skipping embedding queue")
        return
    dynamo = DynamoClient(TABLE_NAME)
    receipt_words = dynamo.list_receipt_words_from_receipt(image_id, int(receipt_id))
    receipt_lines = dynamo.list_receipt_lines_from_receipt(image_id, int(receipt_id))
    
    # Export to S3
    prefix = f"receipts/{image_id}/receipt-{int(receipt_id):05d}/"
    lines_key = prefix + "lines.ndjson"
    words_key = prefix + "words.ndjson"
    _s3_put_ndjson(ARTIFACTS_BUCKET, lines_key, line_rows)
    _s3_put_ndjson(ARTIFACTS_BUCKET, words_key, word_rows)
    
    # Queue for embedding
    payload = {
        "image_id": image_id,
        "receipt_id": int(receipt_id),
        "artifacts_bucket": ARTIFACTS_BUCKET,
        "lines_key": lines_key,
        "words_key": words_key,
    }
    _sqs_embed.send_message(QueueUrl=embed_ndjson_queue_url, MessageBody=json.dumps(payload))
```

### 2. Embedding Lambda (Already Exists!)

**File:** `infra/upload_images/embed_from_ndjson.py`

The `embed_from_ndjson` Lambda already:
- ✅ Reads NDJSON from S3
- ✅ Calls `resolve_receipt()` (merchant validation with ChromaDB + Google Places)
- ✅ Creates embeddings with merchant context
- ✅ Writes ChromaDB deltas to S3
- ✅ Updates COMPACTION_RUN to COMPLETED

**Container Version:** `infra/upload_images/container/handler.py` (same logic, containerized)

### 3. Stream Processor (Already Working!)

**File:** `infra/chromadb_compaction/lambdas/stream_processor.py`

Already detects COMPACTION_RUN completion and queues to compaction Lambda.

### 4. Compaction Lambda (Already Working!)

**File:** `infra/chromadb_compaction/lambdas/enhanced_compaction_handler.py`

Already merges ChromaDB deltas to EFS.

## What's Missing ❌

### 1. Wire `embed_ndjson_queue` to `embed_from_ndjson` Lambda

**Current State:**
- ✅ Queue created in `__main__.py` (line 151)
- ❌ NOT connected to Lambda via event source mapping

**What's Needed:**

In `infra/__main__.py`, add after the queue creation:

```python
# Wire embed-ndjson-queue to existing embed-from-ndjson Lambda
aws.lambda_.EventSourceMapping(
    f"embed-ndjson-event-source-{pulumi.get_stack()}",
    event_source_arn=embed_ndjson_queue.arn,
    function_name=upload_images.embed_from_ndjson_function_name,  # Need to export this
    batch_size=1,
    maximum_batching_window_in_seconds=0,
)
```

### 2. Pass `embed_ndjson_queue_url` to `process_ocr_results` Lambda

**Current State:**
- ✅ Queue URL referenced in `process_ocr_results.py` (line 53)
- ❌ NOT passed as environment variable

**What's Needed:**

In `infra/upload_images/infra.py`, add to `process_ocr_results` Lambda environment:

```python
environment={
    # ... existing vars ...
    "EMBED_NDJSON_QUEUE_URL": embed_ndjson_queue_url,  # From __main__.py
}
```

### 3. Export `embed_from_ndjson` Function Name

**What's Needed:**

In `infra/upload_images/infra.py`, export the function name:

```python
# In UploadImages class
self.embed_from_ndjson_function_name = self.embed_from_ndjson_lambda.name

# Register output
self.register_outputs({
    # ... existing outputs ...
    "embed_from_ndjson_function_name": self.embed_from_ndjson_function_name,
})
```

## Implementation Plan

### Step 1: Update `infra/upload_images/infra.py`

1. Add `embed_ndjson_queue_url` parameter to `UploadImages.__init__()`
2. Pass it to `process_ocr_results` Lambda environment
3. Export `embed_from_ndjson_function_name`

### Step 2: Update `infra/__main__.py`

1. Pass `embed_ndjson_queue.url` to `UploadImages` constructor
2. Add event source mapping connecting queue to Lambda

### Step 3: Deploy

```bash
cd /Users/tnorlund/GitHub/example/infra
pulumi up
```

### Step 4: Test

```bash
# Upload an image via Swift OCR script
# Watch the logs:
aws logs tail /aws/lambda/process-ocr-results-dev --follow
aws logs tail /aws/lambda/embed-from-ndjson-dev --follow
aws logs tail /aws/lambda/chromadb-dev-stream-processor --follow
aws logs tail /aws/lambda/chromadb-dev-enhanced-compaction --follow
```

## Expected Flow After Implementation

```
1. Swift OCR → S3 JSON → SQS (ocr_results_queue)
   ↓
2. process_ocr_results Lambda
   - Creates LINE/WORD/LETTER in DynamoDB
   - Creates COMPACTION_RUN (PENDING)
   - Exports NDJSON to S3
   - ✅ Queues to embed-ndjson-queue (ALREADY WORKING)
   ↓
3. embed-from-ndjson Lambda (TRIGGERED BY SQS)
   - Reads NDJSON from S3
   - Validates merchant (ChromaDB + Google Places)
   - Creates embeddings with merchant context
   - Writes ChromaDB deltas to S3
   - Updates COMPACTION_RUN (COMPLETED)
   ↓
4. stream_processor Lambda (TRIGGERED BY DYNAMODB STREAM)
   - Detects COMPACTION_RUN completion
   - Queues to lines-queue & words-queue
   ↓
5. enhanced_compaction Lambda (TRIGGERED BY SQS)
   - Reads ChromaDB deltas from S3
   - Merges to ChromaDB on EFS
   - Creates S3 snapshot
```

**Result:** Fully automated from image upload to ChromaDB! 🎉

## What We Don't Need

1. ❌ New merchant validation container Lambda - `embed_from_ndjson` already does this
2. ❌ Separate merchant validation Step Function - Redundant
3. ❌ Direct Lambda invocation - SQS is better
4. ❌ Complex DynamoDB stream logic - Already handled

## Cleanup Needed

The merchant validation container we created is not needed. We should:
1. Remove `infra/merchant_validation_container/` directory
2. Remove references from `infra/__main__.py`
3. Keep the `embed_ndjson_queue` (it's needed!)

## Estimated Time to Complete

**30 minutes** - Just wiring up existing pieces!

1. Update `infra/upload_images/infra.py` (10 min)
2. Update `infra/__main__.py` (10 min)
3. Deploy and test (10 min)

