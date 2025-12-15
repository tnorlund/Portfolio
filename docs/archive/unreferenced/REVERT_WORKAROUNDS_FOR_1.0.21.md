# Reverting Workarounds for ChromaDB 1.0.21 Testing

## Overview

To test if ChromaDB 1.0.21 works without workarounds, we need to revert the workarounds we added for 1.3.x issues.

## Changes to Revert in `compaction.py`

### 1. Simplify `close_chromadb_client()` Function (lines 94-163)

**Current**: Complex function with SQLite direct closing, double GC, 0.5s delay

**Revert to**: Simple version (or remove entirely if 1.0.21 doesn't need it)

```python
def close_chromadb_client(client: Any, collection_name: Optional[str] = None) -> None:
    """Simple client cleanup for ChromaDB 1.0.21 (may not need workarounds)."""
    if client is None:
        return
    try:
        # Just clear references - 1.0.21 may handle cleanup better
        if hasattr(client, '_collections'):
            client._collections.clear()
        if hasattr(client, '_client'):
            client._client = None
        client = None
    except Exception:
        pass
```

### 2. Remove Main Client Flush After Chunk Merge (lines 1253-1284)

**Remove**: The entire flush block after merging chunk data

### 3. Remove Main Client Flush After Closing Chunk Client (lines 1318-1346)

**Remove**: The entire flush block in the finally block

### 4. Simplify File Verification (lines 924-1041)

**Current**: Complex verification with HNSW stability checks

**Revert to**: Simple check (or remove entirely)

```python
# Simple check - just verify SQLite files exist
# Remove HNSW stability checks, delays, etc.
```

### 5. Remove Delays

**Remove**: All `_time.sleep()` calls added for workarounds

## What to Keep

- ✅ Empty snapshot initialization (in `chroma_s3_helpers.py`) - useful feature
- ✅ Enhanced error logging - useful for debugging
- ✅ Basic client cleanup (simplified version)

## Testing Steps

1. **Update pyproject.toml** ✅ Done
2. **Revert workarounds** (see below)
3. **Clean environment**:
   ```bash
   python3 dev.cleanup_for_testing.py --env dev --stop-executions
   ```
4. **Deploy**:
   ```bash
   cd infra
   pulumi up --stack tnorlund/portfolio/dev
   ```
5. **Run step function**:
   ```bash
   ./scripts/start_ingestion_dev.sh word
   ```
6. **Monitor results**:
   ```bash
   # Check latest execution
   aws stepfunctions list-executions \
     --state-machine-arn "arn:aws:states:us-east-1:681647709217:stateMachine:word-ingest-sf-dev-8fa425a" \
     --region us-east-1 \
     --max-results 1
   ```

## Expected Results

**If 1.0.21 works without workarounds**:
- ✅ All 4 groups succeed
- ✅ No SQLite locking errors
- ✅ No HNSW corruption errors

**If 1.0.21 still has issues**:
- ⚠️ Restore workarounds
- ⚠️ Or try 1.2.2

