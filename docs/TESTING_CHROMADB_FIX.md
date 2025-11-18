# Testing ChromaDB Client Close Fix

## Current State

✅ **Reset Complete:**
- 55,846 words reset to `embedding_status = NONE`
- 397 WORD_EMBEDDING batch summaries reset to `status = PENDING`

✅ **Existing Snapshots:**
- **Words**: Healthy (version 20251114_221449, created 8.6 minutes ago)
- **Lines**: May be corrupted (we found corruption earlier)

## Safe to Run Ingestion

**Yes, it's safe to run the word embedding step function!**

### Why It's Safe:

1. **ChromaDB handles duplicates**: The `upsert_vectors()` method uses ChromaDB's `upsert()` which:
   - Updates existing IDs if they already exist
   - Inserts new IDs if they don't exist
   - Has duplicate-safe handling (delete + retry if needed)

2. **Compaction merges deltas**: The compaction process:
   - Downloads the existing snapshot
   - Merges new deltas into it
   - Creates a new snapshot version
   - The existing snapshot is not modified (new version created)

3. **Existing snapshot is healthy**: The Words snapshot passed integrity checks

## Testing the Fix

### What We're Testing:

The fix ensures ChromaDB clients are closed before uploading snapshots. This should prevent SQLite corruption.

### Steps:

1. **Run word embedding ingestion:**
   ```bash
   ./scripts/start_ingestion_dev.sh word
   ```

2. **Monitor the process:**
   - Watch CloudWatch logs for compaction Lambda
   - Look for "Closing ChromaDB client" messages
   - Check for any errors

3. **Verify new snapshots:**
   - After compaction completes, check snapshot integrity
   - Use `dev.verify_chromadb_snapshots.py` to verify

### Expected Behavior:

- ✅ New deltas created and uploaded to S3
- ✅ Compaction Lambda processes deltas
- ✅ ChromaDB client closed before snapshot upload
- ✅ New snapshot created and uploaded
- ✅ **New snapshot passes SQLite integrity check** (this is what we're testing!)

## If Corruption Still Occurs

If the new snapshot is still corrupted:

1. **Check CloudWatch logs** for:
   - "Closing ChromaDB client" messages
   - Any errors during client close
   - Timeout errors during upload

2. **Verify client close is working:**
   - Check if `close_chromadb_client()` is being called
   - Verify GC is running
   - Check if file handles are released

3. **Possible additional fixes:**
   - Increase delay after GC
   - Add explicit SQLite connection closing
   - Check for concurrent operations

## Monitoring Commands

```bash
# Watch compaction Lambda logs
aws logs tail /aws/lambda/chromadb-dev-lambdas-enhanced-compaction-62f207f --follow

# Check for client close messages
aws logs filter-log-events \
  --log-group-name /aws/lambda/chromadb-dev-lambdas-enhanced-compaction-62f207f \
  --filter-pattern "Closing ChromaDB client" \
  --max-items 20

# Verify snapshot integrity after compaction
python3 dev.verify_chromadb_snapshots.py --env dev --limit 5
```

