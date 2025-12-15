# Delta Creation Flow Analysis

## Step Function Flow

### 1. PollBatches Step (Map State)
- **Lambda**: `embedding-line-poll-lambda-dev`
- **Handler**: `infra/embedding_step_functions/unified_embedding/handlers/line_polling.py::handle()`
- **MaxConcurrency**: 50 (processes batches in parallel)
- **Purpose**: Polls OpenAI batch API and creates deltas

### 2. Delta Creation Flow

```
PollBatches (Map State)
  ↓
embedding-line-poll Lambda
  ↓
line_polling.py::handle()
  ↓
_handle_internal_core()
  ↓
save_line_embeddings_as_delta()  [receipt_label/embedding/line/poll.py]
  ↓
produce_embedding_delta()  [receipt_label/vector_store/legacy_helpers.py]
  ↓
client.persist_and_upload_delta()  [receipt_label/vector_store/client/chromadb_client.py]
  ↓
✅ VALIDATION SHOULD HAPPEN HERE
```

## Where Deltas Are Created

### File: `receipt_label/receipt_label/embedding/line/poll.py`

**Function**: `save_line_embeddings_as_delta()`

```python
# Line 545
delta_result = produce_embedding_delta(
    ids=ids,
    embeddings=embeddings,
    documents=documents,
    metadatas=metadatas,
    bucket_name=bucket_name,
    collection_name="receipt_lines",
    database_name="lines",
    sqs_queue_url=sqs_queue_url,
    batch_id=batch_id,
)
```

### File: `receipt_label/receipt_label/vector_store/legacy_helpers.py`

**Function**: `produce_embedding_delta()`

**Current Implementation** (after fix):
```python
# Lines 159-164
s3_key = actual_client.persist_and_upload_delta(
    bucket=bucket_name,
    s3_prefix=full_delta_prefix,
    max_retries=3,
    validate_after_upload=True,  # ✅ VALIDATION ENABLED
)
```

## Validation Implementation

### File: `receipt_label/receipt_label/vector_store/client/chromadb_client.py`

**Function**: `persist_and_upload_delta()`

**Validation Steps**:
1. Closes ChromaDB client before upload (`_close_client_for_upload()`)
2. Uploads delta to S3
3. **Validates delta after upload** (if `validate_after_upload=True`):
   - Downloads delta from S3
   - Attempts to open with ChromaDB
   - Verifies collections exist and can be read
4. Retries up to 3 times if validation fails
5. Cleans up corrupted uploads from S3

## The Problem

### Corrupted Delta: `f0d0bf087b8842b39025fcfce3e4a545`

- **Created**: 2025-11-17T01:11:21+00:00 (2025-11-16T17:11:21 PST)
- **Execution Started**: 2025-11-16T17:10:41 PST
- **Delta Created During**: Polling phase of the same execution
- **Error**: SQLite code 14 - "unable to open database file"

### Why It Was Corrupted

The delta was created **before the validation fix was deployed**. At that time:
- ❌ `produce_embedding_delta()` was calling `S3Operations.upload_delta()` directly
- ❌ No client closing before upload
- ❌ No validation after upload
- ❌ No retry logic

### Current Status

✅ **Code Fixed**: `produce_embedding_delta()` now calls `persist_and_upload_delta()` with validation
❓ **Deployment Status**: Need to verify Docker image was rebuilt and Lambda is using new code

## Verification

### Check if Validation is Active

1. **Check Lambda Logs** for validation messages:
   ```bash
   aws logs filter-log-events \
     --log-group-name "/aws/lambda/embedding-line-poll-lambda-dev" \
     --filter-pattern "validation" \
     --start-time $(date -u -v-1H +%s)000
   ```

2. **Look for these log messages**:
   - `"Validating uploaded delta..."`
   - `"Delta validation successful"`
   - `"Delta validation failed"` (with retry attempts)

3. **Check Docker Image**:
   ```bash
   aws lambda get-function \
     --function-name embedding-line-poll-lambda-dev \
     --region us-east-1 \
     --query 'Code.ImageUri'
   ```

4. **Check CodeBuild Builds**:
   ```bash
   aws codebuild list-builds \
     --region us-east-1 \
     --sort-order DESCENDING \
     --max-items 20
   ```

## Expected Behavior After Fix

### When PollBatches Creates Deltas

1. **Delta Creation**:
   - Creates ChromaDB client
   - Upserts embeddings
   - Calls `persist_and_upload_delta()` with `validate_after_upload=True`

2. **Upload Process**:
   - Closes ChromaDB client (ensures SQLite files are flushed)
   - Uploads delta to S3
   - **Validates delta** (downloads and attempts to open)
   - If validation fails → retry (up to 3 times)
   - If all retries fail → raises error (delta not created)

3. **Result**:
   - ✅ Only validated deltas are created
   - ✅ Corrupted uploads are automatically retried
   - ✅ Failed uploads are cleaned up from S3

## Conclusion

**YES** - The corruption is happening during PollBatches when deltas are written to S3.

**YES** - Validation should be added at the upload point (which we've done in `persist_and_upload_delta()`).

**Status**:
- ✅ Code fix is in place
- ❓ Need to verify deployment (Docker image rebuild and Lambda update)

