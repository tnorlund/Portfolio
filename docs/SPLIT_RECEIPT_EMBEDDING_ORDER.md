# Order of Operations for Split Receipt Embeddings and Labels

## Overview

When splitting receipts, we need to ensure embeddings are created in ChromaDB before labels are added, since labels update metadata on existing embeddings.

## Current Architecture

### 1. Realtime Embedding Flow

```
1. Create embeddings in realtime
   ├─ embed_lines_realtime() → generates vectors
   ├─ embed_words_realtime() → generates vectors
   └─ Creates local ChromaDB delta directories

2. Upload deltas to S3
   ├─ lines/delta/{run_id}/delta.tar.gz
   └─ words/delta/{run_id}/delta.tar.gz

3. Create CompactionRun in DynamoDB
   └─ Triggers DynamoDB stream event (INSERT)

4. DynamoDB Stream → SQS Queue
   └─ Stream processor sends compaction messages

5. Enhanced Compactor processes
   ├─ Downloads snapshot from EFS/S3
   ├─ Merges delta into snapshot
   └─ Atomically promotes new snapshot
```

### 2. Label Update Flow

```
1. ReceiptWordLabel created/updated in DynamoDB
   └─ Triggers DynamoDB stream event (INSERT/MODIFY)

2. DynamoDB Stream → SQS Queue
   └─ Stream processor sends label update messages

3. Enhanced Compactor processes
   ├─ Gets embedding from ChromaDB (collection.get())
   ├─ Updates metadata with label information
   └─ Atomically promotes updated snapshot
```

## Key Question: Do We Need to Wait?

### Answer: **YES, we need to wait for embeddings to exist in ChromaDB before adding labels**

**Reason**: The `update_word_labels()` function in `compaction/operations.py` does:

```python
# Get the specific record from ChromaDB
result = collection.get(ids=[chromadb_id], include=["metadatas"])
if not result["ids"]:
    logger.warning("Word embedding not found", chromadb_id=chromadb_id)
    return 0  # ← Fails silently if embedding doesn't exist
```

If the embedding doesn't exist yet, the label update will fail silently (returns 0).

## Correct Order of Operations for Split Receipts

### Option A: Sequential (Recommended for Split Script)

```
1. Create new receipts in DynamoDB
   └─ Receipt, ReceiptLine, ReceiptWord entities

2. Embed in realtime
   ├─ Create embeddings
   ├─ Upload deltas to S3
   ├─ Create CompactionRun
   └─ Wait for compaction to complete

3. Add labels
   ├─ Create ReceiptWordLabel entities
   └─ Labels will update ChromaDB metadata via streams
```

**Implementation**:
- After creating receipts, call embedding functions
- Create CompactionRun
- Poll for compaction completion (check CompactionRun status)
- Then add labels

### Option B: Parallel with Retry (More Complex)

```
1. Create new receipts in DynamoDB
2. Embed in realtime + Create CompactionRun
3. Add labels immediately (they'll queue via streams)
4. Enhanced compactor will:
   - First: Merge embeddings (from CompactionRun)
   - Then: Update labels (from label stream messages)
```

**Risk**: If label update arrives before embedding merge, it will fail silently.

## Recommended Approach for Split Script

### Option A: Use NDJSON Queue (Simpler - Recommended)

The NDJSON worker handles embedding creation automatically. We just need to:
1. Export NDJSON and queue
2. Poll for CompactionRun completion
3. Add labels

**Step-by-Step:**

1. **Create Receipt Records** (already done)
   ```python
   client.add_receipt(receipt)
   client.add_receipt_lines(receipt_lines)
   client.add_receipt_words(receipt_words)
   ```

2. **Export NDJSON and Queue** (already in script)
   ```python
   export_receipt_ndjson_and_queue(...)
   # NDJSON worker will:
   # - Create embeddings
   # - Upload deltas
   # - Create CompactionRun
   ```

3. **Wait for CompactionRun to Complete**
   ```python
   # Poll for most recent CompactionRun for this receipt
   # NDJSON worker creates it, so we need to find it
   max_wait_seconds = 300  # 5 minutes
   poll_interval = 5  # seconds

   start_time = time.time()
   run_id = None

   while time.time() - start_time < max_wait_seconds:
       # Get most recent CompactionRun for this receipt
       runs, _ = client.list_compaction_runs_for_receipt(image_id, receipt_id, limit=1)
       if runs:
           run = runs[0]
           run_id = run.run_id

           # Check if both collections are completed
           if (run.lines_state == "COMPLETED" and
               run.words_state == "COMPLETED"):
               break
           elif (run.lines_state == "FAILED" or
                 run.words_state == "FAILED"):
               raise Exception(f"Compaction failed: {run}")

       time.sleep(poll_interval)

   if not run_id:
       raise Exception("CompactionRun not found after waiting")
   ```

4. **Add Labels** (after embeddings exist)
   ```python
   # Now safe to add labels
   client.add_receipt_word_labels(receipt_labels)
   # Labels will update ChromaDB metadata via streams
   ```

### Option B: Embed in Realtime (More Control)

If we want full control over the embedding process:

1. **Create Receipt Records**
2. **Embed in Realtime and Create CompactionRun**
   ```python
   run_id = str(uuid.uuid4())
   # ... embedding code ...
   client.add_compaction_run(compaction_run)
   ```
3. **Wait for Completion** (same as Option A, but we know run_id)
4. **Add Labels**

## Alternative: Use NDJSON Queue (Simpler)

Instead of embedding in realtime, we can:

1. **Export NDJSON and Queue** (already in script)
   ```python
   export_receipt_ndjson_and_queue(...)
   ```

2. **NDJSON Worker will**:
   - Create embeddings
   - Upload deltas
   - Create CompactionRun
   - All handled automatically

3. **Then add labels** (after NDJSON processing completes)

This is simpler because:
- NDJSON worker handles embedding creation
- We just need to wait for CompactionRun to complete
- Then add labels

## Implementation in Split Script

### Current Script Flow:

```python
# 1. Create receipts (done)
# 2. Export NDJSON and queue (done)
# 3. Add labels (NEEDS TO WAIT)
```

### Updated Flow:

```python
# 1. Create receipts
# 2. Export NDJSON and queue
# 3. Wait for CompactionRun to complete
# 4. Add labels
```

## Code Changes Needed

1. **Add CompactionRun polling function**:
   ```python
   def wait_for_compaction_complete(
       client: DynamoClient,
       image_id: str,
       receipt_id: int,
       run_id: str,
       max_wait_seconds: int = 300,
       poll_interval: int = 5,
   ) -> bool:
       """Wait for compaction to complete."""
       # Implementation
   ```

2. **Update split_receipt.py**:
   - After exporting NDJSON, wait for compaction
   - Then add labels

3. **Handle CompactionRun creation**:
   - NDJSON worker creates CompactionRun
   - We need to get the run_id somehow
   - Or: Create CompactionRun ourselves and let NDJSON worker update it

## Questions to Resolve

1. **How do we get the run_id from NDJSON processing?**
   - Option A: NDJSON worker creates CompactionRun, we poll for it
   - Option B: We create CompactionRun first, NDJSON worker updates it
   - Option C: NDJSON worker returns run_id (requires API change)

2. **Should we embed in realtime or use NDJSON queue?**
   - NDJSON queue is simpler (already implemented)
   - Realtime gives more control but more complex

3. **What if compaction fails?**
   - Should we retry?
   - Should we skip labels?
   - Should we fail the whole split?

## Recommendation

**Use NDJSON queue approach**:
1. Export NDJSON and queue (already done)
2. Poll for CompactionRun completion (need to implement)
3. Add labels after compaction completes

This is simpler and reuses existing infrastructure.

