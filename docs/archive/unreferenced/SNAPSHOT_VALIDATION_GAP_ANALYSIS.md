# Snapshot Validation Gap Analysis

## Current State

### Enhanced Compaction Handler: `perform_final_merge()`

**Location**: `infra/embedding_step_functions/unified_embedding/handlers/compaction.py:1328-1800`

**Current Flow**:
1. ✅ Closes ChromaDB client before upload (`close_chromadb_client()`)
2. ✅ Uploads snapshot to versioned location (`upload_snapshot_atomic()`)
3. ✅ Updates pointer file atomically
4. ❌ **NO VALIDATION** of uploaded snapshot before pointer update

**Problem**: The snapshot is uploaded and the pointer is updated **without validating** that the uploaded snapshot is valid and can be opened by ChromaDB.

### Comparison: Step Function Delta Validation

**Location**: `receipt_label/receipt_label/vector_store/client/chromadb_client.py:425-557`

**Flow**:
1. ✅ Closes ChromaDB client before upload
2. ✅ Uploads delta to S3
3. ✅ **Validates delta after upload** (`_validate_delta_after_upload()`)
   - Downloads from S3
   - Checks for SQLite files
   - Opens with ChromaDB
   - Lists collections
   - Reads from collections (count())
4. ✅ Retries with cleanup if validation fails
5. ✅ Only returns success if validation passes

## The Gap

The enhanced compaction handler does **NOT** validate snapshots after upload, which means:
- ❌ Corrupted snapshots could be uploaded and pointer updated
- ❌ Invalid snapshots could become the "latest" snapshot
- ❌ Downstream consumers could get corrupted snapshots
- ❌ No guarantee that snapshots are valid before pointer update

## Requirements

1. ✅ **GUARANTEE valid snapshots**: Only update pointer if snapshot is valid
2. ✅ **Fast**: Minimize lockout time (validation should be quick)
3. ✅ **Fail fast**: It's okay if it fails (can requeue)

## Proposed Solution

Add validation **BEFORE** updating the pointer, similar to delta validation but optimized for speed:

### Fast Snapshot Validation

**Location**: Add to `upload_snapshot_atomic()` or create `_validate_snapshot_after_upload()`

**Flow**:
1. Upload to versioned location (already done)
2. **Validate versioned location** (NEW):
   - Download from versioned location to temp directory
   - Check for SQLite files
   - Open with ChromaDB (`PersistentClient`)
   - List collections
   - Verify collection exists and can be accessed (lightweight check)
   - Clean up temp directory
3. **Only update pointer if validation passes**
4. **If validation fails**: Clean up versioned upload, return error (can requeue)

**Optimizations for Speed**:
- ✅ Validate **versioned location** (not pointer) - no race condition
- ✅ Lightweight validation (just verify it opens, not full data read)
- ✅ Fail fast (no retries in validation - if it fails, requeue the whole operation)
- ✅ Cleanup on failure (delete versioned upload)

### Implementation Details

```python
def _validate_snapshot_after_upload(
    bucket: str,
    versioned_key: str,
    collection_name: str,
    s3_client: Optional[Any] = None,
) -> tuple[bool, float]:
    """
    Validate that a snapshot uploaded to S3 can be opened and read by ChromaDB.

    This is a fast validation that verifies the snapshot is valid before
    updating the pointer. If validation fails, the versioned upload should
    be cleaned up.

    Returns:
        Tuple of (success: bool, duration_seconds: float)
    """
    validation_start_time = time.time()
    temp_dir = None

    try:
        temp_dir = tempfile.mkdtemp()

        # Download snapshot from versioned location
        download_result = download_snapshot_from_s3(
            bucket=bucket,
            snapshot_key=versioned_key,
            local_snapshot_path=temp_dir,
            verify_integrity=False,  # Skip hash check for speed
        )

        if download_result.get("status") != "downloaded":
            validation_duration = time.time() - validation_start_time
            logger.error(
                "Failed to download snapshot for validation: %s (duration: %.2fs)",
                versioned_key,
                validation_duration,
            )
            return False, validation_duration

        # Check for SQLite files
        temp_path = Path(temp_dir)
        sqlite_files = list(temp_path.rglob("*.sqlite*"))
        if not sqlite_files:
            validation_duration = time.time() - validation_start_time
            logger.error(
                "No SQLite files found in snapshot (duration: %.2fs)",
                validation_duration,
            )
            return False, validation_duration

        # Try to open with ChromaDB
        try:
            test_client = chromadb.PersistentClient(path=temp_dir)
            collections = test_client.list_collections()

            if not collections:
                validation_duration = time.time() - validation_start_time
                logger.error(
                    "No collections found in snapshot (duration: %.2fs)",
                    validation_duration,
                )
                return False, validation_duration

            # Verify expected collection exists
            collection_names = [c.name for c in collections]
            if collection_name not in collection_names:
                validation_duration = time.time() - validation_start_time
                logger.error(
                    "Expected collection '%s' not found in snapshot (found: %s, duration: %.2fs)",
                    collection_name,
                    collection_names,
                    validation_duration,
                )
                return False, validation_duration

            # Lightweight check: verify collection can be accessed
            test_collection = test_client.get_collection(collection_name)
            count = test_collection.count()  # Lightweight operation

            # Clean up test client
            del test_client
            import gc
            gc.collect()

            validation_duration = time.time() - validation_start_time
            logger.info(
                "Snapshot validation successful: %s (collections: %d, count: %d, duration: %.2fs)",
                versioned_key,
                len(collections),
                count,
                validation_duration,
            )
            return True, validation_duration

        except Exception as e:
            validation_duration = time.time() - validation_start_time
            logger.error(
                "Failed to open snapshot with ChromaDB during validation: %s (type: %s, duration: %.2fs)",
                versioned_key,
                type(e).__name__,
                validation_duration,
                exc_info=True,
            )
            return False, validation_duration

    except Exception as e:
        validation_duration = time.time() - validation_start_time
        logger.error(
            "Error during snapshot validation: %s (type: %s, duration: %.2fs)",
            versioned_key,
            type(e).__name__,
            validation_duration,
            exc_info=True,
        )
        return False, validation_duration
    finally:
        # Clean up temp directory
        if temp_dir:
            try:
                shutil.rmtree(temp_dir, ignore_errors=True)
            except Exception:
                pass
```

### Integration into `upload_snapshot_atomic()`

Modify `upload_snapshot_atomic()` to validate **after** uploading to versioned location but **before** updating pointer:

```python
# Step 1: Upload to versioned location (no race condition possible)
upload_result = upload_snapshot_with_hash(...)

if upload_result.get("status") != "uploaded":
    return {"status": "error", ...}

# Step 2: VALIDATE versioned upload (NEW)
validation_result, validation_duration = _validate_snapshot_after_upload(
    bucket=bucket,
    versioned_key=versioned_key,
    collection_name=collection,
    s3_client=s3_client,
)

if not validation_result:
    # Clean up versioned upload
    logger.error(
        "Snapshot validation failed, cleaning up versioned upload: %s",
        versioned_key,
    )
    try:
        _cleanup_s3_prefix(s3_client, bucket, versioned_key)
    except Exception as cleanup_error:
        logger.warning("Failed to cleanup versioned upload: %s", cleanup_error)

    return {
        "status": "error",
        "error": "Snapshot validation failed after upload",
        "collection": collection,
        "version_id": version_id,
        "validation_duration": validation_duration,
    }

# Step 3: Final lock validation before atomic promotion
if lock_manager and not lock_manager.validate_ownership():
    # Clean up versioned upload
    _cleanup_s3_prefix(s3_client, bucket, versioned_key)
    return {"status": "error", ...}

# Step 4: Atomic promotion - update pointer (only if validation passed)
s3_client.put_object(Bucket=bucket, Key=pointer_key, Body=version_id.encode("utf-8"), ...)
```

## Benefits

1. ✅ **Guarantees valid snapshots**: Pointer only updated if snapshot is valid
2. ✅ **Fast**: Lightweight validation (just verify it opens, not full data read)
3. ✅ **Fail fast**: No retries in validation - if it fails, requeue the whole operation
4. ✅ **Cleanup on failure**: Versioned upload is cleaned up if validation fails
5. ✅ **Minimal lock time**: Validation happens after upload, before pointer update (lock still held)

## Performance Considerations

- **Validation time**: ~1-3 seconds (download + open + verify)
- **Lock time**: Validation happens while lock is held, but this is acceptable since:
  - It's fast (1-3 seconds)
  - It guarantees validity
  - If it fails, we can requeue (lock released quickly)
- **S3 operations**: One additional download for validation (acceptable trade-off)

## Comparison with Delta Validation

| Aspect | Delta Validation (Step Function) | Snapshot Validation (Proposed) |
|--------|--------------------------------|-------------------------------|
| **When** | After upload, before returning | After upload, before pointer update |
| **Retries** | Yes (3 attempts with cleanup) | No (fail fast, requeue) |
| **Speed** | ~2-5 seconds | ~1-3 seconds (lighter check) |
| **What it validates** | Can open, collections exist, data readable | Can open, collections exist, collection accessible |
| **On failure** | Deletes failed upload, retries | Deletes versioned upload, returns error (requeue) |

## Next Steps

1. ✅ Add `_validate_snapshot_after_upload()` function
2. ✅ Integrate into `upload_snapshot_atomic()` (validate before pointer update)
3. ✅ Add metrics for validation (success/failure, duration)
4. ✅ Test with corrupted snapshots to ensure validation catches issues
5. ✅ Monitor validation duration to ensure it stays fast


