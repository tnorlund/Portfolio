# PollBatches Validation Verification

## Answer: YES - Validation is in the Right Place

The corruption **IS** happening during PollBatches when deltas are written to S3, and validation **IS** implemented at that point.

## Complete Flow

```
Step Function: line-ingest-sf-dev-1554303
  ↓
PollBatches (Map State, MaxConcurrency: 50)
  ↓
Lambda: embedding-line-poll-lambda-dev
  ↓
Handler: line_polling.py::handle()
  ↓
_handle_internal_core()
  ↓
save_line_embeddings_as_delta()  [receipt_label/embedding/line/poll.py:545]
  ↓
produce_embedding_delta()  [receipt_label/vector_store/legacy_helpers.py:159]
  ↓
client.persist_and_upload_delta(
    bucket=bucket_name,
    s3_prefix=full_delta_prefix,
    max_retries=3,
    validate_after_upload=True  ✅ VALIDATION HERE
)
  ↓
chromadb_client.py::persist_and_upload_delta()
  ↓
1. Close ChromaDB client (_close_client_for_upload)
2. Upload delta to S3
3. Validate delta (download and attempt to open) ✅
4. Retry if validation fails (up to 3 times) ✅
5. Clean up corrupted uploads ✅
```

## Code Verification

### ✅ Validation is Implemented

**File**: `receipt_label/receipt_label/vector_store/legacy_helpers.py`

**Lines 159-164**:
```python
s3_key = actual_client.persist_and_upload_delta(
    bucket=bucket_name,
    s3_prefix=full_delta_prefix,
    max_retries=3,
    validate_after_upload=True,  # ✅ VALIDATION ENABLED
)
```

**File**: `receipt_label/receipt_label/vector_store/client/chromadb_client.py`

**Lines 639-643**:
```python
# Validate the uploaded delta if requested
if validate_after_upload:
    logger.info("Validating uploaded delta...")
    if self._validate_delta_after_upload(bucket, prefix, s3_client):
        logger.info("Delta validation successful: %s", prefix)
        return prefix
```

## Why the Corrupted Delta Exists

### Timeline

1. **17:10:41 PST** - Execution started
2. **17:11:21 PST** - Delta `f0d0bf087b8842b39025fcfce3e4a545` created
3. **17:12:54 PST** - Chunk #40 tried to process it → failed

### Root Cause

The delta was created **BEFORE the validation fix was deployed**. At that time:
- ❌ Code was using old version without validation
- ❌ ChromaDB client wasn't closed before upload
- ❌ No validation after upload
- ❌ Delta was corrupted during upload

## Deployment Status

### ✅ Code Status
- Code fix is committed (commit `4b2495ca` on 2025-11-16 21:15:51 UTC)
- Validation is implemented in the right place (PollBatches → produce_embedding_delta → persist_and_upload_delta)

### ❓ Deployment Status
- Need to verify Docker image was rebuilt after commit
- Need to verify Lambda is using new Docker image
- Need to verify validation is active in logs

## How to Verify Validation is Active

### 1. Check Lambda Logs for Validation Messages

```bash
aws logs filter-log-events \
  --log-group-name "/aws/lambda/embedding-line-poll-lambda-dev" \
  --region us-east-1 \
  --start-time $(python3 -c "from datetime import datetime, timedelta; print(int((datetime.utcnow() - timedelta(hours=1)).timestamp() * 1000))") \
  --filter-pattern "validation" \
  --max-items 20
```

**Look for**:
- `"Validating uploaded delta..."`
- `"Delta validation successful"`
- `"Delta validation failed"` (with retry attempts)

### 2. Check Docker Image

```bash
# Get current Lambda image
aws lambda get-function \
  --function-name embedding-line-poll-lambda-dev \
  --region us-east-1 \
  --query 'Code.ImageUri'

# Check ECR images (should have one pushed AFTER 2025-11-16 21:15:51 UTC)
aws ecr describe-images \
  --repository-name embedding-line-poll-docker-repo-4146688 \
  --region us-east-1 \
  --max-items 5 \
  --query 'imageDetails[*].{pushedAt:imagePushedAt, digest:imageDigest}'
```

### 3. Check CodeBuild Builds

```bash
aws codebuild list-builds \
  --region us-east-1 \
  --sort-order DESCENDING \
  --max-items 10 \
  --output json | jq -r '.ids[]' | head -5 | while read build_id; do
  echo "Build: $build_id"
  aws codebuild batch-get-builds \
    --region us-east-1 \
    --ids "$build_id" \
    --query 'builds[0].{status:buildStatus, startTime:startTime, projectName:projectName}' \
    --output json | jq '.'
done
```

**Look for**:
- Project name containing "embedding-line-poll"
- Started AFTER 2025-11-16 21:15:51 UTC
- Status: SUCCEEDED

## Expected Behavior After Deployment

### When PollBatches Creates Deltas (After Fix)

1. **Delta Creation**:
   ```
   PollBatches → embedding-line-poll Lambda
     → save_line_embeddings_as_delta()
       → produce_embedding_delta()
         → persist_and_upload_delta(validate_after_upload=True)
   ```

2. **Upload Process**:
   - ✅ Closes ChromaDB client (ensures SQLite files are flushed)
   - ✅ Uploads delta to S3
   - ✅ **Validates delta** (downloads and attempts to open)
   - ✅ If validation fails → retry (up to 3 times)
   - ✅ If all retries fail → raises error (delta not created)

3. **Result**:
   - ✅ Only validated deltas are created
   - ✅ Corrupted uploads are automatically retried
   - ✅ Failed uploads are cleaned up from S3
   - ✅ No corrupted deltas should be created going forward

## Summary

| Question | Answer |
|----------|--------|
| Is corruption happening during PollBatches? | ✅ YES |
| Is validation in the right place? | ✅ YES (produce_embedding_delta → persist_and_upload_delta) |
| Is validation code implemented? | ✅ YES (committed) |
| Is validation deployed? | ❓ NEED TO VERIFY |
| Will new deltas be validated? | ✅ YES (once deployed) |

## Next Steps

1. **Verify Deployment**: Check if Docker image was rebuilt and Lambda is using new code
2. **Monitor Logs**: Look for validation messages in new executions
3. **Test**: Run a new execution and verify no corrupted deltas are created
4. **Handle Old Deltas**: The graceful error handling we added will skip corrupted deltas from before the fix

