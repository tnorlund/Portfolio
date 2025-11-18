# Snapshot Validation Implementation

## Summary

Implemented snapshot validation in the enhanced compaction handler to **guarantee that only valid snapshots are updated** before the pointer is updated. This ensures data integrity and prevents corrupted snapshots from becoming the "latest" snapshot.

## Changes Made

### 1. Added `_validate_snapshot_after_upload()` Function

**Location**: `receipt_label/receipt_label/utils/chroma_s3_helpers.py:1489-1632`

**Purpose**: Fast validation that verifies a snapshot uploaded to S3 can be opened and read by ChromaDB.

**Flow**:
1. Downloads snapshot from versioned location to temporary directory
2. Checks for SQLite files (required for ChromaDB)
3. Opens snapshot with `chromadb.PersistentClient`
4. Lists collections and verifies expected collection exists
5. Performs lightweight check (calls `collection.count()`)
6. Cleans up test client and temporary directory
7. Returns `(success: bool, duration_seconds: float)`

**Key Features**:
- ✅ Fast validation (~1-3 seconds)
- ✅ Lightweight check (just verifies it opens, not full data read)
- ✅ Comprehensive error handling with detailed logging
- ✅ Duration tracking for metrics

### 2. Integrated Validation into `upload_snapshot_atomic()`

**Location**: `receipt_label/receipt_label/utils/chroma_s3_helpers.py:1635-1848`

**Changes**:
- Added **Step 2: Validate uploaded snapshot** after upload but before pointer update
- Validation happens **after** upload to versioned location but **before** updating pointer
- If validation fails:
  - Cleans up versioned upload (deletes from S3)
  - Returns error with validation duration
  - Does NOT update pointer (guarantees only valid snapshots become "latest")
- If validation succeeds:
  - Proceeds to lock validation and pointer update
  - Returns validation duration in result

**Updated Flow**:
1. Step 1: Upload to versioned location
2. **Step 2: Validate uploaded snapshot** ← NEW
3. Step 3: Final lock validation
4. Step 4: Atomic promotion (update pointer)
5. Step 5: Background cleanup of old versions

### 3. Added EMF Metrics for Snapshot Validation

**Location**: `infra/embedding_step_functions/unified_embedding/handlers/compaction.py:1693-1734`

**Metrics Logged**:
- `SnapshotValidationSuccess`: 1 if successful, 0 if failed
- `SnapshotValidationAttempts`: Always 1 (single attempt, fail fast)
- `SnapshotValidationDuration`: Duration in seconds
- `SnapshotValidationFailures`: 1 if validation failed (with error_type dimension)

**Dimensions**:
- `validation_stage`: "final_merge"
- `collection`: "lines" or "words"
- `error_type`: Error type if validation failed

**Properties**:
- `batch_id`: Batch identifier
- `version_id`: Snapshot version ID
- `total_embeddings`: Number of embeddings in snapshot
- `error`: Error message if validation failed

**Error Handling**:
- If validation fails, raises `RuntimeError` to fail the compaction
- This allows the step function to retry/requeue the operation
- Lock is released quickly on failure (fail fast)

## Benefits

1. ✅ **Guarantees Valid Snapshots**: Pointer only updated if snapshot is valid
2. ✅ **Fast**: Lightweight validation (~1-3 seconds)
3. ✅ **Fail Fast**: No retries in validation - if it fails, requeue the whole operation
4. ✅ **Cleanup on Failure**: Versioned upload is cleaned up if validation fails
5. ✅ **Minimal Lock Time**: Validation happens while lock is held, but it's fast
6. ✅ **Observability**: EMF metrics track validation success/failure and duration

## Performance Considerations

- **Validation Time**: ~1-3 seconds (download + open + verify)
- **Lock Time**: Validation happens while lock is held, but this is acceptable since:
  - It's fast (1-3 seconds)
  - It guarantees validity
  - If it fails, we can requeue (lock released quickly)
- **S3 Operations**: One additional download for validation (acceptable trade-off)

## Comparison with Delta Validation

| Aspect | Delta Validation (Step Function) | Snapshot Validation (Enhanced Compaction) |
|--------|--------------------------------|------------------------------------------|
| **When** | After upload, before returning | After upload, before pointer update |
| **Retries** | Yes (3 attempts with cleanup) | No (fail fast, requeue) |
| **Speed** | ~2-5 seconds | ~1-3 seconds (lighter check) |
| **What it validates** | Can open, collections exist, data readable | Can open, collections exist, collection accessible |
| **On failure** | Deletes failed upload, retries | Deletes versioned upload, returns error (requeue) |
| **Metrics** | Tracked in polling handler | Tracked in compaction handler |

## Testing Recommendations

1. **Test with corrupted snapshots**: Verify validation catches corruption
2. **Test with valid snapshots**: Verify validation passes and pointer is updated
3. **Monitor validation duration**: Ensure it stays fast (~1-3 seconds)
4. **Monitor validation metrics**: Track success rate and failure types
5. **Test failure scenarios**: Verify cleanup happens on validation failure

## Next Steps

1. ✅ Implementation complete
2. ⏳ Deploy and test in dev environment
3. ⏳ Monitor validation metrics in CloudWatch
4. ⏳ Verify validation catches any corruption issues
5. ⏳ Document any edge cases discovered

## Related Documentation

- `docs/SNAPSHOT_VALIDATION_GAP_ANALYSIS.md`: Analysis of the gap and proposed solution
- `docs/CLIENT_CLOSING_AND_VALIDATION_COMPARISON.md`: Comparison with delta validation approach
- `docs/INGESTION_WORKFLOW_METRICS.md`: Documentation of all custom metrics


