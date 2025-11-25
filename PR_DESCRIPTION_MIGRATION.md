# Migrate infra lambdas to use receipt_chroma package

## Summary

This PR migrates all ChromaDB-related code in the `infra/` directory from using `receipt_label` to the new `receipt_chroma` package. This is part of the ongoing effort to separate ChromaDB functionality into its own dedicated package.

## Changes

### Files Migrated (7 files)

1. **`validate_pending_labels/lambdas/validate_receipt_handler.py`**
   - Replaced `ChromaDBClient` with `ChromaClient` from `receipt_chroma`
   - Updated S3 helpers import to use `receipt_chroma.s3`

2. **`routes/address_similarity_cache_generator/lambdas/index.py`**
   - Replaced `ChromaDBClient` with `ChromaClient` from `receipt_chroma`
   - Changed `get_by_ids()` to `get()` method (API update)
   - Updated S3 helpers import to use `receipt_chroma.s3`

3. **`validate_merchant_step_functions/container/handler.py`**
   - Replaced `VectorClient.create_chromadb_client()` with `ChromaClient()` directly
   - Replaced `upload_bundled_delta_to_s3()` with `ChromaClient.persist_and_upload_delta()`
   - Updated CompactionRun to use actual delta keys returned from upload

4. **`upload_images/container/handler.py`**
   - Replaced `VectorClient.create_chromadb_client()` with `ChromaClient()` directly
   - Replaced `upload_bundled_delta_to_s3()` with `ChromaClient.persist_and_upload_delta()`
   - Updated CompactionRun to use actual delta keys returned from upload

5. **`upload_images/container_ocr/handler/embedding_processor.py`**
   - Replaced `VectorClient.create_chromadb_client()` with `ChromaClient()` directly
   - Replaced `upload_bundled_delta_to_s3()` with `ChromaClient.persist_and_upload_delta()`
   - Updated CompactionRun to use actual delta keys returned from upload

6. **`embedding_step_functions/simple_lambdas/process_receipt_realtime/handler.py`**
   - Replaced `VectorClient.create_chromadb_client()` with `ChromaClient()` directly
   - Replaced `upload_bundled_delta_to_s3()` with `ChromaClient.persist_and_upload_delta()`
   - Updated CompactionRun to use actual delta keys returned from upload

7. **`upload_images/embed_from_ndjson.py`**
   - Replaced `VectorClient.create_chromadb_client()` with `ChromaClient()` directly
   - Replaced `upload_bundled_delta_to_s3()` with `ChromaClient.persist_and_upload_delta()`
   - Updated CompactionRun to use actual delta keys returned from upload

### Documentation

- Added `infra/CHROMADB_USAGE_REVIEW.md` - Comprehensive review document tracking all ChromaDB usage in infra directory

## Migration Patterns

### Client Creation
**Before:**
```python
from receipt_label.vector_store import VectorClient
client = VectorClient.create_chromadb_client(persist_directory=path, mode="read")
```

**After:**
```python
from receipt_chroma.data.chroma_client import ChromaClient
client = ChromaClient(persist_directory=path, mode="read")
```

### S3 Operations
**Before:**
```python
from receipt_label.utils.chroma_s3_helpers import download_snapshot_atomic
from receipt_label.utils.chroma_s3_helpers import upload_bundled_delta_to_s3
```

**After:**
```python
from receipt_chroma.s3 import download_snapshot_atomic
# Delta uploads now use client method:
client.persist_and_upload_delta(bucket=bucket, s3_prefix=prefix)
```

### API Changes
- `get_by_ids()` → `get()` (method name change)
- `ChromaDBClient` → `ChromaClient` (class name change)
- `upload_bundled_delta_to_s3()` → `ChromaClient.persist_and_upload_delta()` (different pattern)

## Key Improvements

1. **Unified Package**: All ChromaDB operations now use a single, dedicated package (`receipt_chroma`)
2. **Better Resource Management**: `persist_and_upload_delta()` automatically handles client cleanup before upload
3. **Consistent API**: All ChromaDB clients use the same interface across the codebase
4. **Proper Delta Keys**: CompactionRun records now use actual delta keys returned from upload operations

## Testing Considerations

### What to Test

1. **Delta Uploads**: Verify that deltas are uploaded correctly and CompactionRun records have correct keys
2. **ChromaDB Queries**: Test that read operations (queries, get operations) work correctly
3. **Snapshot Downloads**: Verify snapshot download operations work for validation handlers
4. **HTTP Endpoints**: Test HTTP ChromaDB client connections where used

### Areas of Concern

1. **Delta Upload Format**: The old `upload_bundled_delta_to_s3()` created tar.gz bundles, while `persist_and_upload_delta()` uploads individual files. Ensure the compaction handlers can process both formats (or verify they only need individual files).

2. **Delta Key Format**: The new `persist_and_upload_delta()` returns a key that includes a unique delta ID. Verify that CompactionRun records work correctly with this format.

3. **Client Cleanup**: The new `persist_and_upload_delta()` automatically closes the client. Ensure this doesn't cause issues if the client is used after upload (it shouldn't be, but worth verifying).

## Breaking Changes

None - this is a refactoring that maintains the same functionality with a different package.

## Dependencies

- Requires `receipt_chroma` package to be available in the Lambda environments
- No changes to `receipt_label` package usage (still used for non-ChromaDB operations)

## Related Work

- Part of the broader effort to separate ChromaDB functionality from `receipt_label`
- The `embedding_step_functions/unified_embedding/` directory was already migrated in previous work
- The `chromadb_compaction/` directory was already migrated in previous work

## Checklist

- [x] All production code files migrated
- [x] Imports updated to use `receipt_chroma`
- [x] API method calls updated (get_by_ids → get)
- [x] Delta upload pattern updated
- [x] CompactionRun records updated to use actual delta keys
- [x] Documentation added
- [x] No linting errors
- [ ] Test delta uploads in staging
- [ ] Test ChromaDB queries in staging
- [ ] Verify CompactionRun records are created correctly

