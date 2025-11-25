# Migration Guide: Moving to receipt_chroma

This guide helps you migrate from existing ChromaDB usage in `receipt_label` and `infra/` to the new `receipt_chroma` package.

## Why Migrate?

1. **Proper Resource Management**: The new package implements proper `close()` method and context manager support, addressing [GitHub issue #5868](https://github.com/chroma-core/chroma/issues/5868)
2. **Consolidated Accessors**: All ChromaDB operations in one clean package
3. **Better Testing**: Dedicated package allows for comprehensive test coverage
4. **Cleaner Code**: Removes scattered ChromaDB client implementations

## Migration Patterns

### Pattern 1: Basic Client Usage

**Before** (`receipt_label/receipt_label/utils/chroma_client.py`):
```python
from receipt_label.utils.chroma_client import ChromaDBClient

client = ChromaDBClient(persist_directory="/path/to/db", mode="read")
collection = client.get_collection("my_collection")
results = client.query_collection("my_collection", query_texts=["query"])
# No explicit close - relies on GC
```

**After** (`receipt_chroma`):
```python
from receipt_chroma import ChromaClient

with ChromaClient(persist_directory="/path/to/db", mode="read") as client:
    results = client.query(
        collection_name="my_collection",
        query_texts=["query"]
    )
    # Automatic cleanup
```

### Pattern 2: Lambda Functions with S3 Upload

**Before** (`infra/embedding_step_functions/unified_embedding/handlers/compaction.py`):
```python
from receipt_label.vector_store.client.chromadb_client import ChromaDBClient

chroma_client = ChromaDBClient(persist_directory=temp_dir, mode="delta")
# ... operations ...
chroma_client._close_client_for_upload()  # Fragile workaround
# Upload to S3
```

**After** (`receipt_chroma`):
```python
from receipt_chroma import ChromaClient

chroma_client = ChromaClient(persist_directory=temp_dir, mode="delta")
try:
    # ... operations ...
    chroma_client.upsert(...)
finally:
    # Proper close() method
    chroma_client.close()
    # Now safe to upload to S3
```

### Pattern 3: Direct PersistentClient Usage

**Before** (`infra/merchant_validation_container/handler/handler.py`):
```python
from chromadb import PersistentClient
from chromadb.config import Settings

client = PersistentClient(
    path=chroma_root,
    settings=Settings(anonymized_telemetry=False, allow_reset=False)
)
collection = client.get_collection("lines")
# No close() method available
```

**After** (`receipt_chroma`):
```python
from receipt_chroma import ChromaClient

with ChromaClient(persist_directory=chroma_root, mode="read") as client:
    collection = client.get_collection("lines")
    # Automatic cleanup
```

### Pattern 4: VectorClient Factory Pattern

**Before** (`receipt_label/receipt_label/vector_store/client/factory.py`):
```python
from receipt_label.vector_store.client.factory import VectorClient

chroma_client = VectorClient.create_chromadb_client(
    persist_directory=path,
    mode="read"
)
```

**After** (`receipt_chroma`):
```python
from receipt_chroma import ChromaClient

chroma_client = ChromaClient(
    persist_directory=path,
    mode="read"
)
```

## API Changes

### Method Name Changes

| Old (receipt_label) | New (receipt_chroma) |
|---------------------|----------------------|
| `query_collection()` | `query()` |
| `upsert_vectors()` | `upsert()` |
| `get_by_ids()` | `get()` |
| `_close_client_for_upload()` | `close()` |

### New Features

1. **Context Manager Support**:
   ```python
   with ChromaClient(...) as client:
       # Automatic cleanup
   ```

2. **Explicit close() Method**:
   ```python
   client = ChromaClient(...)
   try:
       # ... operations ...
   finally:
       client.close()  # Proper resource cleanup
   ```

3. **Collection Context Manager**:
   ```python
   with client.collection("my_collection") as coll:
       # Work with collection
   ```

## Step-by-Step Migration

### Step 1: Install receipt_chroma

```bash
pip install receipt-chroma[chromadb]
```

### Step 2: Update Imports

Replace:
```python
from receipt_label.utils.chroma_client import ChromaDBClient
# or
from receipt_label.vector_store.client.chromadb_client import ChromaDBClient
```

With:
```python
from receipt_chroma import ChromaClient
```

### Step 3: Update Client Creation

Replace:
```python
client = ChromaDBClient(persist_directory=path, mode="read")
```

With:
```python
client = ChromaClient(persist_directory=path, mode="read")
```

### Step 4: Add Resource Management

**Option A: Use Context Manager (Recommended)**
```python
with ChromaClient(...) as client:
    # ... operations ...
```

**Option B: Explicit close()**
```python
client = ChromaClient(...)
try:
    # ... operations ...
finally:
    client.close()
```

### Step 5: Update Method Calls

- `query_collection()` → `query()`
- `upsert_vectors()` → `upsert()`
- `get_by_ids()` → `get()`

### Step 6: Remove Workarounds

Remove calls to:
- `_close_client_for_upload()`
- `close_chromadb_client()` (standalone function)
- Manual GC calls

Replace with:
- `client.close()`

## Common Issues and Solutions

### Issue: "Cannot use closed ChromaClient"

**Cause**: Trying to use a client after calling `close()`

**Solution**: Create a new client instance or use a context manager:
```python
# Wrong
client.close()
client.query(...)  # Error!

# Right
with ChromaClient(...) as client:
    client.query(...)
```

### Issue: File locking when uploading to S3

**Cause**: Not closing the client before file operations

**Solution**: Always call `close()` before uploading:
```python
client = ChromaClient(persist_directory=temp_dir, mode="delta")
try:
    # ... operations ...
finally:
    client.close()  # CRITICAL: Close before upload
    # Now safe to upload files
```

### Issue: Import errors

**Cause**: ChromaDB not installed

**Solution**: Install with optional dependencies:
```bash
pip install receipt-chroma[chromadb]
```

## Testing Your Migration

1. **Run existing tests** to ensure functionality is preserved
2. **Test file operations** (S3 uploads, copies) to verify locks are released
3. **Test context manager** usage in new code
4. **Monitor for resource leaks** in long-running processes

## Rollback Plan

If you need to rollback:

1. Keep old imports working temporarily:
   ```python
   # Temporary compatibility layer
   from receipt_label.utils.chroma_client import ChromaDBClient as OldChromaDBClient
   ```

2. Gradually migrate one module at a time
3. Test thoroughly before removing old code

## Questions?

- See [README.md](README.md) for usage examples
- Check [GitHub issue #5868](https://github.com/chroma-core/chroma/issues/5868) for context on the close() method issue
- Review tests in `tests/test_chroma_client.py` for examples

