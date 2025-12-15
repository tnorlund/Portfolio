# Final Merge Optimization Proposal

## Problem Statement

The WordFinalMerge step is slow because it does ALL work under lock:
1. Download snapshot from S3/EFS
2. Merge all intermediate chunks
3. Upload final snapshot

This results in long lock hold times (potentially minutes), causing lock contention and failures.

## Current Architecture

### Enhanced Compaction Handler (Stream Processor) - FAST ✅
- **Lock ID**: `chroma-{collection.value}-update` (shared across batches)
- **Lock Strategy**: Optimized two-phase locking
  - **Phase 1**: Quick CAS validation under lock (~100ms), then release
  - **Phase 2**: Heavy work OFF-LOCK (download snapshot, merge deltas, apply updates)
  - **Phase 3**: Re-acquire lock, CAS validation, upload (~2-3s)
- **Total Lock Time**: ~2-3 seconds
- **Why Fast**: Most work happens off-lock

### Step Function Final Merge - SLOW ❌
- **Lock ID**: `chroma-final-merge-{batch_id}` (unique per batch)
- **Lock Strategy**: Single-phase locking
  - **All work under lock**: Download snapshot, merge chunks, upload
- **Total Lock Time**: Potentially minutes (depends on chunk count)
- **Why Slow**: Everything happens under lock

## Optimization Strategies

### Strategy 1: Apply Two-Phase Locking Pattern (Recommended)

Apply the same optimized locking pattern from the enhanced compaction handler to the step function final merge.

**Implementation**:
1. **Phase 1**: Quick CAS validation under lock (~100ms)
   - Acquire lock
   - Read snapshot pointer from S3
   - Validate pointer hasn't changed
   - Release lock

2. **Phase 2**: Heavy work OFF-LOCK
   - Download snapshot from S3/EFS
   - Merge all intermediate chunks
   - Prepare final snapshot

3. **Phase 3**: Final upload under lock (~2-3s)
   - Re-acquire lock
   - CAS validation (check pointer hasn't changed)
   - Upload final snapshot
   - Update pointer
   - Release lock

**Benefits**:
- Reduces lock time from minutes to ~2-3 seconds
- Allows parallel processing (multiple batches can work off-lock simultaneously)
- Prevents lock contention
- Matches the proven pattern from enhanced compaction handler

**Code Changes**:
- Modify `final_merge_handler()` in `compaction.py` to:
  1. Acquire lock, validate pointer, release lock
  2. Do heavy work off-lock
  3. Re-acquire lock, validate pointer, upload, release lock

### Strategy 2: Increase Chunk Sizes

Reduce the number of chunks to merge by increasing chunk sizes.

**Current Configuration**:
- `CHUNK_SIZE_WORDS = 5` (5 deltas per chunk)
- `CHUNK_SIZE_LINES = 10` (10 deltas per chunk)
- `group_size = 10` (10 chunks per group)

**Proposed Configuration**:
- `CHUNK_SIZE_WORDS = 15-20` (15-20 deltas per chunk)
- `CHUNK_SIZE_LINES = 25-30` (25-30 deltas per chunk)
- `group_size = 20` (20 chunks per group, or eliminate grouping entirely)

**Impact**:
- **Fewer chunks**: If we have 50 deltas:
  - Current: 10 chunks (5 deltas each) → 1-2 groups → 1-2 intermediate merges → final merge of 1-2 chunks
  - Proposed: 3-4 chunks (15 deltas each) → 1 group → 1 intermediate merge → final merge of 1 chunk
- **Faster final merge**: Merging 1 chunk is much faster than merging 5-10 chunks
- **Less time under lock**: Fewer chunks = faster merge = less lock time

**Trade-offs**:
- **Memory**: Larger chunks use more memory per Lambda invocation
- **Parallelism**: Fewer chunks = less parallelism (but final merge is the bottleneck anyway)
- **Risk**: If a chunk fails, more work is lost (but we have retries)

**Code Changes**:
- Update `CHUNK_SIZE_WORDS` and `CHUNK_SIZE_LINES` in:
  - `infra/embedding_step_functions/unified_embedding/handlers/split_into_chunks.py`
  - `infra/embedding_step_functions/simple_lambdas/split_into_chunks/handler.py`
- Update `group_size` in:
  - `infra/embedding_step_functions/components/word_workflow.py`
  - `infra/embedding_step_functions/components/line_workflow.py`

### Strategy 3: Combine Both Strategies (Best)

Apply both optimizations together for maximum benefit.

**Result**:
- **Two-phase locking**: Reduces lock time to ~2-3 seconds
- **Larger chunks**: Reduces number of chunks to merge (ideally 1 chunk)
- **Combined effect**: Final merge with 1 chunk under lock for ~2-3 seconds = minimal lock contention

## Implementation Plan

### Phase 1: Increase Chunk Sizes (Quick Win)

1. **Update chunk size configuration**:
   ```python
   # In split_into_chunks.py
   CHUNK_SIZE_WORDS = int(os.environ.get("CHUNK_SIZE_WORDS", "15"))  # Increase from 5
   CHUNK_SIZE_LINES = int(os.environ.get("CHUNK_SIZE_LINES", "25"))  # Increase from 10
   ```

2. **Update group size**:
   ```python
   # In word_workflow.py and line_workflow.py
   "group_size": 20,  # Increase from 10, or eliminate grouping if chunks are large enough
   ```

3. **Test with small batch**:
   - Deploy to dev
   - Run a test batch
   - Monitor lock times and final merge duration

**Expected Impact**:
- 50-70% reduction in final merge time (fewer chunks to merge)
- 50-70% reduction in lock hold time

### Phase 2: Apply Two-Phase Locking (Major Optimization)

1. **Refactor `final_merge_handler()`**:
   - Extract snapshot download/merge logic into separate function
   - Implement Phase 1: Quick CAS validation
   - Implement Phase 2: Heavy work off-lock
   - Implement Phase 3: Final upload under lock

2. **Update lock acquisition logic**:
   - Acquire lock for Phase 1 (validation)
   - Release lock after validation
   - Re-acquire lock for Phase 3 (upload)

3. **Add CAS validation**:
   - Check snapshot pointer before and after heavy work
   - Skip upload if pointer changed (another process already published)

**Expected Impact**:
- 90-95% reduction in lock hold time (from minutes to ~2-3 seconds)
- Eliminates lock contention issues
- Allows parallel processing of multiple batches

### Phase 3: Optimize for Single Chunk Final Merge

If chunk sizes are large enough, we can optimize for the common case where final merge only has 1 chunk to merge.

**Optimization**:
- If `len(chunk_results) == 1`, skip intermediate merge entirely
- Directly merge the single chunk into the snapshot
- Even faster final merge

## Code Changes Required

### 1. Update Chunk Sizes

**File**: `infra/embedding_step_functions/unified_embedding/handlers/split_into_chunks.py`
```python
# Change from:
CHUNK_SIZE = int(os.environ.get("CHUNK_SIZE", "10"))

# To:
CHUNK_SIZE_WORDS = int(os.environ.get("CHUNK_SIZE_WORDS", "15"))
CHUNK_SIZE_LINES = int(os.environ.get("CHUNK_SIZE_LINES", "25"))
```

**File**: `infra/embedding_step_functions/simple_lambdas/split_into_chunks/handler.py`
```python
# Change from:
CHUNK_SIZE_WORDS = int(os.environ.get("CHUNK_SIZE_WORDS", "5"))
CHUNK_SIZE_LINES = int(os.environ.get("CHUNK_SIZE_LINES", "10"))

# To:
CHUNK_SIZE_WORDS = int(os.environ.get("CHUNK_SIZE_WORDS", "15"))
CHUNK_SIZE_LINES = int(os.environ.get("CHUNK_SIZE_LINES", "25"))
```

### 2. Update Group Sizes

**File**: `infra/embedding_step_functions/components/word_workflow.py`
```python
# Change from:
"group_size": 10,

# To:
"group_size": 20,  # Or eliminate grouping if chunks are large enough
```

### 3. Apply Two-Phase Locking

**File**: `infra/embedding_step_functions/unified_embedding/handlers/compaction.py`

Refactor `final_merge_handler()` to:
1. Phase 1: Quick validation under lock
2. Phase 2: Heavy work off-lock
3. Phase 3: Final upload under lock

See `enhanced_compaction_handler.py` lines 525-747 for reference implementation.

## Expected Results

### Before Optimization
- **Lock Time**: 2-5 minutes (depending on chunk count)
- **Final Merge Time**: 2-5 minutes
- **Lock Contention**: High (locks held for long periods)
- **Failure Rate**: High (lock acquisition failures)

### After Optimization (Strategy 1 + 2)
- **Lock Time**: ~2-3 seconds (two-phase locking)
- **Final Merge Time**: 30-60 seconds (fewer chunks to merge)
- **Lock Contention**: Low (locks held for seconds, not minutes)
- **Failure Rate**: Low (minimal lock contention)

### After Optimization (Strategy 3 - Combined)
- **Lock Time**: ~2-3 seconds (two-phase locking)
- **Final Merge Time**: 10-30 seconds (single chunk merge)
- **Lock Contention**: Minimal (locks held for seconds, single chunk merge)
- **Failure Rate**: Very low (minimal lock contention, fast merges)

## Risk Assessment

### Low Risk
- **Increasing chunk sizes**: Well-tested pattern, just changing configuration
- **Updating group sizes**: Simple configuration change

### Medium Risk
- **Two-phase locking refactor**: More complex, but follows proven pattern from enhanced compaction handler
- **CAS validation**: Need to ensure pointer validation is correct

### Mitigation
1. **Gradual rollout**: Start with chunk size increases, then add two-phase locking
2. **Testing**: Test with small batches first, then scale up
3. **Monitoring**: Add metrics for lock times, merge durations, failure rates
4. **Rollback plan**: Keep old code path available via feature flag

## Monitoring

Add metrics to track:
- **Lock acquisition time**: Time to acquire lock
- **Lock hold time**: Total time lock is held
- **Final merge duration**: Time to complete final merge
- **Chunk count**: Number of chunks merged in final merge
- **Lock contention**: Number of lock acquisition failures
- **CAS validation failures**: Number of times pointer changed during merge

## Next Steps

1. ✅ **Review this proposal** with team
2. ⏳ **Implement Phase 1**: Increase chunk sizes (quick win)
3. ⏳ **Test Phase 1**: Deploy to dev, monitor results
4. ⏳ **Implement Phase 2**: Apply two-phase locking (major optimization)
5. ⏳ **Test Phase 2**: Deploy to dev, monitor results
6. ⏳ **Optimize Phase 3**: Single chunk merge optimization
7. ⏳ **Deploy to production**: Gradual rollout with monitoring

## Related Documentation

- `docs/EXECUTION_1618C96E_WORDFINALMERGE_LOCK_FAILURE_ANALYSIS.md` - Lock failure analysis
- `infra/LOCKING_BUSINESS_LOGIC.md` - Locking strategy overview
- `infra/chromadb_compaction/lambdas/enhanced_compaction_handler.py` - Reference implementation for two-phase locking

