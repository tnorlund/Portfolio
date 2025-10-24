# Revised Flow Strategy

## Key Discovery

The existing `embed_from_ndjson` Lambda **already does merchant validation**! It:
1. Reads NDJSON from S3
2. Calls `resolve_receipt()` (merchant validation with ChromaDB + Google Places)
3. Creates embeddings with merchant context
4. Writes ChromaDB deltas
5. Updates COMPACTION_RUN

**We don't need a separate merchant validation Lambda!**

## Simplified Approach

### Current Flow (Working)

```
Swift OCR → S3 JSON → SQS (ocr_results_queue) → process_ocr_results Lambda
    ↓
Creates LINE/WORD/LETTER in DynamoDB
Creates COMPACTION_RUN (PENDING)
    ↓
❌ MANUAL: Trigger embed_from_ndjson Lambda
    ↓
embed_from_ndjson reads NDJSON, validates merchant, creates embeddings
Updates COMPACTION_RUN (COMPLETED)
    ↓
Stream Processor detects completion → Queues to compaction
    ↓
Compaction Lambda merges to ChromaDB on EFS
```

### Proposed Flow (Automated)

**Option 1: Direct SQS Trigger from process_ocr_results (Simplest)**

```python
# In process_ocr_results.py after creating NDJSON
if image_type == ImageType.NATIVE:
    process_native(...)  # Creates LINE/WORD/LETTER, COMPACTION_RUN
    
    # Export NDJSON to S3
    lines_key = f"receipts/{image_id}/receipt-{receipt_id:05d}/lines.ndjson"
    words_key = f"receipts/{image_id}/receipt-{receipt_id:05d}/words.ndjson"
    
    # Write NDJSON files
    _s3_put_ndjson(ARTIFACTS_BUCKET, lines_key, [line.to_dict() for line in receipt_lines])
    _s3_put_ndjson(ARTIFACTS_BUCKET, words_key, [word.to_dict() for word in receipt_words])
    
    # Queue to embed-ndjson-queue
    sqs.send_message(
        QueueUrl=embed_ndjson_queue_url,
        MessageBody=json.dumps({
            "image_id": image_id,
            "receipt_id": receipt_id,
            "artifacts_bucket": ARTIFACTS_BUCKET,
            "lines_key": lines_key,
            "words_key": words_key
        })
    )
```

**Option 2: DynamoDB Stream Trigger (More Decoupled)**

Add logic to `stream_processor` to detect new COMPACTION_RUN records:

```python
# In stream_processor/message_builder.py
if event_name == "INSERT" and entity_type == "COMPACTION_RUN":
    # Check if NDJSON files exist in S3
    lines_key = f"receipts/{image_id}/receipt-{receipt_id:05d}/lines.ndjson"
    words_key = f"receipts/{image_id}/receipt-{receipt_id:05d}/words.ndjson"
    
    # Queue to embed-ndjson-queue
    message = {
        "image_id": image_id,
        "receipt_id": receipt_id,
        "artifacts_bucket": CHROMADB_BUCKET,
        "lines_key": lines_key,
        "words_key": words_key
    }
    sqs.send_message(QueueUrl=embed_ndjson_queue_url, MessageBody=json.dumps(message))
```

## Recommendation

**Use Option 1** - Direct SQS trigger from `process_ocr_results`

**Why:**
1. ✅ Simpler - No additional Lambda logic needed
2. ✅ Faster - No DynamoDB stream delay
3. ✅ Clear - Direct cause and effect
4. ✅ Existing pattern - Already used for other queues
5. ✅ Works with existing `embed_from_ndjson` Lambda (no changes needed!)

## What We Actually Need

### 1. Update `process_ocr_results.py`

Add NDJSON export and SQS queuing after `process_native()`:

```python
def _trigger_embedding_pipeline(image_id: str, receipt_id: int, receipt_lines, receipt_words):
    """Export NDJSON and queue for embedding."""
    if not embed_ndjson_queue_url:
        logger.warning("EMBED_NDJSON_QUEUE_URL not set, skipping embedding trigger")
        return
    
    try:
        # Export NDJSON to S3
        lines_key = f"receipts/{image_id}/receipt-{receipt_id:05d}/lines.ndjson"
        words_key = f"receipts/{image_id}/receipt-{receipt_id:05d}/words.ndjson"
        
        _s3_put_ndjson(ARTIFACTS_BUCKET, lines_key, [line.to_dict() for line in receipt_lines])
        _s3_put_ndjson(ARTIFACTS_BUCKET, words_key, [word.to_dict() for word in receipt_words])
        
        # Queue to embed-ndjson-queue
        message = {
            "image_id": image_id,
            "receipt_id": receipt_id,
            "artifacts_bucket": ARTIFACTS_BUCKET,
            "lines_key": lines_key,
            "words_key": words_key
        }
        
        sqs.send_message(
            QueueUrl=embed_ndjson_queue_url,
            MessageBody=json.dumps(message)
        )
        
        logger.info(f"Queued embedding job for {image_id}/{receipt_id}")
    except Exception as e:
        logger.error(f"Failed to trigger embedding: {e}")
        # Don't fail the main processing
```

### 2. Update `infra/upload_images/infra.py`

Add environment variable for `embed_ndjson_queue_url`:

```python
environment={
    # ... existing vars ...
    "EMBED_NDJSON_QUEUE_URL": embed_ndjson_queue_url,  # From main
}
```

### 3. Wire `embed_ndjson_queue` to existing `embed_from_ndjson` Lambda

In `__main__.py`, add event source mapping:

```python
# Connect embed-ndjson-queue to embed-from-ndjson Lambda
aws.lambda_.EventSourceMapping(
    f"embed-ndjson-event-source-{pulumi.get_stack()}",
    event_source_arn=embed_ndjson_queue.arn,
    function_name=upload_images.embed_from_ndjson_function_name,  # Existing Lambda
    batch_size=1,
    maximum_batching_window_in_seconds=0,
)
```

## What We DON'T Need

1. ❌ **New merchant validation Lambda** - `embed_from_ndjson` already does this
2. ❌ **Separate merchant validation container** - Redundant
3. ❌ **Direct Lambda invocation** - Use SQS instead
4. ❌ **Complex DynamoDB stream logic** - Direct trigger is simpler

## Benefits of This Approach

1. **Reuses existing code** - `embed_from_ndjson` already works
2. **Simpler architecture** - One less Lambda to maintain
3. **Event-driven** - SQS provides retry, DLQ, and decoupling
4. **Already tested** - `embed_from_ndjson` is proven
5. **Faster to implement** - Just wire up existing pieces

## Migration Path for EFS

Later, we can optimize `embed_from_ndjson` to use EFS instead of HTTP Chroma:

```python
# In embed_from_ndjson.py
if os.environ.get("CHROMA_ROOT"):
    # Use EFS (container Lambda)
    chroma_client = PersistentClient(path=os.environ["CHROMA_ROOT"])
else:
    # Fall back to HTTP (current)
    chroma_client = _VC.create_chromadb_client(mode="read", http_url=chroma_http)
```

This can be done as a separate optimization after the automation is working.

## Next Steps

1. ✅ Revert the merchant validation container Lambda (not needed)
2. ✅ Update `process_ocr_results.py` to export NDJSON and queue
3. ✅ Wire `embed_ndjson_queue` to existing `embed_from_ndjson` Lambda
4. ✅ Test end-to-end flow
5. 🔮 Later: Optimize `embed_from_ndjson` to use EFS

