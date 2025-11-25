# Word Ingest Step Function Migration Guide

## Overview

This document describes how each lambda in the `word-ingest-sf` step function uses the new `receipt_chroma` package. **All migrations are complete** ✅ - this document serves as a reference for the implementation and testing.

## Step Function Flow

```
ListPendingWordBatches → CheckPendingWordBatches → NormalizePendingWordBatches
→ PollWordBatches (Map) → NormalizePollWordBatchesData → SplitWordIntoChunks
→ CreateWordChunkGroups → CompactWordChunks (Map) → MarkWordBatchesComplete
```

## Lambda Handlers Status

### 1. ListPendingWordBatches (`embedding-list-pending`) ✅ MIGRATED

**Handler**: `unified_embedding/handlers/list_pending.py`

**Previous Usage**:
```python
from receipt_label.embedding.line import list_pending_line_embedding_batches
from receipt_label.embedding.word import list_pending_embedding_batches
```

**Current Implementation**:
```python
from receipt_chroma.embedding.openai import (
    list_pending_line_embedding_batches,
    list_pending_word_embedding_batches,
)
from receipt_dynamo.data.dynamo_client import DynamoClient

dynamo_client = DynamoClient(os.environ.get("DYNAMODB_TABLE_NAME"))
batches = list_pending_word_embedding_batches(dynamo_client)
```

**Changes Made**:
- ✅ Updated imports to use `receipt_chroma.embedding.openai`
- ✅ Added `DynamoClient` initialization
- ✅ Updated function calls to pass `dynamo_client` parameter
- ✅ Added support for both `line` and `word` batch types
- ✅ Added S3 manifest support for large batch lists

**Implementation**:
```python
from receipt_chroma.embedding.openai import list_pending_word_embedding_batches
from receipt_dynamo.data.dynamo_client import DynamoClient

def handle(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    batch_type = event.get("batch_type", "word")
    dynamo_client = DynamoClient()

    if batch_type == "word":
        batches = list_pending_word_embedding_batches(dynamo_client)
    else:
        batches = list_pending_line_embedding_batches(dynamo_client)
    # ... rest of handler
```

---

### 2. PollWordBatch (`embedding-word-poll`) ✅ MIGRATED

**Handler**: `unified_embedding/handlers/word_polling.py`

**Previous Usage**:
```python
from receipt_chroma.embedding.openai import (
    download_openai_batch_result,
    get_openai_batch_status,
    get_unique_receipt_and_image_ids,
    handle_batch_status,
    mark_items_for_retry,
)
from receipt_chroma.embedding.delta import save_word_embeddings_as_delta
from receipt_label.embedding.word.poll import (
    get_receipt_descriptions,
    mark_batch_complete,
)
```

**Current Implementation**:
```python
from receipt_chroma.embedding.openai import (
    download_openai_batch_result,
    get_openai_batch_status,
    get_unique_receipt_and_image_ids,
    handle_batch_status,
    mark_items_for_retry,
)
from receipt_chroma.embedding.delta import save_word_embeddings_as_delta
from receipt_label.embedding.word.poll import (
    get_receipt_descriptions,  # DynamoDB query - stays in receipt_label
    mark_batch_complete,  # DynamoDB update - stays in receipt_label
)
```

**Status**: ✅ **MIGRATED** - Uses `receipt_chroma` for all ChromaDB operations

**Note**: `get_receipt_descriptions()` and `mark_batch_complete()` remain in `receipt_label` because they are DynamoDB operations, not ChromaDB operations. This is the correct separation of concerns.

---

### 3. NormalizePollWordBatchesData (`embedding-normalize-poll-batches`) ✅ NO CHANGES

**Handler**: `unified_embedding/handlers/normalize_poll_batches_data.py`

**Status**: ✅ **NO CHANGES NEEDED**

This handler only normalizes data structures and uploads to S3. No ChromaDB or embedding logic.

---

### 4. SplitWordIntoChunks (`embedding-split-chunks`) ✅ NO CHANGES

**Handler**: `unified_embedding/handlers/split_into_chunks.py`

**Status**: ✅ **NO CHANGES NEEDED**

This handler only splits results into chunks for parallel processing. No ChromaDB or embedding logic.

---

### 5. CreateWordChunkGroups (`embedding-create-chunk-groups`) ✅ NO CHANGES

**Handler**: `unified_embedding/handlers/create_chunk_groups.py`

**Status**: ✅ **NO CHANGES NEEDED**

This handler groups chunks for compaction. No ChromaDB or embedding logic.

---

### 6. CompactWordChunks (`embedding-vector-compact`) ✅ ALREADY MIGRATED

**Handler**: `unified_embedding/handlers/compaction.py`

**Status**: ✅ **ALREADY MIGRATED**

This handler already uses `receipt_chroma` for all ChromaDB operations:
```python
from receipt_chroma import ChromaClient, LockManager
from receipt_chroma.s3 import download_snapshot_atomic, upload_snapshot_atomic
```

---

### 7. MarkWordBatchesComplete (`embedding-mark-batches-complete`) ✅ NO CHANGES

**Handler**: `unified_embedding/handlers/mark_batches_complete.py`

**Status**: ✅ **NO CHANGES NEEDED**

This handler uses `DynamoClient` directly for DynamoDB operations. `mark_batch_complete()` is a DynamoDB operation (updates batch status), not a ChromaDB operation, so it correctly remains in `receipt_label`.

---

## Submit Workflow (word-submit-sf)

The submit workflow is separate from ingest, but here's the migration status:

### SubmitWordsOpenAI (`embedding-submit-words-openai`) ✅ MIGRATED

**Handler**: `unified_embedding/handlers/submit_words_openai.py`

**Previous Usage**:
```python
from receipt_label.embedding.word import (
    add_batch_summary,
    create_batch_summary,
    deserialize_receipt_words,
    download_serialized_words,
    format_word_context_embedding,
    generate_batch_id,
    query_receipt_words,
    submit_openai_batch,
    update_word_embedding_status,
    upload_to_openai,
    write_ndjson,
)
```

**Current Implementation**:
```python
from receipt_chroma.embedding.openai import (
    add_batch_summary,
    create_batch_summary,
    submit_openai_batch,
    upload_to_openai,
)
from receipt_label.embedding.word import (
    deserialize_receipt_words,  # Entity deserialization - stays in receipt_label
    download_serialized_words,  # S3 utility - stays in receipt_label
    format_word_context_embedding,  # Business logic - stays in receipt_label
    generate_batch_id,  # Simple utility - stays in receipt_label
    query_receipt_words,  # DynamoDB query - stays in receipt_label
    update_word_embedding_status,  # DynamoDB update - stays in receipt_label
    write_ndjson,  # File I/O utility - stays in receipt_label
)
from receipt_dynamo.data.dynamo_client import DynamoClient
from openai import OpenAI

dynamo_client = DynamoClient(os.environ.get("DYNAMODB_TABLE_NAME"))
openai_client = OpenAI()

file_object = upload_to_openai(Path(input_file), openai_client)
batch = submit_openai_batch(file_object.id, openai_client)
batch_summary = create_batch_summary(
    batch_id=batch_id,
    openai_batch_id=batch.id,
    file_path=input_file,
    batch_type="WORD_EMBEDDING",
)
add_batch_summary(batch_summary, dynamo_client)
```

**Status**: ✅ **MIGRATED** - Uses `receipt_chroma` for OpenAI operations

**Functions Migrated to receipt_chroma**:
- ✅ `submit_openai_batch()` → `receipt_chroma.embedding.openai.submit_openai_batch`
- ✅ `upload_to_openai()` → `receipt_chroma.embedding.openai.upload_to_openai`
- ✅ `create_batch_summary()` → `receipt_chroma.embedding.openai.create_batch_summary`
- ✅ `add_batch_summary()` → `receipt_chroma.embedding.openai.add_batch_summary`

**Functions Remaining in receipt_label** (DynamoDB operations - correct separation):
- `deserialize_receipt_words()` - Entity deserialization
- `download_serialized_words()` - S3 utility
- `format_word_context_embedding()` - Business logic (different from formatting in receipt_chroma)
- `generate_batch_id()` - Simple utility
- `query_receipt_words()` - DynamoDB query
- `update_word_embedding_status()` - DynamoDB update
- `write_ndjson()` - File I/O utility

**Implementation**:
```python
from receipt_chroma.embedding.openai import (
    add_batch_summary,
    create_batch_summary,
    submit_openai_batch,
    upload_to_openai,
)
from receipt_label.embedding.word import (
    deserialize_receipt_words,
    download_serialized_words,
    format_word_context_embedding,
    generate_batch_id,
    query_receipt_words,
    update_word_embedding_status,
    write_ndjson,
)
from receipt_dynamo.data.dynamo_client import DynamoClient
from openai import OpenAI

def handle(event: Dict[str, Any], context: Any) -> Dict[str, Any]:
    dynamo_client = DynamoClient()
    openai_client = OpenAI()

    # ... existing code ...

    # Upload NDJSON file to OpenAI
    file_object = upload_to_openai(input_file, openai_client)

    # Submit batch to OpenAI
    batch = submit_openai_batch(file_object.id, openai_client)

    # Create batch summary
    batch_summary = create_batch_summary(
        batch_id=batch_id,
        openai_batch_id=batch.id,
        file_path=input_file,
        batch_type="WORD_EMBEDDING",  # New parameter
    )

    # Add batch summary to DynamoDB
    add_batch_summary(batch_summary, dynamo_client)

    # ... rest of handler
```

---

### FindUnembeddedWords (`embedding-find-unembedded-words`) ✅ NO CHANGES

**Handler**: `unified_embedding/handlers/find_unembedded_words.py`

**Status**: ✅ **NO CHANGES NEEDED**

These are all DynamoDB orchestration functions (queries, serialization, S3 uploads). They don't interact with ChromaDB, so they correctly remain in `receipt_label`.

---

## Summary

### ✅ Fully Migrated to receipt_chroma
1. **ListPendingWordBatches** ✅ - Uses `receipt_chroma.embedding.openai` for batch listing
2. **PollWordBatch** ✅ - Uses `receipt_chroma` for ChromaDB operations and OpenAI polling
3. **CompactWordChunks** ✅ - Uses `receipt_chroma` for ChromaDB operations
4. **SubmitWordsOpenAI** ✅ - Uses `receipt_chroma.embedding.openai` for OpenAI submission

### ✅ No Changes Needed (DynamoDB Operations)
1. **NormalizePollWordBatchesData** - Data normalization only (no ChromaDB/embedding logic)
2. **SplitWordIntoChunks** - Chunking logic only (no ChromaDB/embedding logic)
3. **CreateWordChunkGroups** - Grouping logic only (no ChromaDB/embedding logic)
4. **MarkWordBatchesComplete** - DynamoDB update (correctly remains in receipt_label)
5. **FindUnembeddedWords** - DynamoDB queries (correctly remains in receipt_label)

---

## Implementation Steps

### ✅ Step 1: Update ListPendingWordBatches Handler - COMPLETE

**File**: `unified_embedding/handlers/list_pending.py`

**Changes Made**:
- ✅ Updated imports to use `receipt_chroma.embedding.openai`
- ✅ Added `DynamoClient` initialization
- ✅ Updated function calls to pass `dynamo_client` parameter
- ✅ Added support for both `line` and `word` batch types
- ✅ Added S3 manifest support for large batch lists

**Key Changes**:
```python
from receipt_chroma.embedding.openai import (
    list_pending_line_embedding_batches,
    list_pending_word_embedding_batches,
)
from receipt_dynamo.data.dynamo_client import DynamoClient

dynamo_client = DynamoClient(os.environ.get("DYNAMODB_TABLE_NAME"))
batches = list_pending_word_embedding_batches(dynamo_client)
```

### ✅ Step 2: Update SubmitWordsOpenAI Handler - COMPLETE

**File**: `unified_embedding/handlers/submit_words_openai.py`

**Changes Made**:
- ✅ Updated imports to use `receipt_chroma.embedding.openai` for OpenAI functions
- ✅ Added `DynamoClient` and `OpenAI` client initialization
- ✅ Updated function calls to use direct clients instead of `client_manager`
- ✅ Added `batch_type="WORD_EMBEDDING"` parameter to `create_batch_summary()`

**Key Changes**:
```python
from receipt_chroma.embedding.openai import (
    add_batch_summary,
    create_batch_summary,
    submit_openai_batch,
    upload_to_openai,
)
from receipt_dynamo.data.dynamo_client import DynamoClient
from openai import OpenAI

dynamo_client = DynamoClient(os.environ.get("DYNAMODB_TABLE_NAME"))
openai_client = OpenAI()

file_object = upload_to_openai(Path(input_file), openai_client)
batch = submit_openai_batch(file_object.id, openai_client)
batch_summary = create_batch_summary(
    batch_id=batch_id,
    openai_batch_id=batch.id,
    file_path=input_file,
    batch_type="WORD_EMBEDDING",
)
add_batch_summary(batch_summary, dynamo_client)
```

### ✅ Step 3: Update SubmitOpenAI Handler (Lines) - COMPLETE

**File**: `unified_embedding/handlers/submit_openai.py`

**Changes Made**:
- ✅ Same updates as `submit_words_openai.py` but for line embeddings
- ✅ Uses `batch_type="LINE_EMBEDDING"`

### ✅ Step 4: Dockerfile - VERIFIED

**File**: `unified_embedding/Dockerfile`

**Status**: ✅ Already includes `receipt_chroma`:
```dockerfile
COPY receipt_dynamo/ /tmp/receipt_dynamo/
COPY receipt_chroma/ /tmp/receipt_chroma/

RUN pip install --no-cache-dir /tmp/receipt_dynamo && \
    pip install --no-cache-dir /tmp/receipt_chroma && \
    rm -rf /tmp/receipt_dynamo /tmp/receipt_chroma
```

### ✅ Step 5: CodeBuild Pipeline - VERIFIED

**File**: `infra/codebuild_docker_image.py`

**Status**: ✅ Already includes `receipt_chroma` in rsync patterns (updated in previous migration)

---

## Testing Checklist

### Pre-Deployment
- [x] ListPendingWordBatches handler updated to use `receipt_chroma`
- [x] SubmitWordsOpenAI handler updated to use `receipt_chroma`
- [x] SubmitOpenAI handler updated to use `receipt_chroma`
- [x] PollWordBatch handler already using `receipt_chroma` ✅
- [x] CompactWordChunks handler already using `receipt_chroma` ✅
- [x] Dockerfile updated to include `receipt_chroma`
- [x] CodeBuild pipeline includes `receipt_chroma` ✅
- [x] All imports updated
- [x] No `ClientManager` dependencies remain

### Post-Deployment (AWS Testing)
- [ ] ListPendingWordBatches returns correct batch list
- [ ] PollWordBatch successfully polls OpenAI and saves deltas
- [ ] SubmitWordsOpenAI successfully submits batches to OpenAI
- [ ] CompactWordChunks successfully merges deltas
- [ ] All handlers work with new `receipt_chroma` imports
- [ ] All DynamoDB operations still work correctly
- [ ] Step function completes successfully end-to-end

---

## Notes

- **ClientManager Removal**: All handlers now use direct `DynamoClient` and `OpenAI` instances
- **Function Signatures**: Some functions now require explicit client parameters instead of `client_manager`
- **Batch Type Parameter**: `create_batch_summary()` now requires `batch_type` parameter ("LINE_EMBEDDING" or "WORD_EMBEDDING")
- **DynamoDB Operations**: Functions that only interact with DynamoDB remain in `receipt_label` (appropriate separation of concerns)

