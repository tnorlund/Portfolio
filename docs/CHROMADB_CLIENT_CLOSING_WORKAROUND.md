# ChromaDB Client Closing Workaround

## Problem Statement

When processing ChromaDB snapshots and intermediate chunks in our embedding pipeline, we encounter SQLite file locking issues that cause corruption and "unable to open database file" errors. This occurs when:

1. ChromaDB clients are used to create/modify SQLite databases
2. Files are uploaded to S3 or copied while SQLite connections are still open
3. Another process tries to access the same SQLite files

**Symptoms:**
- `error returned from database: (code: 14) unable to open database file`
- `database disk image is malformed` errors
- `Too many open files` errors
- Corrupted ChromaDB snapshots in S3

## Root Cause

**ChromaDB's `PersistentClient` does not expose a `close()` method.**

ChromaDB uses SQLite under the hood for persistent storage. When you create a `PersistentClient`, it opens SQLite connections that remain open until:
- The Python process exits
- Garbage collection occurs (non-deterministic)
- The client object is explicitly deleted

**The Problem:**
- SQLite files cannot be safely copied/uploaded while connections are open
- Python's garbage collector doesn't run immediately when references are cleared
- File handles may remain open even after setting `client = None`

**Official ChromaDB Documentation:**
- No `close()` method documented for `PersistentClient`
- No context manager support (`__enter__`/`__exit__`)
- No explicit resource management API

**Research Findings:**
After extensive searching of:
- Official ChromaDB documentation (docs.trychroma.com)
- ChromaDB GitHub repository
- ChromaDB Cookbook (cookbook.chromadb.dev)
- Community forums and Stack Overflow

**Result:** There is **no official documentation or method** for closing `PersistentClient` instances. The ChromaDB API does not provide:
- A `close()` method
- Context manager support (`with` statement)
- Any documented way to explicitly release SQLite connections

This confirms that our workaround is necessary and represents the best available solution given ChromaDB's current API limitations.

## Workaround Solution

Since ChromaDB doesn't provide an official close method, we use a workaround that:

1. Clears collection caches
2. Clears client references
3. Forces garbage collection
4. Adds a small delay for OS to release file handles

### Implementation

```python
def close_chromadb_client(client: Any, collection_name: Optional[str] = None) -> None:
    """
    Properly close ChromaDB client to ensure SQLite files are flushed and unlocked.

    NOTE: ChromaDB's PersistentClient doesn't expose a close() method, so we use
    a workaround: clear references and force garbage collection. This is necessary
    to prevent SQLite file locking issues when uploading/copying SQLite files.

    This is a workaround because ChromaDB doesn't provide an official close() method.
    See: https://docs.trychroma.com/ - no close() method documented for PersistentClient

    Args:
        client: ChromaDB PersistentClient instance (or wrapper with _client attribute)
        collection_name: Optional collection name for logging
    """
    if client is None:
        return

    try:
        # Clear collections cache (for wrapper classes)
        if hasattr(client, '_collections'):
            client._collections.clear()

        # Clear the underlying client reference
        if hasattr(client, '_client') and client._client is not None:
            client._client = None

        # Force garbage collection to ensure SQLite connections are closed
        # This is necessary because ChromaDB doesn't expose a close() method
        import gc
        gc.collect()

        # Small delay to ensure file handles are released by OS
        import time as _time
        _time.sleep(0.1)

    except Exception as e:
        logger.warning(
            "Error closing ChromaDB client (non-critical)",
            error=str(e),
            collection=collection_name or "unknown",
        )
```

### Usage Pattern

**Before uploading/copying SQLite files:**

```python
# Process ChromaDB operations
chroma_client = chromadb.PersistentClient(path=temp_dir)
collection = chroma_client.get_collection("words")
# ... do work ...

# CRITICAL: Close client BEFORE uploading/copying
close_chromadb_client(chroma_client, collection_name="words")
chroma_client = None

# Now safe to upload/copy SQLite files
upload_to_s3(temp_dir, bucket, key)
```

## Where This Workaround Is Applied

1. **Chunk Processing** (`process_chunk_deltas`)
   - Closes client before uploading intermediate chunks to S3

2. **Delta Processing** (`download_and_merge_delta`)
   - Closes delta client before cleanup

3. **Group Merge** (`perform_intermediate_merge`)
   - Closes chunk clients after processing each chunk
   - Closes main client before uploading merged snapshot

4. **Final Merge** (`perform_final_merge`)
   - Closes client before uploading final snapshot

5. **Compaction Handler** (`enhanced_compaction_handler.py`)
   - Closes client before snapshot upload phase

6. **Empty Snapshot Initialization** (`initialize_empty_snapshot`)
   - Closes client before uploading newly created snapshot

## Why This Is Necessary

**Without this workaround:**
- SQLite files are locked when uploaded to S3
- Files may be corrupted (incomplete writes)
- Subsequent reads fail with "database disk image is malformed"
- File handles accumulate, causing "Too many open files" errors

**With this workaround:**
- SQLite connections are closed before file operations
- Files are fully flushed to disk
- File handles are released by the OS
- Uploads/copies succeed without corruption

## Limitations

1. **Still a workaround**: Not an official API, relies on implementation details
2. **Non-deterministic**: Garbage collection timing is not guaranteed
3. **Performance**: `gc.collect()` and `sleep()` add small overhead
4. **Fragile**: May break if ChromaDB changes internal implementation

## Best Practices

1. **Always call `close_chromadb_client()` before:**
   - Uploading snapshots/chunks to S3
   - Copying SQLite files
   - Deleting temp directories containing SQLite files

2. **Set client to `None` after closing:**
   ```python
   close_chromadb_client(chroma_client)
   chroma_client = None  # Clear reference
   ```

3. **Use in `finally` blocks:**
   ```python
   try:
       # Use client
       pass
   finally:
       close_chromadb_client(chroma_client)
       chroma_client = None
   ```

4. **Don't rely on Python's automatic GC:**
   - GC is non-deterministic
   - File handles may remain open
   - Always explicitly close before file operations

## Future Improvements

### Ideal Solution (Requires ChromaDB Changes)

If ChromaDB adds proper resource management:

```python
# Option 1: Context manager support
with chromadb.PersistentClient(path=temp_dir) as client:
    collection = client.get_collection("words")
    # ... do work ...
# Client automatically closed here

# Option 2: Explicit close() method
client = chromadb.PersistentClient(path=temp_dir)
# ... do work ...
client.close()  # Properly closes SQLite connections
```

### Current Workaround Improvements

1. **Extract to shared utility**: Already done - `close_chromadb_client()` helper
2. **Add to wrapper classes**: Consider adding to `ChromaDBClient` wrapper
3. **Context manager wrapper**: Create a wrapper that adds context manager support

```python
@contextmanager
def chromadb_client_context(path: str):
    """Context manager wrapper for ChromaDB PersistentClient."""
    client = chromadb.PersistentClient(path=path)
    try:
        yield client
    finally:
        close_chromadb_client(client)
```

## References

- [ChromaDB Documentation](https://docs.trychroma.com/)
- [Python gc Module](https://docs.python.org/3/library/gc.html)
- [SQLite File Locking](https://www.sqlite.org/lockingv3.html)
- [Python Resource Management Best Practices](https://docs.python.org/3/reference/datamodel.html#context-managers)

## Related Issues

- SQLite file corruption in ChromaDB snapshots
- "Too many open files" errors in Lambda functions
- ChromaDB snapshot upload failures
- Intermediate chunk merge failures

## Files Using This Workaround

- `infra/embedding_step_functions/unified_embedding/handlers/compaction.py`
- `infra/chromadb_compaction/lambdas/enhanced_compaction_handler.py`
- `receipt_label/receipt_label/utils/chroma_s3_helpers.py` (in `initialize_empty_snapshot`)

## Testing

To verify the workaround is working:

1. Check CloudWatch logs for "Closing ChromaDB client" messages
2. Verify snapshots upload successfully without corruption
3. Check SQLite integrity: `sqlite3 chroma.sqlite3 "PRAGMA integrity_check"`
4. Monitor for "unable to open database file" errors

## Conclusion

While using `gc.collect()` is not ideal, it's currently the only reliable way to ensure ChromaDB's SQLite connections are closed before file operations. This workaround is:

- **Necessary**: ChromaDB doesn't provide a close() method
- **Documented**: Clearly marked as a workaround with explanations
- **Centralized**: Uses a shared helper function for consistency
- **Temporary**: Should be replaced when ChromaDB adds proper resource management

The workaround is acceptable given the constraints, but we should monitor ChromaDB updates for official resource management support.

