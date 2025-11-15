# File Polling Verification Guide

## Overview

This document explains how to verify that the file polling implementation is deployed and working correctly.

## Current Status

**❌ File Polling Not Verified**

As of the last check:
- No "Closing ChromaDB client" logs found (should be INFO level)
- No "File unlocked after polling" logs found (DEBUG level)
- No "File unlock timeout" logs found (WARNING level)

This suggests the file polling code **has not been deployed yet**.

## Logging Levels

The file polling implementation uses different log levels:

### INFO Level (Should Always Appear)
- `"Closing ChromaDB client"` - Logged when `close_chromadb_client()` is called
- `"ChromaDB client closed successfully"` - Logged after successful close

### DEBUG Level (May Not Appear)
- `"File unlocked after polling"` - Only logged if file was locked and then unlocked
- `"HNSW file still locked (non-critical)"` - Only logged if HNSW files are locked
- `"Could not determine persist directory, used fallback delay"` - Only logged if persist directory can't be determined

### WARNING Level (Should Appear on Issues)
- `"File unlock timeout"` - Logged if file doesn't unlock within `max_wait` time
- `"Files still locked after max wait time"` - Logged if files are still locked after polling

## How to Verify File Polling is Working

### Step 1: Check if Code is Deployed

```bash
# Find the compaction Lambda function name
aws lambda list-functions --query 'Functions[?contains(FunctionName, `vector-compact`)].FunctionName' --output text

# Check the Lambda code (should contain wait_for_file_unlock function)
aws lambda get-function --function-name <function-name> --query 'Code.Location' --output text
```

### Step 2: Check CloudWatch Logs

```bash
# Set the log group name
LOG_GROUP="/aws/lambda/embedding-vector-compact-lambda-dev-377c856"

# Search for "Closing ChromaDB client" messages (INFO level - should always appear)
aws logs filter-log-events \
  --log-group-name "$LOG_GROUP" \
  --filter-pattern "Closing ChromaDB client" \
  --start-time $(date -u -d '2 hours ago' +%s)000

# Search for file unlock messages
aws logs filter-log-events \
  --log-group-name "$LOG_GROUP" \
  --filter-pattern "unlock" \
  --start-time $(date -u -d '2 hours ago' +%s)000
```

### Step 3: Check Log Level Configuration

The Lambda environment variable `LOG_LEVEL` should be set to `INFO` or `DEBUG`:

```bash
aws lambda get-function-configuration \
  --function-name <function-name> \
  --query 'Environment.Variables.LOG_LEVEL'
```

If `LOG_LEVEL` is set to `WARNING` or `ERROR`, DEBUG and INFO messages won't appear.

### Step 4: Run a Test Execution

1. **Clean environment:**
   ```bash
   python3 dev.cleanup_for_testing.py --env dev
   ```

2. **Start ingestion:**
   ```bash
   ./scripts/start_ingestion_dev.sh word
   ```

3. **Monitor logs in real-time:**
   ```bash
   aws logs tail /aws/lambda/embedding-vector-compact-lambda-dev-377c856 --follow
   ```

4. **Look for these messages:**
   - ✅ `"Closing ChromaDB client"` - Confirms code path is executed
   - ✅ `"File unlocked after polling"` - Confirms file polling worked (if files were locked)
   - ⚠️ `"File unlock timeout"` - Indicates files took too long to unlock
   - ⚠️ `"Files still locked after max wait time"` - Indicates polling failed

## Expected Behavior

### If File Polling is Working:

1. **Files unlock quickly (< 100ms):**
   - No polling logs (files unlock on first check)
   - `"Closing ChromaDB client"` appears
   - `"ChromaDB client closed successfully"` appears
   - No timeout warnings

2. **Files take time to unlock (100ms - 2s):**
   - `"File unlocked after polling"` appears with `elapsed_ms` and `checks` count
   - `"Closing ChromaDB client"` appears
   - `"ChromaDB client closed successfully"` appears

3. **Files don't unlock (> 2s):**
   - `"File unlock timeout"` appears
   - `"Files still locked after max wait time"` appears
   - Execution may fail with file locking errors

### If File Polling is NOT Working:

1. **Code not deployed:**
   - No `"Closing ChromaDB client"` messages
   - Old error messages persist ("unable to open database file", "Too many open files")

2. **Code deployed but not called:**
   - No `"Closing ChromaDB client"` messages
   - Execution fails before reaching close logic

3. **Log level too high:**
   - `"Closing ChromaDB client"` may appear (INFO level)
   - `"File unlocked after polling"` won't appear (DEBUG level)
   - Need to check `LOG_LEVEL` environment variable

## Proof of Testing

To prove file polling was tested:

1. **Deploy the code** - Verify Lambda function code includes `wait_for_file_unlock()` and `wait_for_chromadb_files_unlocked()`

2. **Run an execution** - Execute the step function and monitor logs

3. **Capture evidence:**
   - Screenshot/logs showing `"Closing ChromaDB client"` messages
   - Screenshot/logs showing `"File unlocked after polling"` with timing data
   - Execution results showing improved success rate

4. **Compare before/after:**
   - Before: "unable to open database file" errors
   - After: Successful executions with file polling logs

## Current Issue

**The file polling code exists in the repository but appears to not be deployed.**

**Evidence:**
- No `"Closing ChromaDB client"` logs found (should be INFO level)
- Recent executions still show "unable to open database file" and "Too many open files" errors
- Log streams are from August 21, 2025 (old)

**Next Steps:**
1. Verify the code is committed to the repository
2. Deploy the updated Lambda function
3. Run a test execution
4. Verify logs show file polling messages

## Code Locations

- File polling functions: `infra/embedding_step_functions/unified_embedding/handlers/compaction.py`
  - `wait_for_file_unlock()` - Lines 71-124
  - `wait_for_chromadb_files_unlocked()` - Lines 127-174
  - `close_chromadb_client()` - Lines 177-307 (calls file polling)

- Call sites: `compaction.py`
  - Line 1049: `process_chunk_handler` - Before uploading chunks
  - Line 1178: `download_and_merge_delta` - In finally block
  - Line 1328: `perform_intermediate_merge` - After processing chunks
  - Line 1360: `perform_intermediate_merge` - Before uploading intermediate snapshot
  - Line 1728: `perform_final_merge` - After processing chunks
  - Line 1745: `perform_final_merge` - Before uploading final snapshot

