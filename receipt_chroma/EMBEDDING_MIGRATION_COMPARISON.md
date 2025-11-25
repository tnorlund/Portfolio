# Embedding Functionality Comparison: receipt_chroma vs receipt_label

## Overview

This document compares the embedding business logic between `receipt_chroma` (new) and `receipt_label` (legacy) to identify what's been migrated and what remains.

## ✅ Fully Migrated to receipt_chroma

### 1. Delta Creation (receipt_chroma/embedding/delta/)
**Status**: ✅ **COMPLETE**

| Function | receipt_label Location | receipt_chroma Location | Status |
|----------|----------------------|------------------------|--------|
| `produce_embedding_delta()` | `utils/chroma_s3_helpers.py` | `embedding/delta/producer.py` | ✅ Migrated |
| `save_line_embeddings_as_delta()` | `line/poll.py` | `embedding/delta/line_delta.py` | ✅ Migrated |
| `save_word_embeddings_as_delta()` | `word/poll.py` | `embedding/delta/word_delta.py` | ✅ Migrated |

**Key Improvements**:
- Removed ClientManager dependency
- Uses consolidated metadata creation
- Better separation of concerns

### 2. Metadata Creation (receipt_chroma/embedding/metadata/)
**Status**: ✅ **COMPLETE**

| Function | receipt_label Location | receipt_chroma Location | Status |
|----------|----------------------|------------------------|--------|
| Line metadata creation | Inline in `line/poll.py` | `embedding/metadata/line_metadata.py` | ✅ Migrated |
| Word metadata creation | Inline in `word/poll.py` | `embedding/metadata/word_metadata.py` | ✅ Migrated |
| Anchor enrichment | Inline in poll modules | Consolidated in metadata modules | ✅ Migrated |
| Label enrichment | Inline in poll modules | Consolidated in metadata modules | ✅ Migrated |

**Key Improvements**:
- Reusable metadata builders
- Consistent enrichment patterns
- Better testability

### 3. Formatting Utilities (receipt_chroma/embedding/formatting/)
**Status**: ✅ **COMPLETE**

| Function | receipt_label Location | receipt_chroma Location | Status |
|----------|----------------------|------------------------|--------|
| `format_line_context_embedding_input()` | `line/realtime.py` | `embedding/formatting/line_format.py` | ✅ Migrated |
| `format_word_context_embedding_input()` | `word/submit.py` | `embedding/formatting/word_format.py` | ✅ Migrated |
| `parse_prev_next_from_formatted()` | `line/poll.py` | `embedding/formatting/line_format.py` | ✅ Migrated |
| `parse_left_right_from_formatted()` | `word/poll.py` | `embedding/formatting/word_format.py` | ✅ Migrated |

**Key Improvements**:
- Centralized formatting logic
- Consistent API across line/word
- Better reusability

### 4. OpenAI Orchestration (receipt_chroma/embedding/openai/)
**Status**: ✅ **COMPLETE**

| Function | receipt_label Location | receipt_chroma Location | Status |
|----------|----------------------|------------------------|--------|
| `handle_batch_status()` | `common/batch_status_handler.py` | `embedding/openai/batch_status.py` | ✅ Migrated |
| `process_error_file()` | `common/batch_status_handler.py` | `embedding/openai/batch_status.py` | ✅ Migrated |
| `process_partial_results()` | `common/batch_status_handler.py` | `embedding/openai/batch_status.py` | ✅ Migrated |
| `mark_items_for_retry()` | `common/batch_status_handler.py` | `embedding/openai/batch_status.py` | ✅ Migrated |
| `get_openai_batch_status()` | `line/poll.py`, `word/poll.py` | `embedding/openai/poll.py` | ✅ Migrated |
| `download_openai_batch_result()` | `line/poll.py`, `word/poll.py` | `embedding/openai/poll.py` | ✅ Migrated |
| `list_pending_*_batches()` | `line/poll.py`, `word/poll.py` | `embedding/openai/poll.py` | ✅ Migrated |
| `upload_to_openai()` | `line/submit.py`, `word/submit.py` | `embedding/openai/submit.py` | ✅ Migrated |
| `submit_openai_batch()` | `line/submit.py`, `word/submit.py` | `embedding/openai/submit.py` | ✅ Migrated |
| `create_batch_summary()` | `line/submit.py`, `word/submit.py` | `embedding/openai/submit.py` | ✅ Migrated |
| `add_batch_summary()` | `line/submit.py`, `word/submit.py` | `embedding/openai/submit.py` | ✅ Migrated |
| `get_unique_receipt_and_image_ids()` | `line/poll.py`, `word/poll.py` | `embedding/openai/helpers.py` | ✅ Migrated |

**Key Improvements**:
- Removed ClientManager (uses direct `DynamoClient` and `OpenAI`)
- Unified poll functions (no duplication)
- Unified submit functions (no duplication)
- Better error handling

### 5. Normalization Utilities (receipt_chroma/embedding/utils/)
**Status**: ✅ **COMPLETE**

| Function | receipt_label Location | receipt_chroma Location | Status |
|----------|----------------------|------------------------|--------|
| `normalize_phone()` | `merchant_validation/normalize.py` | `embedding/utils/normalize.py` | ✅ Migrated |
| `normalize_address()` | `merchant_validation/normalize.py` | `embedding/utils/normalize.py` | ✅ Migrated |
| `normalize_url()` | `merchant_validation/normalize.py` | `embedding/utils/normalize.py` | ✅ Migrated |
| `build_full_address_from_words()` | `merchant_validation/normalize.py` | `embedding/utils/normalize.py` | ✅ Migrated |
| `build_full_address_from_lines()` | `merchant_validation/normalize.py` | `embedding/utils/normalize.py` | ✅ Migrated |

## ⚠️ Still in receipt_label (Not ChromaDB-Related)

### 1. Submit Pipeline Functions (receipt_label/embedding/*/submit.py)
**Status**: ⚠️ **REMAINS IN receipt_label** (DynamoDB orchestration, not ChromaDB)

These functions handle the **submission** pipeline (preparing data for OpenAI):

| Function | Purpose | Should Migrate? |
|----------|---------|----------------|
| `list_receipt_lines_with_no_embeddings()` | Query DynamoDB for lines needing embeddings | ❌ No - DynamoDB query |
| `list_receipt_words_with_no_embeddings()` | Query DynamoDB for words needing embeddings | ❌ No - DynamoDB query |
| `chunk_into_line_embedding_batches()` | Batch lines for OpenAI submission | ❌ No - Business logic |
| `chunk_into_embedding_batches()` | Batch words for OpenAI submission | ❌ No - Business logic |
| `serialize_receipt_lines()` | Convert ReceiptLine entities to NDJSON | ❌ No - Entity serialization |
| `serialize_receipt_words()` | Convert ReceiptWord entities to NDJSON | ❌ No - Entity serialization |
| `deserialize_receipt_lines()` | Convert NDJSON back to ReceiptLine entities | ❌ No - Entity deserialization |
| `deserialize_receipt_words()` | Convert NDJSON back to ReceiptWord entities | ❌ No - Entity deserialization |
| `format_line_context_embedding()` | Format line text for OpenAI (different from formatting in receipt_chroma) | ⚠️ **MAYBE** - Could consolidate |
| `format_word_context_embedding()` | Format word text for OpenAI (different from formatting in receipt_chroma) | ⚠️ **MAYBE** - Could consolidate |
| `write_ndjson()` | Write NDJSON file for OpenAI batch | ❌ No - File I/O utility |
| `upload_serialized_lines()` | Upload serialized lines to S3 | ❌ No - S3 utility |
| `upload_serialized_words()` | Upload serialized words to S3 | ❌ No - S3 utility |
| `download_serialized_lines()` | Download serialized lines from S3 | ❌ No - S3 utility |
| `download_serialized_words()` | Download serialized words from S3 | ❌ No - S3 utility |
| `query_receipt_lines()` | Query DynamoDB for lines | ❌ No - DynamoDB query |
| `query_receipt_words()` | Query DynamoDB for words | ❌ No - DynamoDB query |
| `update_line_embedding_status()` | Update DynamoDB embedding status | ❌ No - DynamoDB update |
| `update_word_embedding_status()` | Update DynamoDB embedding status | ❌ No - DynamoDB update |
| `generate_batch_id()` | Generate UUID for batch | ❌ No - Simple utility |

**Rationale**: These are **DynamoDB orchestration** functions, not ChromaDB operations. They belong in `receipt_label` or could be moved to `receipt_dynamo` if we want to centralize DynamoDB operations.

### 2. Poll Pipeline Functions (receipt_label/embedding/*/poll.py)
**Status**: ⚠️ **PARTIALLY MIGRATED** (Some functions remain)

| Function | Purpose | Status |
|----------|---------|--------|
| `get_receipt_descriptions()` | Fetch receipt details from DynamoDB | ⚠️ **REMAINS** - DynamoDB query |
| `write_line_embedding_results_to_dynamo()` | Write EmbeddingBatchResult to DynamoDB | ⚠️ **REMAINS** - DynamoDB write |
| `write_embedding_results_to_dynamo()` | Write EmbeddingBatchResult to DynamoDB | ⚠️ **REMAINS** - DynamoDB write |
| `mark_batch_complete()` | Update batch status in DynamoDB | ⚠️ **REMAINS** - DynamoDB update |
| `update_line_embedding_status_to_success()` | Update line embedding status | ⚠️ **REMAINS** - DynamoDB update |

**Rationale**: These are **DynamoDB operations** for tracking embedding status. They don't interact with ChromaDB.

### 3. Realtime Embedding (receipt_label/embedding/*/realtime.py)
**Status**: ⚠️ **REMAINS IN receipt_label** (Not batch-based)

| Function | Purpose | Should Migrate? |
|----------|---------|----------------|
| `embed_lines_realtime()` | Generate embeddings for lines using OpenAI API directly | ⚠️ **MAYBE** - Uses ChromaDB |
| `embed_words_realtime()` | Generate embeddings for words using OpenAI API directly | ⚠️ **MAYBE** - Uses ChromaDB |
| `embed_receipt_lines_realtime()` | Embed all lines from a receipt | ⚠️ **MAYBE** - Uses ChromaDB |
| `embed_receipt_words_realtime()` | Embed all words from a receipt | ⚠️ **MAYBE** - Uses ChromaDB |

**Rationale**: These functions **do use ChromaDB** (via `ChromaClient`), but they're for **realtime** (non-batch) embeddings. They could be migrated to `receipt_chroma` if we want to centralize all ChromaDB operations.

**Note**: These functions use `_create_line_metadata()` and `_create_word_metadata()` which we've now consolidated in `receipt_chroma/embedding/metadata/`. They should be updated to use the new metadata functions.

## 📊 Summary

### ✅ Fully Migrated (ChromaDB Core Operations)
- **Delta creation**: 100% migrated
- **Metadata creation**: 100% migrated
- **Formatting utilities**: 100% migrated
- **OpenAI orchestration**: 100% migrated (batch status, poll, submit)
- **Normalization utilities**: 100% migrated

### ⚠️ Remains in receipt_label (DynamoDB Orchestration)
- **Submit pipeline**: DynamoDB queries, entity serialization, S3 uploads
- **Poll pipeline**: DynamoDB writes for tracking, status updates
- **Realtime embedding**: Direct OpenAI API calls (could migrate if desired)

### 🎯 Key Differences

1. **receipt_chroma** focuses on:
   - ChromaDB operations (deltas, snapshots)
   - OpenAI batch API orchestration
   - Metadata and formatting for embeddings
   - Normalization utilities

2. **receipt_label** still handles:
   - DynamoDB queries for finding items to embed
   - Entity serialization/deserialization
   - DynamoDB status tracking
   - S3 file management for batch submission
   - Realtime embedding workflows

### 🔄 Migration Path Forward

**Option 1: Keep Current Split** (Recommended)
- `receipt_chroma`: ChromaDB + OpenAI batch orchestration
- `receipt_label`: DynamoDB orchestration + business logic
- **Pros**: Clear separation of concerns
- **Cons**: Some duplication (formatting functions exist in both)

**Option 2: Migrate Realtime Functions**
- Move `realtime.py` functions to `receipt_chroma/embedding/realtime/`
- Update to use new metadata creation functions
- **Pros**: All ChromaDB operations in one place
- **Cons**: Realtime functions are less commonly used

**Option 3: Full Consolidation**
- Move all embedding-related functions to `receipt_chroma`
- Keep only label validation in `receipt_label`
- **Pros**: Single source of truth for embeddings
- **Cons**: Blurs boundaries between ChromaDB and DynamoDB operations

## 📝 Recommendations

1. **Update realtime functions** to use `receipt_chroma/embedding/metadata/` functions
2. **Consider migrating realtime functions** if they're actively used
3. **Keep DynamoDB orchestration** in `receipt_label` (it's not ChromaDB-specific)
4. **Document the split** clearly so developers know where to find functions

