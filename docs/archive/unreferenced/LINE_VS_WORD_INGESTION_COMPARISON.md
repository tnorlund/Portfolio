# Line vs Word Ingestion Step Functions Comparison

## Overview

Both line and word ingestion step functions follow the same architecture and payload strategies. They are nearly identical except for naming and the database parameter passed to compaction.

## Payload Strategy: S3/Manifest Approach

### ✅ Both Use the Same Strategy

Both workflows use the **exact same S3/manifest payload strategy** to handle Step Functions' 256KB payload limit:

1. **PollBatches Map State**
   - Each individual `PollBatch` Lambda uploads its full result to S3
   - Returns only a small S3 reference: `{"batch_id": "...", "result_s3_key": "...", "result_s3_bucket": "..."}`
   - This keeps the Map state's aggregated output small (~100 bytes per batch)

2. **NormalizePollBatchesData Handler**
   - Downloads all individual S3 results
   - Combines them into a single array
   - Uploads combined results to S3
   - Returns S3 reference: `{"poll_results_s3_key": "...", "poll_results_s3_bucket": "..."}`

3. **SplitIntoChunks Handler**
   - Loads `poll_results` from S3 if `poll_results_s3_key` is provided
   - Splits into chunks
   - Uploads chunks to S3 if payload exceeds limit
   - Returns chunks array or S3 reference

4. **ProcessChunks Map State**
   - Each chunk processor downloads from S3 if `chunks_s3_key` is provided
   - Processes chunk and uploads intermediate result to S3

### Key Differences (Naming Only)

| Aspect | Line Workflow | Word Workflow |
|--------|---------------|--------------|
| State names | `PollBatches`, `NormalizePollBatchesData`, `SplitIntoChunks`, `ProcessChunksInParallel` | `PollWordBatches`, `NormalizePollWordBatchesData`, `SplitWordIntoChunks`, `ProcessWordChunksInParallel` |
| Lambda handler | `embedding-line-poll` | `embedding-word-poll` |
| Database parameter | `"database": "lines"` | `"database": "words"` |
| Collection name | `"receipt_lines"` | `"receipt_words"` |

## ChromaDB Client Closing: Where It's Used

### ✅ All ChromaDB Clients Now Use the Close Hack

#### 1. **Delta Creation (PollBatch Lambdas)**
   - **Location**: `receipt_label/receipt_label/embedding/line/poll.py` and `receipt_label/receipt_label/embedding/word/poll.py`
   - **Function**: `save_line_embeddings_as_delta()` and `save_word_embeddings_as_delta()`
   - **Calls**: `produce_embedding_delta()` → `chroma.persist_and_upload_delta()`
   - **Status**: ✅ **FIXED** - `persist_and_upload_delta()` now calls `_close_client_for_upload()` before uploading

#### 2. **Delta Upload (chromadb_client.py)**
   - **Location**: `receipt_label/receipt_label/vector_store/client/chromadb_client.py`
   - **Function**: `persist_and_upload_delta()`
   - **Status**: ✅ **FIXED** - Calls `_close_client_for_upload()` before uploading files to S3
   - **Implementation**:
     ```python
     # CRITICAL: Close ChromaDB client BEFORE uploading
     self._close_client_for_upload()
     # Then upload files...
     ```

#### 3. **Chunk Processing (Compaction Handler)**
   - **Location**: `infra/embedding_step_functions/unified_embedding/handlers/compaction.py`
   - **Function**: `process_chunk_deltas()`
   - **Status**: ✅ **ALREADY HAS IT** - Calls `close_chromadb_client()` before uploading intermediate chunks

#### 4. **Delta Merging (Compaction Handler)**
   - **Location**: `infra/embedding_step_functions/unified_embedding/handlers/compaction.py`
   - **Function**: `download_and_merge_delta()`
   - **Status**: ✅ **ALREADY HAS IT** - Calls `close_chromadb_client()` before cleanup

#### 5. **Group Merging (Compaction Handler)**
   - **Location**: `infra/embedding_step_functions/unified_embedding/handlers/compaction.py`
   - **Function**: `perform_intermediate_merge()`
   - **Status**: ✅ **ALREADY HAS IT** - Calls `close_chromadb_client()` for each chunk client and main client

#### 6. **Final Merge (Compaction Handler)**
   - **Location**: `infra/embedding_step_functions/unified_embedding/handlers/compaction.py`
   - **Function**: `perform_final_merge()`
   - **Status**: ✅ **ALREADY HAS IT** - Calls `close_chromadb_client()` before uploading final snapshot

## Workflow Comparison

### Step-by-Step Flow (Both Identical)

```
1. ListPendingBatches
   ↓
2. CheckPendingBatches (if > 0)
   ↓
3. NormalizePendingBatches (Pass state - prepares data)
   ↓
4. PollBatches/PollWordBatches (Map state - parallel polling)
   ├─ Each PollBatch Lambda:
   │  ├─ Polls OpenAI batch
   │  ├─ Creates delta file (with close hack ✅)
   │  ├─ Uploads full result to S3
   │  └─ Returns S3 reference
   ↓
5. NormalizePollBatchesData (Task state)
   ├─ Downloads individual S3 results
   ├─ Combines them
   └─ Uploads combined result to S3
   ↓
6. SplitIntoChunks (Task state)
   ├─ Loads from S3 if needed
   ├─ Splits into chunks
   └─ Uploads chunks to S3 if needed
   ↓
7. CheckChunksSource (Choice state)
   ├─ If use_s3=True → LoadChunksFromS3
   └─ Otherwise → CheckForChunks
   ↓
8. ProcessChunksInParallel (Map state)
   ├─ Each ProcessSingleChunk Lambda:
   │  ├─ Downloads chunk from S3 if needed
   │  ├─ Processes chunk (with close hack ✅)
   │  └─ Uploads intermediate result to S3
   ↓
9. GroupChunksForMerge (Task state)
   ├─ Groups chunks for parallel merging
   └─ Uploads groups to S3 if needed
   ↓
10. MergeChunkGroups (Map state)
    ├─ Each MergeChunkGroup Lambda:
    │  ├─ Downloads group from S3 if needed
    │  ├─ Merges chunks (with close hack ✅)
    │  └─ Uploads merged result to S3
    ↓
11. FinalMerge (Task state)
    ├─ Downloads all intermediate results from S3
    ├─ Merges everything (with close hack ✅)
    └─ Uploads final snapshot to S3
    ↓
12. MarkBatchesComplete (Task state)
```

## Key Findings

### ✅ Both Workflows Are Identical in Structure

1. **Same payload strategy**: Both use S3 references to avoid 256KB limits
2. **Same handlers**: Both use the same Lambda handlers (just different configs)
3. **Same compaction logic**: Both use the same compaction handler with `database` parameter

### ✅ All ChromaDB Clients Use the Close Hack

| Location | Function | Status |
|----------|----------|--------|
| `chromadb_client.py` | `persist_and_upload_delta()` | ✅ **FIXED** (just added) |
| `compaction.py` | `process_chunk_deltas()` | ✅ Already had it |
| `compaction.py` | `download_and_merge_delta()` | ✅ Already had it |
| `compaction.py` | `perform_intermediate_merge()` | ✅ Already had it |
| `compaction.py` | `perform_final_merge()` | ✅ Already had it |

### The Fix We Just Applied

The **only missing piece** was in `persist_and_upload_delta()` which is called when creating delta files in the PollBatch Lambdas. This is now fixed:

```python
# receipt_label/receipt_label/vector_store/client/chromadb_client.py

def persist_and_upload_delta(self, ...):
    # ... persist data ...

    # CRITICAL: Close ChromaDB client BEFORE uploading
    self._close_client_for_upload()

    # Now safe to upload files
    for file_path in files_to_upload:
        s3_client.upload_file(...)
```

This ensures delta files are uploaded only after SQLite files are unlocked, preventing the "unable to open database file" errors.

## Summary

- ✅ **Both workflows use identical S3/manifest payload strategies**
- ✅ **All ChromaDB clients now use the close hack**
- ✅ **The fix is complete and ready for testing**

The only difference between line and word workflows is naming and the `database` parameter ("lines" vs "words"), which is passed to the compaction handler to determine which ChromaDB collection to use.

