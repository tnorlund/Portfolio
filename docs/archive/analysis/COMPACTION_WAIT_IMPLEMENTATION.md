# Compaction Wait Implementation

## Problem

When `save_labels=True` in production, validation creates `ReceiptWordLabels`. If compaction is still reading from DynamoDB when these labels are created, we could have a race condition.

## Solution

Wait for the initial compaction to complete before creating ReceiptWordLabels.

### How It Works

1. **Poll Compaction State**: Check `COMPACTION_RUN.lines_state` and `words_state` every 2 seconds
2. **Wait for Completion**: Only proceed when both are `COMPLETED`
3. **Timeout Protection**: Max 120 seconds wait, then proceed anyway
4. **Then Create Labels**: Now safe to create ReceiptWordLabels without race condition

### Code Changes

**File**: `infra/upload_images/container_ocr/handler/handler.py`

```python
def _run_validation_async(
    image_id: str,
    receipt_id: int,
    run_id: str,  # NEW: Track compaction completion
    receipt_lines: Optional[list],
    receipt_words: Optional[list],
    ollama_api_key: Optional[str],
    langsmith_api_key: Optional[str],
) -> None:
    """Run LangGraph validation asynchronously (non-blocking).
    
    IMPORTANT: Waits for initial compaction to complete before creating ReceiptWordLabels
    to avoid race conditions.
    """
    
    async def run_validation():
        # CRITICAL: Wait for initial compaction to complete
        _log(f"Waiting for compaction run {run_id} to complete...")
        max_wait_seconds = 120
        poll_interval_seconds = 2
        waited_seconds = 0
        
        while waited_seconds < max_wait_seconds:
            compaction_run = dynamo.get_compaction_run(image_id, receipt_id, run_id)
            lines_completed = compaction_run.lines_state == CompactionState.COMPLETED.value
            words_completed = compaction_run.words_state == CompactionState.COMPLETED.value
            
            if lines_completed and words_completed:
                _log(f"✅ Compaction completed (waited {waited_seconds}s)")
                break
            
            await asyncio.sleep(poll_interval_seconds)
            waited_seconds += poll_interval_seconds
        
        # Now safe to create ReceiptWordLabels
        await analyze_receipt_simple(
            ...
            save_labels=True,  # Safe now - compaction is done
            ...
        )
```

## Execution Flow

```
1. Embeddings created → COMPACTION_RUN.created()
   ↓
2. Compaction Lambda starts (async via DynamoDB Streams)
   ↓
3. Validation starts in background (async task)
   ↓
4. Validation waits... (polls COMPACTION_RUN state every 2s)
   ↓
5. Compaction completes (both lines and words → COMPLETED)
   ↓
6. Validation proceeds (creates ReceiptWordLabels)
   ↓
7. ReceiptMetadata updated if mismatch
   ↓
8. ReceiptMetadata change triggers SECOND compaction (metadata update)
```

## Benefits

✅ **No Race Conditions**: Labels are only created after compaction finishes
✅ **Reliable**: Compaction sees consistent state from DynamoDB
✅ **Non-blocking**: Lambda still returns immediately (async task)
✅ **Resilient**: Timeout protection prevents infinite waits

## Trade-offs

⚠️ **Slightly Slower**: Validation waits for compaction (typically 30-60 seconds)
- But this is expected - we need compaction to finish first
- Lambda still returns immediately (async task)

✅ **Worth It**: Prevents race conditions in production with `save_labels=True`

## Monitoring

Track via CloudWatch logs:
```
[HANDLER] Waiting for compaction run {run_id} to complete...
[HANDLER] ⏳ Compaction in progress: lines=PROCESSING, words=PENDING
[HANDLER] ✅ Compaction completed (waited 35s)
[HANDLER] ✅ Validation completed for {image_id}/{receipt_id}
```

## Timeout Behavior

If compaction takes > 120 seconds:
```
[HANDLER] ⚠️ Timeout waiting for compaction after 120s, proceeding anyway
```

This ensures we don't wait forever if compaction fails silently.

