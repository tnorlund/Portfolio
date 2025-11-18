# ChromaDB Client Close Fix

## Problem

ChromaDB SQLite files were being corrupted during snapshot upload because the ChromaDB `PersistentClient` keeps SQLite database files open/locked. When uploading to S3, we were uploading files that were still being written to, causing corruption.

**Evidence:**
- Lines snapshot: SQLite integrity check failed ("database disk image is malformed")
- Words snapshot: Healthy (same process, different timing)
- Both snapshots created within 2 seconds of each other

## Root Cause

The ChromaDB client was not being closed before uploading snapshots. SQLite files remain locked while the ChromaDB `PersistentClient` has an active connection.

## Solution

### 1. Added `close_chromadb_client()` Helper Function

Located in `infra/chromadb_compaction/lambdas/enhanced_compaction_handler.py`:

```python
def close_chromadb_client(client: Any, collection_name: str) -> None:
    """
    Properly close ChromaDB client to ensure SQLite files are flushed and unlocked.

    This is critical to prevent corruption when uploading/copying SQLite files
    that are still being written to by an active ChromaDB connection.
    """
    if client is None:
        return

    try:
        logger.info(
            "Closing ChromaDB client",
            collection=collection_name,
            persist_directory=getattr(client, 'persist_directory', None)
        )

        # Clear collections cache
        if hasattr(client, '_collections'):
            client._collections.clear()

        # Clear client reference
        if hasattr(client, '_client') and client._client is not None:
            # ChromaDB PersistentClient doesn't have explicit close(), but clearing
            # the reference allows Python GC to close SQLite connections
            client._client = None

        # Force garbage collection to ensure SQLite connections are closed
        import gc
        gc.collect()

        # Small delay to ensure file handles are released by OS
        import time as _time
        _time.sleep(0.1)

        logger.info(
            "ChromaDB client closed successfully",
            collection=collection_name
        )
    except Exception as e:
        logger.warning(
            "Error closing ChromaDB client (non-critical)",
            error=str(e),
            collection=collection_name
        )
```

### 2. Close Client Before Upload

**Before acquiring lock for upload:**
```python
# Phase B: Optimized upload with minimal lock time
# CRITICAL: Close ChromaDB client BEFORE acquiring lock to ensure SQLite files are flushed and unlocked
# This prevents corruption when uploading files that are still being written to
close_chromadb_client(chroma_client, collection.value)
chroma_client = None
```

### 3. Ensure Cleanup in Finally Block

**Added cleanup in finally block:**
```python
finally:
    # CRITICAL: Ensure ChromaDB client is closed even if errors occurred
    # This prevents SQLite file locks from persisting
    if 'chroma_client' in locals() and chroma_client is not None:
        try:
            close_chromadb_client(chroma_client, collection.value)
        except Exception as e:
            logger.warning(
                "Error closing ChromaDB client in finally block",
                error=str(e),
                collection=collection.value
            )
```

## How It Works

1. **Clear Collections Cache**: Removes references to collection objects
2. **Clear Client Reference**: Sets `_client = None` to allow Python GC to close SQLite connections
3. **Force Garbage Collection**: `gc.collect()` ensures SQLite connections are closed immediately
4. **Small Delay**: `time.sleep(0.1)` gives OS time to release file handles
5. **Then Upload**: Only after client is closed do we acquire lock and upload

## Locking Strategy

The fix maintains the existing locking strategy:

1. **Phase 1**: Download snapshot, validate pointer (OFF-LOCK)
2. **Phase 2**: Merge deltas, apply updates (OFF-LOCK)
3. **Close Client**: Clear references, force GC (OFF-LOCK)
4. **Phase 3**: Acquire lock, validate pointer, upload snapshot (UNDER-LOCK)

This ensures:
- Minimal lock time (only during upload)
- No concurrent modifications (lock prevents race conditions)
- SQLite files are unlocked before upload (prevents corruption)

## Testing

After deploying this fix, verify:

1. **Snapshot Integrity**: Check SQLite integrity after upload
2. **No Corruption**: Verify snapshots can be opened and queried
3. **Lock Behavior**: Ensure locks are acquired/released correctly
4. **Performance**: Verify upload times are not significantly impacted

## Related Files

- `infra/chromadb_compaction/lambdas/enhanced_compaction_handler.py`: Main fix location
- `receipt_label/receipt_label/vector_store/client/chromadb_client.py`: ChromaDBClient implementation
- `docs/CHROMADB_UPLOAD_DEBUG_FLOW.md`: Complete flow documentation
- `docs/DEBUG_FINDINGS_2025_11_14.md`: Debug findings

## Future Improvements

1. **Add SQLite Lock Detection**: Check if files are locked before upload
2. **Add Integrity Verification**: Verify SQLite integrity before upload
3. **Context Manager**: Consider adding `__enter__`/`__exit__` to ChromaDBClient for automatic cleanup
4. **Retry Logic**: Add retry logic if files are still locked after close

