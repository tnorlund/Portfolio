# ChromaDB Usage Review in infra/ Directory

This document reviews all ChromaDB-related code in the `infra/` directory to confirm usage of `receipt_chroma` vs `receipt_label`.

## Summary

**Status**: ⚠️ **NOT ALL MIGRATED** - Several files still use `receipt_label` for ChromaDB operations.

## Files Using `receipt_chroma` ✅

These files correctly use the `receipt_chroma` package:

### Core ChromaDB Operations
1. **`embedding_step_functions/unified_embedding/handlers/compaction.py`**
   - Uses: `receipt_chroma.data.chroma_client.ChromaClient`
   - Uses: `receipt_chroma.s3.*` (download_snapshot_atomic, upload_snapshot_atomic, etc.)
   - Uses: `receipt_chroma.LockManager`
   - ✅ **CORRECT**

2. **`chromadb_compaction/lambdas/enhanced_compaction_handler.py`**
   - Uses: `receipt_chroma.ChromaClient`
   - Uses: `receipt_chroma.LockManager`
   - Uses: `receipt_chroma.s3.*`
   - ✅ **CORRECT**

3. **`chromadb_compaction/lambdas/compaction/metadata_handler.py`**
   - Uses: `receipt_chroma.ChromaClient`
   - Uses: `receipt_chroma.s3.*`
   - ✅ **CORRECT**

4. **`chromadb_compaction/lambdas/compaction/label_handler.py`**
   - Uses: `receipt_chroma.ChromaClient`
   - Uses: `receipt_chroma.s3.*`
   - ✅ **CORRECT**

5. **`chromadb_compaction/lambdas/compaction/compaction_run.py`**
   - Uses: `receipt_chroma.ChromaClient`
   - Uses: `receipt_chroma.s3.*`
   - ✅ **CORRECT**

### Embedding Operations
6. **`embedding_step_functions/unified_embedding/handlers/word_polling.py`**
   - Uses: `receipt_chroma.embedding.delta.save_word_embeddings_as_delta`
   - Uses: `receipt_chroma.embedding.openai.*`
   - ✅ **CORRECT**

7. **`embedding_step_functions/unified_embedding/handlers/line_polling.py`**
   - Uses: `receipt_chroma.embedding.delta.save_line_embeddings_as_delta`
   - Uses: `receipt_chroma.embedding.openai.*`
   - ✅ **CORRECT**

8. **`embedding_step_functions/unified_embedding/handlers/submit_words_openai.py`**
   - Uses: `receipt_chroma.embedding.openai.*`
   - ✅ **CORRECT**

9. **`embedding_step_functions/unified_embedding/handlers/submit_openai.py`**
   - Uses: `receipt_chroma.embedding.openai.*`
   - ✅ **CORRECT**

## Files Still Using `receipt_label` for ChromaDB ❌

These files need to be migrated to use `receipt_chroma`:

### Critical - Direct ChromaDB Client Usage
1. **`validate_pending_labels/lambdas/validate_receipt_handler.py`**
   - ❌ Uses: `receipt_label.utils.chroma_s3_helpers.download_snapshot_atomic`
   - ❌ Uses: `receipt_label.vector_store.client.chromadb_client.ChromaDBClient`
   - **Action**: Migrate to `receipt_chroma.s3.download_snapshot_atomic` and `receipt_chroma.data.chroma_client.ChromaClient`

2. **`routes/address_similarity_cache_generator/lambdas/index.py`**
   - ❌ Uses: `receipt_label.utils.chroma_s3_helpers.download_snapshot_atomic`
   - ❌ Uses: `receipt_label.vector_store.client.chromadb_client.ChromaDBClient`
   - **Action**: Migrate to `receipt_chroma.s3.download_snapshot_atomic` and `receipt_chroma.data.chroma_client.ChromaClient`

### Critical - VectorClient and S3 Helpers
3. **`validate_merchant_step_functions/container/handler.py`**
   - ❌ Uses: `receipt_label.vector_store.VectorClient`
   - ❌ Uses: `receipt_label.utils.chroma_s3_helpers.upload_bundled_delta_to_s3`
   - **Action**: Migrate to `receipt_chroma.data.chroma_client.ChromaClient` and `receipt_chroma.s3.*`

4. **`upload_images/container/handler.py`**
   - ❌ Uses: `receipt_label.vector_store.VectorClient`
   - ❌ Uses: `receipt_label.utils.chroma_s3_helpers.upload_bundled_delta_to_s3`
   - **Action**: Migrate to `receipt_chroma.data.chroma_client.ChromaClient` and `receipt_chroma.s3.*`

5. **`upload_images/container_ocr/handler/embedding_processor.py`**
   - ❌ Uses: `receipt_label.vector_store.VectorClient`
   - ❌ Uses: `receipt_label.utils.chroma_s3_helpers.upload_bundled_delta_to_s3`
   - **Action**: Migrate to `receipt_chroma.data.chroma_client.ChromaClient` and `receipt_chroma.s3.*`

6. **`embedding_step_functions/simple_lambdas/process_receipt_realtime/handler.py`**
   - ❌ Uses: `receipt_label.vector_store.VectorClient`
   - ❌ Uses: `receipt_label.utils.chroma_s3_helpers.upload_bundled_delta_to_s3`
   - **Action**: Migrate to `receipt_chroma.data.chroma_client.ChromaClient` and `receipt_chroma.s3.*`

7. **`upload_images/embed_from_ndjson.py`**
   - ❌ Uses: `receipt_label.vector_store.VectorClient`
   - ❌ Uses: `receipt_label.utils.chroma_s3_helpers.upload_bundled_delta_to_s3`
   - **Action**: Migrate to `receipt_chroma.data.chroma_client.ChromaClient` and `receipt_chroma.s3.*`

## Test Files and Documentation

These files reference `receipt_label` but are tests/documentation (lower priority):

1. **`embedding_step_functions/unified_embedding/handlers/tests/test_close_chromadb_client.py`**
   - Mocks `receipt_label.utils.chroma_s3_helpers` (test file)
   - **Action**: Update mocks to use `receipt_chroma` if tests are still relevant

2. **`embedding_step_functions/unified_embedding/handlers/tests/standalone_test_close_client.py`**
   - Mocks `receipt_label.utils.chroma_s3_helpers` (test file)
   - **Action**: Update mocks to use `receipt_chroma` if tests are still relevant

3. **`chromadb_compaction/tests/fixtures/aws_services.py`**
   - Mocks `receipt_label.utils.chroma_s3_helpers` (test file)
   - **Action**: Update mocks to use `receipt_chroma` if tests are still relevant

4. **`chromadb_compaction/tests/README_NEW.md`**
   - Documentation referencing `receipt_label.utils.chroma_s3_helpers`
   - **Action**: Update documentation

5. **`chromadb_compaction/tests/test_enhanced_compaction_handler_integration.py`**
   - Mocks `receipt_label.utils.chroma_client.ChromaDBClient` (test file)
   - **Action**: Update mocks to use `receipt_chroma`

6. **`chromadb_compaction/lambdas/enhanced_compaction_handler.py.backup`**
   - Backup file (can be deleted)
   - **Action**: Delete if no longer needed

7. **`VALIDATION_PIPELINE_CHROMADB_MIGRATION.md`**
   - Documentation file
   - **Action**: Update documentation

## Migration Checklist

### High Priority (Production Code)
- [ ] `validate_pending_labels/lambdas/validate_receipt_handler.py`
- [ ] `routes/address_similarity_cache_generator/lambdas/index.py`
- [ ] `validate_merchant_step_functions/container/handler.py`
- [ ] `upload_images/container/handler.py`
- [ ] `upload_images/container_ocr/handler/embedding_processor.py`
- [ ] `embedding_step_functions/simple_lambdas/process_receipt_realtime/handler.py`
- [ ] `upload_images/embed_from_ndjson.py`

### Lower Priority (Tests/Documentation)
- [ ] Update test mocks to use `receipt_chroma`
- [ ] Update documentation files
- [ ] Delete backup files if no longer needed

## Key Migration Patterns

### Pattern 1: ChromaDB Client
**Old:**
```python
from receipt_label.vector_store.client.chromadb_client import ChromaDBClient
client = ChromaDBClient(persist_directory=path, mode="read")
```

**New:**
```python
from receipt_chroma.data.chroma_client import ChromaClient
client = ChromaClient(persist_directory=path, mode="read")
```

### Pattern 2: S3 Helpers
**Old:**
```python
from receipt_label.utils.chroma_s3_helpers import download_snapshot_atomic, upload_snapshot_atomic
```

**New:**
```python
from receipt_chroma.s3 import download_snapshot_atomic, upload_snapshot_atomic
```

### Pattern 3: VectorClient
**Old:**
```python
from receipt_label.vector_store import VectorClient
client = VectorClient.create_chromadb_client(...)
```

**New:**
```python
from receipt_chroma.data.chroma_client import ChromaClient
client = ChromaClient(...)
```

### Pattern 4: Delta Upload
**Old:**
```python
from receipt_label.utils.chroma_s3_helpers import upload_bundled_delta_to_s3
upload_bundled_delta_to_s3(local_delta_dir, bucket, delta_prefix)
```

**New:**
```python
# receipt_chroma uses a different pattern - deltas are uploaded via ChromaClient
from receipt_chroma.data.chroma_client import ChromaClient
# Create client in delta mode, upsert data, then:
chroma_client.persist_and_upload_delta(bucket=bucket, s3_prefix=delta_prefix)
# OR use the embedding delta functions:
from receipt_chroma.embedding.delta import save_word_embeddings_as_delta
# or save_line_embeddings_as_delta
```

**Note**: `upload_bundled_delta_to_s3` doesn't exist in `receipt_chroma`. The pattern is different - you create a ChromaClient in delta mode, upsert your data, and then call `persist_and_upload_delta()` on the client. For embedding deltas, use `save_word_embeddings_as_delta` or `save_line_embeddings_as_delta`.

## Notes

- The `embedding_step_functions/` directory appears to be fully migrated ✅
- The `chromadb_compaction/` directory appears to be fully migrated ✅
- Several upload/validation handlers still need migration
- Some files use `receipt_label` for non-ChromaDB purposes (e.g., `receipt_label.langchain.*`, `receipt_label.embedding.*`) - these are fine and not part of this migration

