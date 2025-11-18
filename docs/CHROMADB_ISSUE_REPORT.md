# ChromaDB Issue Report

## What happened?

When using ChromaDB's `PersistentClient` in production (AWS Lambda), we encounter SQLite file locking issues that cause database corruption and "unable to open database file" errors. This occurs when:

1. ChromaDB clients are used to create/modify SQLite databases
2. Files need to be uploaded to S3 or copied while SQLite connections are still open
3. Another process tries to access the same SQLite files

**Symptoms:**
- `error returned from database: (code: 14) unable to open database file`
- `database disk image is malformed` errors
- `Too many open files` errors
- Corrupted ChromaDB snapshots in S3
- SQLite integrity check failures (`PRAGMA integrity_check`)

**Minimal reproducible example (Lambda environment):**

```python
import os
import tempfile
import shutil
import boto3
import chromadb
from pathlib import Path

def lambda_handler(event, context):
    """AWS Lambda handler that creates ChromaDB delta and uploads to S3."""
    temp_dir = tempfile.mkdtemp()
    bucket = "my-chromadb-bucket"

    try:
        # Create ChromaDB client and collection
        chroma_client = chromadb.PersistentClient(path=temp_dir)
        collection = chroma_client.get_or_create_collection("words")

        # Add some embeddings
        collection.upsert(
            ids=["word_1", "word_2"],
            embeddings=[[0.1, 0.2, 0.3], [0.4, 0.5, 0.6]],
            documents=["hello", "world"],
            metadatas=[{"pos": 1}, {"pos": 2}]
        )

        # PROBLEM: ChromaDB client is still open, SQLite files are locked
        # No close() method available: chroma_client.close()  # ❌ AttributeError

        # Try to upload SQLite files to S3
        s3_client = boto3.client("s3")
        persist_path = Path(temp_dir)

        for file_path in persist_path.rglob("*"):
            if file_path.is_file():
                s3_key = f"snapshots/{file_path.name}"
                # ❌ This fails or uploads corrupted files because SQLite is locked
                s3_client.upload_file(str(file_path), bucket, s3_key)
                # Error: "unable to open database file" or corrupted database

        # Even setting client to None doesn't help immediately
        chroma_client = None  # SQLite connections still open!

        # Files may still be locked here, causing corruption when uploaded

    finally:
        # Cleanup
        shutil.rmtree(temp_dir, ignore_errors=True)
```

**Actual error observed in production:**
```
error returned from database: (code: 14) unable to open database file
database disk image is malformed
```

**When downloading and verifying the uploaded snapshot:**
```python
# Later, when downloading from S3 and trying to use:
client = chromadb.PersistentClient(path=downloaded_dir)
collection = client.get_collection("words")
# ❌ Fails: database disk image is malformed
```

## What did you expect to happen?

I expected ChromaDB's `PersistentClient` to provide a way to explicitly close SQLite connections before performing file operations. Specifically:

1. **Option 1: Context manager support** (preferred)
   ```python
   with chromadb.PersistentClient(path=temp_dir) as client:
       collection = client.get_collection("words")
       # ... do work ...
   # Client automatically closed here, SQLite files unlocked
   ```

2. **Option 2: Explicit `close()` method**
   ```python
   client = chromadb.PersistentClient(path=temp_dir)
   # ... do work ...
   client.close()  # Properly closes SQLite connections
   # Now safe to upload/copy SQLite files
   ```

This would allow us to:
- Safely upload ChromaDB snapshots to S3 without corruption
- Copy SQLite files while ensuring they're fully flushed
- Properly manage resources in long-running processes
- Avoid relying on Python's non-deterministic garbage collection

## Current Workaround

Since ChromaDB doesn't provide an official close method, we're using a fragile workaround:

```python
def close_chromadb_client(client):
    """Workaround: ChromaDB doesn't expose close() method."""
    if hasattr(client, '_collections'):
        client._collections.clear()
    if hasattr(client, '_client') and client._client is not None:
        client._client = None
    import gc
    gc.collect()  # Force GC to close SQLite connections
    import time
    time.sleep(0.1)  # Wait for OS to release file handles
```

This workaround:
- ❌ Relies on implementation details (`_client`, `_collections`)
- ❌ Is non-deterministic (GC timing)
- ❌ May break if ChromaDB changes internal implementation
- ❌ Adds unnecessary overhead

## Versions

- **ChromaDB**: >=1.3.3 (tested with latest stable)
- **Python**: 3.14.0
- **OS**: macOS 25.1.0 (darwin), also occurs on Linux (AWS Lambda)
- **SQLite**: Version bundled with ChromaDB

## Additional Context

**Why this matters:**
- In production, we need to upload ChromaDB snapshots to S3 for backup and distribution
- SQLite files cannot be safely copied/uploaded while connections are open
- Python's garbage collector doesn't run immediately when references are cleared
- File handles may remain open even after setting `client = None`

**Impact:**
- Database corruption requiring manual recovery
- Production failures in Lambda functions
- "Too many open files" errors in long-running processes
- Need for complex workarounds that are fragile and non-deterministic

**Related:**
- SQLite file locking documentation: https://www.sqlite.org/lockingv3.html
- Python context manager protocol: https://docs.python.org/3/reference/datamodel.html#context-managers

## Proposed Solution

Add resource management to `PersistentClient`:

1. **Add `close()` method** that:
   - Closes all SQLite connections
   - Flushes pending writes
   - Releases file handles
   - Can be called multiple times safely (idempotent)

2. **Add context manager support** (`__enter__`/`__exit__`):
   - Automatically closes client when exiting context
   - Handles exceptions properly
   - Follows Python resource management best practices

3. **Document proper usage** in ChromaDB docs:
   - When to close clients
   - Best practices for file operations
   - Context manager examples

This would align ChromaDB with standard Python resource management patterns and eliminate the need for workarounds.

