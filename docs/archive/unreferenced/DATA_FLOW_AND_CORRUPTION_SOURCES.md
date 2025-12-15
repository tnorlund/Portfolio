# Data Flow and Corruption Sources

## Data Flow Overview

### 1. Polling Phase (Fresh Data from OpenAI) ✅

**Location**: `receipt_label/receipt_label/embedding/line/poll.py`

**Process**:
```python
def download_openai_batch_result(openai_batch_id: str):
    batch = client_manager.openai.batches.retrieve(openai_batch_id)
    output_file_id = batch.output_file_id
    response = client_manager.openai.files.content(output_file_id)  # ← FRESH DATA FROM OPENAI API
    # Parse and return results
```

**Key Point**: ✅ **Every run downloads fresh data directly from OpenAI's API** - no caching or reuse of previous results.

### 2. Delta Creation (New Deltas from Fresh Data) ⚠️

**Location**: `receipt_label/receipt_label/vector_store/legacy_helpers.py::produce_embedding_delta()`

**Process**:
1. Receives fresh embedding results from OpenAI
2. Creates ChromaDB delta locally
3. Uploads delta to S3

**Corruption Risk**:
- ✅ **NEW deltas** (after validation fix): Protected by validation + retry logic
- ❌ **OLD deltas** (before validation fix): May be corrupted if uploaded while client was open

### 3. Compaction Phase (Merges Deltas into Snapshots) ⚠️

**Location**: `infra/embedding_step_functions/unified_embedding/handlers/compaction.py`

**Process**:
1. Downloads **existing snapshot** from S3 (or EFS)
2. Downloads **deltas** from S3 (created by polling)
3. Merges deltas into snapshot
4. Uploads updated snapshot back to S3

**Corruption Risk**:
- ❌ **Corrupted snapshots**: If snapshot is corrupted, compaction fails when trying to load it
- ❌ **Corrupted deltas**: If delta is corrupted, compaction fails when trying to merge it

## What `dev.delete_corrupted_snapshots.py` Does

**Deletes**:
- ✅ `latest-pointer.txt` files
- ✅ `latest/` snapshot directories
- ✅ `timestamped/` snapshot versions (optional)

**Keeps**:
- ✅ All deltas (needed for rebuilding snapshots)

**Effect**:
- Forces system to rebuild snapshots from deltas
- **BUT**: If deltas themselves are corrupted, rebuild will still fail

## Answer to Your Question

### "Does this process use fresh data from OpenAI every run?"

**YES** ✅ - The polling phase always downloads fresh data from OpenAI's API.

### "Will no previous artifacts corrupt a run because I ran the cleanup script?"

**PARTIALLY** ⚠️ - The cleanup script helps, but:

1. **Snapshots**: ✅ Deleted by cleanup script → Forces rebuild from deltas
2. **Deltas**: ❌ **NOT deleted** by cleanup script → Old corrupted deltas still exist
3. **New Deltas**: ✅ Protected by validation fix (after deployment)

## What Can Still Cause Failures

### Scenario 1: Corrupted Delta from Before Validation Fix
```
Timeline:
- Day 1: Delta created (before validation fix) → Corrupted → Uploaded to S3
- Day 2: Cleanup script runs → Deletes snapshots
- Day 3: New run starts → Polls fresh data from OpenAI ✅
- Day 3: Creates new delta → Protected by validation ✅
- Day 3: Compaction tries to merge old corrupted delta → FAILS ❌
```

### Scenario 2: Corrupted Snapshot Not Cleaned Up
```
Timeline:
- Day 1: Snapshot corrupted → Still in S3
- Day 2: New run starts → Polls fresh data ✅
- Day 2: Compaction downloads corrupted snapshot → FAILS ❌
```

## Recommendations

### 1. Run Cleanup Script ✅
```bash
python dev.delete_corrupted_snapshots.py --env dev --collection both
```
This deletes corrupted snapshots and forces rebuild.

### 2. Deploy Validation Fix ✅
The fix we just made ensures **new deltas** are validated and retried.

### 3. Monitor for Old Corrupted Deltas ⚠️
If compaction fails on a specific delta:
- Check when the delta was created (S3 object timestamp)
- If created before validation fix deployment, it may be corrupted
- Consider deleting that specific delta to force re-polling

### 4. Consider Delta Cleanup Script (Future)
A script to identify and delete corrupted deltas would be helpful:
- Check delta creation timestamp
- Attempt to validate delta (try to open with ChromaDB)
- Delete if corrupted and created before validation fix

## Summary

| Artifact | Source | Corrupted? | Cleanup Script? | Validation Fix? |
|----------|--------|------------|----------------|------------------|
| **Polling Data** | OpenAI API | ✅ Always fresh | N/A | N/A |
| **New Deltas** | Fresh OpenAI data | ✅ Protected | N/A | ✅ Yes |
| **Old Deltas** | Previous runs | ⚠️ May be corrupted | ❌ Not deleted | ❌ Created before fix |
| **Snapshots** | Merged from deltas | ⚠️ May be corrupted | ✅ Deleted | N/A |

**Bottom Line**:
- ✅ Fresh data from OpenAI every run
- ✅ Cleanup script helps with snapshots
- ⚠️ Old corrupted deltas can still cause failures
- ✅ New deltas are protected by validation

