# Chunk #40 Iteration Failure Analysis

## Execution Details

- **Execution ID**: `323efb0e-896c-4de9-b075-4c1e12d0bd1d`
- **Step Function**: `line-ingest-sf-dev-1554303`
- **Status**: FAILED
- **Failed Iteration**: ProcessChunksInParallel #40
- **Start Time**: 2025-11-16T17:10:41.157 PST
- **Failure Time**: 2025-11-16T17:12:59.632 PST
- **Duration**: ~2 minutes 18 seconds

## Failure Sequence

### Timeline

1. **17:10:41 PST** - Execution started
2. **17:11:21 PST** - Delta `f0d0bf087b8842b39025fcfce3e4a545` created (during polling phase)
3. **17:12:54.495 PST** - Chunk #40 iteration started
4. **17:12:54.720 PST** - Chunk #40 downloaded from S3 (10 deltas)
5. **17:12:54.720 - 17:12:59 PST** - Processing deltas:
   - Successfully processed 9 deltas
   - Failed on 10th delta: `lines/delta/lines/receipt_lines/f0d0bf087b8842b39025fcfce3e4a545/`
6. **17:12:59.548 PST** - Error occurred: "unable to open database file" (SQLite code 14)
7. **17:12:59.632 PST** - Execution failed with `ChunkProcessingFailed`

### Failed Delta

- **Delta Key**: `lines/delta/lines/receipt_lines/f0d0bf087b8842b39025fcfce3e4a545/`
- **Created**: 2025-11-17T01:11:21+00:00 (2025-11-16T17:11:21 PST)
- **File Size**: 966,656 bytes (chroma.sqlite3 exists in S3)
- **Error**: `error returned from database: (code: 14) unable to open database file`
- **Root Cause**: Delta was corrupted during upload (ChromaDB client was still open)

## Error Details

### Lambda Error
```
RuntimeError: Failed to open delta lines/delta/lines/receipt_lines/f0d0bf087b8842b39025fcfce3e4a545/:
error returned from database: (code: 14) unable to open database file.
This may indicate the delta was corrupted during upload or download.
```

### Lambda Logs
```json
{
  "level": "ERROR",
  "logger": "handlers.compaction",
  "message": "Failed to open delta ChromaDB client",
  "delta_key": "lines/delta/lines/receipt_lines/f0d0bf087b8842b39025fcfce3e4a545/",
  "delta_temp": "/tmp/tmpqpo1pbs6/tmpqqjw2d0d",
  "error": "error returned from database: (code: 14) unable to open database file",
  "error_type": "InternalError",
  "sqlite_files": ["/tmp/tmpqpo1pbs6/tmpqqjw2d0d/chroma.sqlite3"]
}
```

### Step Function Events

1. **MapIterationStarted** (id: 3561) - Chunk #40 started
2. **LambdaFunctionScheduled** (id: 3563)
3. **LambdaFunctionStarted** (id: 3564) - 17:12:54.578 PST
4. **LambdaFunctionFailed** (id: 3586) - 17:12:59.561 PST
5. **MapIterationFailed** (id: 3589) - Chunk #40 failed
6. **ExecutionFailed** (id: 3600) - Execution terminated

## Root Cause Analysis

### Why This Happened

1. **Delta Created During Polling**: The delta was created at 17:11:21 PST, which was during the polling phase of the same execution
2. **Client Not Closed**: The delta was uploaded while the ChromaDB client was still open in the polling Lambda
3. **SQLite File Corruption**: The SQLite database file was locked or being written to during upload, causing corruption
4. **Validation Not Active**: The validation fix was committed but the Docker image hadn't been rebuilt yet

### Evidence

- Delta file exists in S3 with non-zero size (966,656 bytes)
- SQLite file is present in the delta directory
- ChromaDB cannot open the SQLite file (code 14 = "unable to open database file")
- This matches the pattern of deltas uploaded before the client closing fix

## Impact

- **Chunk #40**: Failed completely (0/10 deltas processed)
- **Execution**: Failed, preventing all subsequent chunks from being processed
- **Data Loss**: 10 deltas worth of embeddings not processed

## Fix Status

### ✅ Code Fixes Implemented

1. **Client Closing**: `persist_and_upload_delta()` now closes ChromaDB client before upload
2. **Post-Upload Validation**: Deltas are validated after upload (download and attempt to open)
3. **Retry Logic**: Up to 3 retries if validation fails
4. **Cleanup**: Failed uploads are automatically deleted from S3

### ⚠️ Deployment Status

- **Code Committed**: ✅ (commit `4b2495ca` on 2025-11-16 21:15:51 UTC)
- **Docker Image Rebuilt**: ❓ Need to verify
- **Lambda Updated**: ❓ Need to verify
- **Validation Active**: ❓ Need to verify in logs

## Reproduction

See `dev.reproduce_chunk_40_failure.py` for a script to reproduce this issue locally.

## Solution

### Short-term (Immediate)

1. **Re-process Failed Batches**: The batch that created delta `f0d0bf087b8842b39025fcfce3e4a545` should be re-processed
2. **Verify Deployment**: Ensure Docker image was rebuilt and Lambda is using new image
3. **Monitor Logs**: Check for validation messages in new executions

### Long-term (Prevention)

1. **Validation Active**: All new deltas will be validated after upload
2. **Automatic Retry**: Corrupted uploads will be retried automatically
3. **Better Error Handling**: Consider skipping corrupted deltas and continuing with others (with alerting)

## Related Documentation

- `docs/DELTA_CORRUPTION_ERROR_ANALYSIS.md` - General delta corruption analysis
- `docs/DELTA_VALIDATION_FIX_SUMMARY.md` - Validation fix implementation
- `docs/PRODUCE_EMBEDDING_DELTA_VALIDATION_FIX.md` - produce_embedding_delta fix
- `docs/CONTAINER_LAMBDA_DEPLOYMENT.md` - Container Lambda deployment process

