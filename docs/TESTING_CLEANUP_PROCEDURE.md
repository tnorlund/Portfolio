# Testing Cleanup Procedure

## Overview

This document outlines the procedure for cleaning up the environment before testing the ChromaDB client closing fix. This ensures a clean slate for verifying that the fix resolves SQLite file locking issues.

## What Gets Cleaned

### 1. S3 Snapshots
- **What**: Deletes all ChromaDB snapshots (words and lines) from S3
- **Why**: Removes corrupted snapshots that were created before the fix
- **Impact**: System will rebuild snapshots from deltas on next ingestion run
- **EFS**: EFS will automatically sync from new S3 snapshots (no manual cleanup needed)

### 2. DynamoDB Word Embedding Statuses
- **What**: Resets all `ReceiptWord.embedding_status` to `NONE`
- **Why**: Allows words to be re-processed through the embedding pipeline
- **Impact**: All words will be picked up for embedding again

### 3. DynamoDB Batch Summaries
- **What**: Resets all `WORD_EMBEDDING` `BatchSummary.status` to `PENDING`
- **Why**: Allows batch processing to restart from scratch
- **Impact**: All batches will be re-processed through the step function

### 4. Step Function Executions (Optional)
- **What**: Stops any in-flight step function executions
- **Why**: Prevents conflicts with new executions
- **Impact**: Current executions are stopped (use `--stop-executions` flag)

## What Does NOT Get Cleaned

### EFS (Elastic File System)
- **Why**: EFS automatically syncs from S3 when new snapshots are created
- **Note**: No manual cleanup needed - the system handles EFS sync automatically

### S3 Deltas
- **Why**: Deltas are preserved - they're needed to rebuild snapshots
- **Note**: Only snapshots are deleted, not the delta files

### DynamoDB Receipt Data
- **Why**: Receipt metadata, words, and lines are preserved
- **Note**: Only embedding statuses and batch summaries are reset

## Cleanup Script

### Usage

```bash
# Dry run (see what would be done)
python dev.cleanup_for_testing.py --env dev --dry-run

# Actual cleanup
python dev.cleanup_for_testing.py --env dev

# Include stopping step function executions
python dev.cleanup_for_testing.py --env dev --stop-executions

# Skip S3 cleanup (only reset DynamoDB)
python dev.cleanup_for_testing.py --env dev --skip-s3

# Skip DynamoDB cleanup (only delete S3 snapshots)
python dev.cleanup_for_testing.py --env dev --skip-dynamo
```

### What It Does

1. **Loads Pulumi configuration** to get bucket and table names
2. **Deletes S3 snapshots** for both words and lines collections
3. **Resets word embedding statuses** to NONE
4. **Resets batch summaries** to PENDING
5. **Optionally stops step function executions** (if `--stop-executions` is used)

## Complete Testing Procedure

### Step 1: Clean Up Environment

```bash
# Run cleanup script
python dev.cleanup_for_testing.py --env dev --stop-executions
```

**Expected Output:**
- S3 snapshots deleted (words and lines)
- Word embedding statuses reset
- Batch summaries reset
- Running executions stopped (if any)

### Step 2: Deploy the Fix

Deploy the updated Lambda functions with the `close_chromadb_client` fix:

```bash
# Deploy container-based lambdas
pulumi up --stack tnorlund/portfolio/dev
```

**What's Being Deployed:**
- Updated compaction handlers with `close_chromadb_client` calls
- Empty snapshot initialization logic
- Proper SQLite file closing before uploads

### Step 3: Start Ingestion

```bash
# Start word embedding ingestion
./scripts/start_ingestion_dev.sh word
```

**What Happens:**
- Step function starts processing batches
- Words are embedded and deltas are created
- Chunks are merged (with proper client closing)
- Final merge creates new snapshot (with proper client closing)
- Snapshot is uploaded to S3 (SQLite files are closed first)

### Step 4: Monitor Execution

Monitor the step function execution:

```bash
# Get latest execution ARN
aws stepfunctions list-executions \
  --state-machine-arn "arn:aws:states:us-east-1:681647709217:stateMachine:word-ingest-sf-dev-8fa425a" \
  --max-results 1 \
  --query "executions[0].executionArn" \
  --output text

# Check execution status
aws stepfunctions describe-execution \
  --execution-arn "<EXECUTION_ARN>" \
  --query "{status:status, output:output}" \
  --output json
```

**What to Look For:**
- ✅ Execution status: `SUCCEEDED`
- ✅ All chunk groups succeed (no "unable to open database file" errors)
- ✅ Final merge succeeds with expected number of embeddings
- ✅ No SQLite corruption errors

### Step 5: Verify Snapshots

After the step function completes, verify the snapshots:

```bash
# Verify snapshots contain recent receipts
python dev.verify_chromadb_snapshots.py --env dev --limit 10
```

**What to Check:**
- ✅ Snapshots download without corruption errors
- ✅ Recent receipts are found in snapshots
- ✅ No "database disk image is malformed" errors

## Expected Results After Fix

### Before Fix (Current State)
- ❌ Some chunk groups fail with "unable to open database file"
- ❌ Some chunk groups fail with "Error loading hnsw index"
- ❌ Final merge succeeds but with fewer embeddings than expected
- ❌ Snapshots may be corrupted

### After Fix (Expected)
- ✅ All chunk groups succeed
- ✅ No SQLite file locking errors
- ✅ Final merge succeeds with all expected embeddings
- ✅ Snapshots are healthy and can be downloaded/verified

## Troubleshooting

### If Cleanup Fails

**S3 Deletion Issues:**
- Check AWS permissions
- Verify bucket name is correct
- Check if objects are in use (may need to wait)

**DynamoDB Reset Issues:**
- Check table name is correct
- Verify DynamoDB permissions
- Check if there are too many items (may need pagination)

### If Ingestion Still Fails

**Check CloudWatch Logs:**
```bash
# Find log group for compaction lambda
aws logs describe-log-groups \
  --log-group-name-prefix "/aws/lambda/embedding" \
  --query "logGroups[*].logGroupName" \
  --output table

# Get recent logs
aws logs tail <LOG_GROUP_NAME> --follow
```

**Look For:**
- "Closing ChromaDB client" messages (confirms fix is deployed)
- SQLite errors (should be gone)
- File locking errors (should be gone)

### If Snapshots Are Still Corrupted

1. **Verify fix is deployed**: Check Lambda function code version
2. **Check EFS sync**: EFS should sync from new S3 snapshots automatically
3. **Re-run cleanup**: Delete snapshots and try again
4. **Check for concurrent access**: Ensure only one compaction runs at a time

## Related Documentation

- [ChromaDB Client Closing Workaround](./CHROMADB_CLIENT_CLOSING_WORKAROUND.md)
- [ChromaDB Upload Debug Flow](./CHROMADB_UPLOAD_DEBUG_FLOW.md)
- [Testing ChromaDB Fix](./TESTING_CHROMADB_FIX.md)

## Scripts Used

- `dev.cleanup_for_testing.py` - Comprehensive cleanup script
- `dev.delete_corrupted_snapshots.py` - S3 snapshot deletion only
- `dev.reset_word_embeddings.py` - DynamoDB reset only
- `dev.verify_chromadb_snapshots.py` - Snapshot verification

