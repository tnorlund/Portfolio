# Corrected Data Flow Analysis

## You Are Correct! ✅

**Old corrupted deltas CANNOT cause failures** because they are **never processed** by the step function.

## Actual Data Flow

### 1. Polling Phase (Fresh Data from OpenAI) ✅
```python
# receipt_label/receipt_label/embedding/line/poll.py
results = download_openai_batch_result(openai_batch_id)  # ← Fresh API call
delta_result = save_line_embeddings_as_delta(...)  # ← Creates NEW delta
return {
    "delta_key": delta_result["delta_key"],  # ← Returns ONLY the NEW delta
    ...
}
```

### 2. Step Function Collects ONLY New Deltas ✅
The step function collects `delta_key` values from the polling handler results. These are **ONLY** the deltas that were just created from fresh OpenAI data.

### 3. Compaction Processes ONLY New Deltas ✅
```python
# infra/embedding_step_functions/unified_embedding/handlers/compaction.py
def process_chunk_handler(event):
    delta_results = event.get("delta_results", [])  # ← Only deltas from current run
    for delta in delta_results:
        delta_key = delta["delta_key"]  # ← Process ONLY these deltas
        download_and_merge_delta(bucket, delta_key, ...)
```

**Key Point**: The compaction handler processes **ONLY** the deltas passed to it in `delta_results`, which are **ONLY** the deltas created in the current run from fresh OpenAI data.

## What CAN Cause Failures

### 1. Corrupted Snapshots (Not Deltas) ⚠️
The final merge downloads the existing snapshot and merges new deltas into it:
```python
# compaction.py::perform_final_merge()
download_snapshot_atomic(bucket, snapshot_key, temp_dir)  # ← Downloads existing snapshot
chroma_client = chromadb.PersistentClient(path=temp_dir)  # ← If snapshot corrupted, fails here
```

**Solution**: ✅ `dev.delete_corrupted_snapshots.py` deletes snapshots, forcing rebuild from deltas.

### 2. Corrupted NEW Deltas (Created in Current Run) ⚠️
If a delta is corrupted during creation (before validation fix), it will fail when compaction tries to process it:
```python
# compaction.py::download_and_merge_delta()
delta_client = chromadb.PersistentClient(path=delta_temp)  # ← Fails if delta corrupted
```

**Solution**: ✅ Validation fix prevents NEW deltas from being corrupted.

## Why Old Corrupted Deltas Don't Matter

1. **They're not in the list**: The polling handler only returns `delta_key` for deltas it just created
2. **They're not processed**: Compaction only processes deltas in `delta_results` from the current run
3. **They're not referenced**: The step function doesn't scan S3 for all deltas - it only uses what the polling handler returns

## Summary

| Artifact | Source | Processed? | Can Cause Failure? |
|----------|--------|------------|-------------------|
| **Fresh OpenAI Data** | OpenAI API | ✅ Every run | ❌ No (always fresh) |
| **New Deltas** | Current run | ✅ Yes | ⚠️ Yes (if corrupted during creation) |
| **Old Deltas** | Previous runs | ❌ **NO** | ❌ **NO** (never processed) |
| **Snapshots** | Merged from deltas | ✅ Yes (downloaded for merge) | ⚠️ Yes (if corrupted) |

## Conclusion

**You are absolutely correct**: Old corrupted deltas cannot cause failures because:
- ✅ The step function pulls fresh data from OpenAI every time
- ✅ Only NEW deltas (from current run) are processed
- ✅ Old deltas are never referenced or processed

The only artifacts that can cause failures are:
1. **Corrupted snapshots** (deleted by cleanup script)
2. **Corrupted NEW deltas** (prevented by validation fix)

Thank you for the correction! 🎯

