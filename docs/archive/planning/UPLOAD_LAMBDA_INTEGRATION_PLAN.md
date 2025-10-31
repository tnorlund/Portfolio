# Upload Lambda Integration Plan - Parallel Execution

## Current Flow

```
1. Lambda receives SQS event
2. OCR Processing → receipt_lines, receipt_words in DynamoDB
3. Embedding Processing (NATIVE/REFINEMENT only):
   ├─ Merchant resolution (ChromaDB + Google Places)
   ├─ Creates ReceiptMetadata in DynamoDB
   ├─ Generate embeddings with merchant context
   ├─ Upload ChromaDB deltas to S3
   └─ Create COMPACTION_RUN record
4. Return success
```

## Proposed Flow with Validation

```
1. Lambda receives SQS event
2. OCR Processing → receipt_lines, receipt_words in DynamoDB
3. Embedding Processing (NATIVE/REFINEMENT only):
   ├─ Merchant resolution (ChromaDB + Google Places)
   ├─ Creates ReceiptMetadata in DynamoDB
   ├─ Generate embeddings with merchant context
   ├─ Upload ChromaDB deltas to S3
   └─ Create COMPACTION_RUN record
4. Start compaction (non-blocking - runs in background)
5. Run LangGraph validation (async):
   ├─ Uses receipt_lines, receipt_words, receipt_metadata already in DynamoDB
   ├─ Extracts merchant name from receipt text
   ├─ Validates against ReceiptMetadata
   └─ Auto-corrects if critical mismatch
6. Return success
```

## Key Insight

**Compaction and validation are completely independent!**

- **Compaction**: Merges ChromaDB deltas (read-only on newly created deltas)
- **Validation**: Reads receipt_lines, receipt_words, receipt_metadata from DynamoDB
- **No dependencies**: They don't interfere with each other

## Implementation

### Option 1: Sequential (Simpler)
```python
# After embedding processing (line 177 in handler.py)
result = embedding_result

# Add validation step
try:
    _log("Starting ReceiptMetadata validation...")
    validation_result = await validate_receipt_metadata(
        image_id=image_id,
        receipt_id=receipt_id,
        client=dynamo,
        ollama_api_key=os.environ.get("OLLAMA_API_KEY"),
        dry_run=False,  # Actually update DynamoDB
    )
    if validation_result.get("requires_metadata_update"):
        _log(f"✅ Updated ReceiptMetadata: {validation_result['corrected_merchant_name']}")
except Exception as e:
    _log(f"⚠️ Validation failed (non-critical): {e}")
    # Don't fail the lambda

return result
```

### Option 2: Parallel (Faster) ⭐ RECOMMENDED
```python
# After creating COMPACTION_RUN (line 437)
# Compaction will start automatically via DynamoDB streams

# Run validation in parallel (non-blocking)
import asyncio
from concurrent.futures import ThreadPoolExecutor

async def run_validation():
    try:
        validation_result = await validate_receipt_metadata(...)
        if validation_result.get("requires_metadata_update"):
            _log(f"✅ Validated and updated ReceiptMetadata")
    except Exception as e:
        _log(f"⚠️ Validation failed: {e}")

# Don't wait - let it run in background
# Lambda returns immediately, compaction and validation run in parallel
asyncio.create_task(run_validation())

return result
```

## Data Flow

We already have everything we need:

```python
# From embedding_result
receipt_lines = embedding_result.get("receipt_lines")  # Already in DynamoDB
receipt_words = embedding_result.get("receipt_words")   # Already in DynamoDB
receipt_metadata = ...  # Already in DynamoDB (created by merchant resolution)

# Just call LangGraph validation
validation_result = await analyze_receipt_simple(
    client=dynamo,
    image_id=image_id,
    receipt_id=receipt_id,
    ollama_api_key=ollama_api_key,
    langsmith_api_key=None,  # Optional
    save_labels=False,  # We're not saving ReceiptWordLabels here
    dry_run=False,  # Update ReceiptMetadata if needed
    save_dev_state=False,
)
```

## Benefits

1. **No extra data fetching** - everything is already in DynamoDB
2. **Parallel execution** - compaction runs while validation runs
3. **Non-blocking** - doesn't slow down the lambda
4. **Independent** - validation success/failure doesn't affect compaction
5. **Auto-correction** - fixes wrong ReceiptMetadata automatically

## Timing

- **Compaction**: ~30-60 seconds (merges ChromaDB deltas)
- **Validation**: ~20-30 seconds (3 LLM calls: Phase 1, Phase 1 Context, Phase 2)
- **Total**: 30-60 seconds (running in parallel!)

If sequential: 60-90 seconds ❌
If parallel: 30-60 seconds ✅

## Next Steps

1. Add validation call to handler.py after embedding processing
2. Make it async/non-blocking so lambda returns quickly
3. Let compaction and validation run independently
4. Monitor both processes via CloudWatch logs

