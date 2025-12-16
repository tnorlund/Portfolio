# Embedding Orchestration Migration: Moving to receipt_chroma

**Status**: Planning
**Created**: December 16 2025
**Goal**: Move high-level embedding orchestration from `receipt_agent` to `receipt_chroma` while maintaining backward compatibility

## Current Architecture

### What's in `receipt_chroma` (Building Blocks)

`receipt_chroma` provides **pure ChromaDB operations** with no DynamoDB dependencies:

1. **ChromaDB Client Operations:**
   - `ChromaClient` - Create local ChromaDB deltas
   - `process_collection_updates()` - Merge deltas into snapshots (compaction)

2. **Delta Creation/Upload:**
   - `produce_embedding_delta()` - Create and upload delta to S3
   - `save_line_embeddings_as_delta()` - Save line embeddings as delta
   - `save_word_embeddings_as_delta()` - Save word embeddings as delta

3. **Embedding Utilities:**
   - `embed_texts()` - Generate embeddings via OpenAI
   - `build_line_payload()`, `build_word_payload()` - Format data for ChromaDB

4. **S3 Operations:**
   - `download_snapshot_atomic()`, `upload_snapshot_atomic()` - Snapshot management

### What's in `receipt_agent` (Orchestration)

`receipt_agent.lifecycle.embedding_manager` provides **business logic orchestration**:

- `create_embeddings_and_compaction_run()` - High-level function that:
  1. Generates embeddings using OpenAI
  2. Creates local ChromaDB deltas
  3. Uploads deltas to S3
  4. Creates `CompactionRun` entity
  5. Optionally adds `CompactionRun` to DynamoDB (triggers async compaction)

## Problem Statement

### Why Move to `receipt_chroma`?

1. **Better Package Boundaries**: Embedding orchestration is ChromaDB-focused, not agent-specific
2. **Reusability**: Other services (not just agents) need embedding functionality
3. **Consistency**: All ChromaDB operations should live in one package
4. **Reduced Duplication**: Combine agent has duplicate implementation

### Challenges

1. **Backward Compatibility**: Must not break existing consumers during migration
2. **Return Value Design**: Function should return snapshotted ChromaClient instances for immediate querying after compaction

## Migration Strategy

### Phase 1: Create Embedding Result Class in `receipt_chroma`

**Goal**: Return an object that provides access to snapshotted clients and compaction status

**Approach**: Create a result class that encapsulates the CompactionRun and provides methods to wait for compaction and access snapshotted clients

The `EmbeddingResult` class should:
- Store the CompactionRun entity
- Provide `wait_for_compaction_to_finish()` method that polls DynamoDB for CompactionRun status
- Separate Clients for the `lines` and `words` Snapshots with the newly embedded ReceiptLines and ReceiptWords included


**Key Design Decisions:**

1. **Simple Input**: Takes ReceiptLines and ReceiptWords directly. It also takes the necessary infra to pull snapshots, query Dynamo, etc. It should optionally take the ReceiptMetadata and ReceiptWordLabels for the optional embedding metadata.
2. **Return Object**: Returns `EmbeddingResult` with methods for waiting and accessing clients
3. **Snapshotted Clients**: Provides ChromaClient instances with latest snapshot after compaction
4. **Resource Management**: EmbeddingResult manages client lifecycle with `close()` method

### Phase 2: Create Backward-Compatible Wrapper in `receipt_agent`

**Goal**: Keep existing `receipt_agent` API working

The wrapper should:
- Maintain the original function signature (takes DynamoClient as first parameter)
- Fetch receipt data if not provided (backward compatibility)
- Call the new `receipt_chroma` implementation
- Return CompactionRun for backward compatibility (callers can access EmbeddingResult by calling receipt_chroma directly if they need snapshotted clients)

### Phase 3: Update Combine Receipts Agent

**Goal**: Use `receipt_chroma` directly instead of duplicate implementation

The Combine Receipts agent should:
- Remove duplicate `embedding_utils.py` implementation
- Import `create_embeddings_and_compaction_run` from `receipt_chroma`
- Use the new API with ReceiptLines and ReceiptWords as required parameters
- Call `wait_for_compaction_to_finish()` on the EmbeddingResult
- Optionally access snapshotted clients if needed

**Benefits:**
- Removes duplicate code
- Uses canonical implementation
- Easier to maintain
- Provides access to snapshotted clients after compaction


## Success Criteria

- ✅ `receipt_chroma` has `create_embeddings_and_compaction_run()` function
- ✅ Function takes ReceiptLines and ReceiptWords as required parameters
- ✅ Function returns `EmbeddingResult` with:
  - `wait_for_compaction_to_finish()` method
  - `close()` method for resource cleanup
- ✅ Backward compatibility maintained (receipt_agent wrapper returns CompactionRun)
- ✅ Combine Receipts agent uses `receipt_chroma` directly
- ✅ No duplicate implementations
- ✅ All tests pass
- ✅ No breaking changes to existing consumers

## Open Questions

1. **Should we support batch embedding operations?**
   - **Answer**: Future enhancement, out of scope for this migration.

2. **What about the `_upload_bundled_delta_to_s3` helper?**
   - **Answer**: Use existing `ChromaClient.persist_and_upload_delta()` method.

3. **Should EmbeddingResult cache clients or always download fresh snapshots?**
   - **Answer**: Always donwload fresh snapshot.

## References

- Current implementation: `receipt_agent/receipt_agent/lifecycle/embedding_manager.py`
- Combine agent usage: `infra/combine_receipts_step_functions/lambdas/embedding_utils.py`
- Receipt lifecycle docs: `receipt_agent/receipt_agent/lifecycle/README.md`
