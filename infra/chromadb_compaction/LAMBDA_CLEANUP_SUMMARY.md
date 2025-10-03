# Lambda Cleanup - Removing Duplicate Queue Consumers

## Problem Found

You had **THREE separate Lambdas** competing for queues, causing confusion:

### EMBED_NDJSON_QUEUE Consumers:

1. ✅ `embed_from_ndjson_lambda` (upload_images/infra.py) - **ACTIVE**

### LINES_QUEUE & WORDS_QUEUE Consumers:

1. ✅ `chroma-worker-lambda` (**main**.py:421-475) - **WAS ACTIVE** (the one you saw)
2. ❌ `enhanced_compaction_handler` (chromadb_infrastructure) - **WAS DISABLED**

## Changes Made

### 1. Disabled Old `chroma-worker-lambda`

**File:** `infra/__main__.py` lines 421-475

**Before:**

```python
chroma_worker_lambda = aws.lambda_.Function(
    f"chroma-worker-lambda-{pulumi.get_stack()}",
    ...
)

aws.lambda_.EventSourceMapping(
    f"chroma-worker-lines-mapping-{pulumi.get_stack()}",
    event_source_arn=chromadb_infrastructure.chromadb_queues.lines_queue_arn,
    function_name=chroma_worker_lambda.arn,  # ← OLD Lambda
    ...
)
```

**After:**

```python
# DISABLED: Commented out entirely
# Using enhanced_compaction_handler instead
```

### 2. Enabled Enhanced Compaction Handler

**File:** `infra/chromadb_compaction/infrastructure.py` line 105

**Before:**

```python
enable_enhanced_sqs_mappings=False,  # ← Disabled
```

**After:**

```python
enable_enhanced_sqs_mappings=True,  # ← Enabled
```

This creates EventSourceMappings in `components/lambda_functions.py`:

- `{name}-lines-event-source-mapping` → `enhanced_compaction_function`
- `{name}-words-event-source-mapping` → `enhanced_compaction_function`

## Final Clean Architecture

```
┌─────────────────────────────────────────────────────────────┐
│ process_ocr_results.py                                      │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│ EMBED_NDJSON_QUEUE                                          │
└────────────────────┬────────────────────────────────────────┘
                     │ EventSourceMapping
                     ▼
┌─────────────────────────────────────────────────────────────┐
│ embed_from_ndjson_lambda ✅                                 │
│ (upload_images/infra.py)                                    │
│ - Creates embeddings                                        │
│ - Uploads deltas to S3                                      │
│ - Writes CompactionRun to DynamoDB                          │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│ DynamoDB Stream                                             │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│ stream_processor Lambda                                     │
│ - Sends COMPACTION_RUN messages                            │
└────────────────────┬────────────────────────────────────────┘
                     │
                     ▼
┌─────────────────────────────────────────────────────────────┐
│ LINES_QUEUE & WORDS_QUEUE                                   │
└────────────────────┬────────────────────────────────────────┘
                     │ EventSourceMapping
                     ▼
┌─────────────────────────────────────────────────────────────┐
│ enhanced_compaction_handler ✅ (NEW CONSUMER)               │
│ (chromadb_infrastructure)                                   │
│ - Downloads deltas from S3                                  │
│ - Merges into main ChromaDB collections on EFS             │
└─────────────────────────────────────────────────────────────┘
```

## What Will Happen After Deploy

### During `pulumi up`:

1. **Old Lambda Deleted:**

   - `chroma-worker-lambda-dev-ed34dbe` will be removed
   - Its EventSourceMappings will be deleted

2. **New Lambda EventSourceMappings Created:**

   - `chromadb-dev-lambdas-lines-event-source-mapping`
   - `chromadb-dev-lambdas-words-event-source-mapping`
   - Connected to `enhanced_compaction_function`

3. **No Downtime:**
   - Messages already in queues will be processed by new Lambda
   - Stream processor continues working

### After Deploy:

You'll see:

- ❌ `chroma-worker-lambda-dev-ed34dbe` - **GONE**
- ✅ `chromadb-dev-lambdas-enhanced-compaction` - **ACTIVE**

## Verification Steps

### 1. Check Lambda Functions

```bash
# Should NOT see old chroma-worker-lambda
aws lambda list-functions --query 'Functions[?contains(FunctionName, `chroma-worker`)].FunctionName'

# Should see enhanced compaction handler
aws lambda list-functions --query 'Functions[?contains(FunctionName, `enhanced-compaction`)].FunctionName'
```

### 2. Check EventSourceMappings

```bash
# List all event source mappings for your queues
aws lambda list-event-source-mappings \
  --query 'EventSourceMappings[?contains(EventSourceArn, `lines-queue`)].{Function:FunctionArn,State:State}'

aws lambda list-event-source-mappings \
  --query 'EventSourceMappings[?contains(EventSourceArn, `words-queue`)].{Function:FunctionArn,State:State}'
```

Should show:

- Function: `arn:aws:lambda:...:function:chromadb-dev-lambdas-enhanced-compaction`
- State: `Enabled`

### 3. Monitor Queue Processing

```bash
# Watch SQS queue metrics
aws sqs get-queue-attributes \
  --queue-url https://sqs.us-east-1.amazonaws.com/.../lines-queue \
  --attribute-names ApproximateNumberOfMessages
```

Messages should be processing normally.

### 4. Check CloudWatch Logs

```bash
# New Lambda logs
aws logs tail /aws/lambda/chromadb-dev-lambdas-enhanced-compaction --follow

# Should see processing activity:
# "Processing COMPACTION_RUN..."
# "Downloaded delta..."
# "Merged X records..."
```

## Benefits of This Change

✅ **Single Consumer Per Queue**

- No more race conditions
- Predictable message routing

✅ **Modern Architecture**

- Uses newer enhanced_compaction_handler
- Better structured code in chromadb_compaction module

✅ **Cleaner Codebase**

- Removed duplicate Lambda definition
- All compaction logic in one place

✅ **Easier Maintenance**

- One place to update compaction logic
- Clear separation of concerns

## Rollback Plan

If issues occur:

1. **Re-enable old Lambda:**

   ```python
   # In infra/__main__.py, uncomment lines 426-475
   chroma_worker_lambda = aws.lambda_.Function(...)
   aws.lambda_.EventSourceMapping(...)
   ```

2. **Disable enhanced handler:**

   ```python
   # In infra/chromadb_compaction/infrastructure.py
   enable_enhanced_sqs_mappings=False,
   ```

3. **Deploy:**
   ```bash
   pulumi up
   ```

## Summary

- ✅ Removed duplicate `chroma-worker-lambda`
- ✅ Enabled `enhanced_compaction_handler`
- ✅ Clean single-consumer architecture
- ✅ Ready to deploy

Run `pulumi up` and you'll see the old Lambda disappear and the new one take over!
