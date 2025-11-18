# All-or-Nothing Behavior - No Partial Ingestion

## Policy

**All ingestion operations must either succeed completely or fail completely. No partial ingestion is allowed.**

## Rationale

1. **Data Integrity**: Partial ingestion can lead to inconsistent state
2. **Silent Failures**: Partial ingestion can hide problems
3. **Reproducibility**: All-or-nothing makes it clear when something failed
4. **Proper Handling**: Failures force proper investigation and re-processing

## Implementation

### Chunk Processing

**File**: `infra/embedding_step_functions/unified_embedding/handlers/compaction.py`

**Function**: `process_chunk_deltas()`

**Behavior**:
- If any delta fails to process (corrupted or otherwise), the entire chunk fails
- No try-catch that skips deltas and continues
- Exception propagates up, causing chunk to fail
- Step Function retry logic handles retries

```python
# All-or-nothing behavior
for i, delta in enumerate(collection_deltas):
    delta_key = delta["delta_key"]

    # If this fails, exception propagates and entire chunk fails
    embeddings_added = download_and_merge_delta(
        bucket, delta_key, collection, temp_dir
    )
    # No try-catch that skips and continues
```

### Delta Upload (PollBatches)

**File**: `receipt_label/receipt_label/vector_store/legacy_helpers.py`

**Function**: `produce_embedding_delta()`

**Behavior**:
- Validation happens after upload
- If validation fails, retry (up to 3 times)
- If all retries fail, raise exception (delta not created)
- No partial uploads - either fully validated delta or no delta

```python
s3_key = actual_client.persist_and_upload_delta(
    bucket=bucket_name,
    s3_prefix=full_delta_prefix,
    max_retries=3,
    validate_after_upload=True,  # Must validate or fail
)
```

## Error Handling Strategy

### 1. Prevention (Primary Strategy)

- **Validation at upload time**: Prevents corrupted deltas from being created
- **Client closing**: Ensures SQLite files are properly flushed before upload
- **Retry logic**: Automatically retries on transient failures

### 2. Failure (Secondary Strategy)

- **Fail fast**: If corruption is detected, fail immediately
- **No silent failures**: All failures are logged and propagated
- **Clear error messages**: Errors include context about what failed and why

### 3. Recovery (Tertiary Strategy)

- **Re-process batches**: If a batch created corrupted deltas, re-process the entire batch
- **Step Function retries**: Step Functions retry logic handles transient failures
- **Manual intervention**: For persistent issues, manual investigation and re-processing

## What This Means

### ✅ Good Behavior

- Chunk fails if any delta is corrupted → Forces re-processing of the batch
- Delta upload fails if validation fails → Prevents corrupted deltas from being created
- Execution fails if any chunk fails → Clear indication that something went wrong

### ❌ Bad Behavior (Avoided)

- ~~Chunk succeeds with some deltas skipped~~ → Partial ingestion
- ~~Delta upload succeeds even if validation fails~~ → Corrupted deltas in S3
- ~~Execution continues with partial data~~ → Silent failures

## Monitoring

### What to Monitor

1. **Chunk Processing Failures**: Track when chunks fail due to corrupted deltas
2. **Delta Validation Failures**: Track when validation fails during upload
3. **Retry Rates**: Track how often retries are needed
4. **Execution Success Rate**: Track overall execution success rate

### Alerts

- Alert on chunk processing failures (indicates corrupted deltas)
- Alert on delta validation failures (indicates upload issues)
- Alert on high retry rates (indicates systemic issues)

## Related Documentation

- `docs/DELTA_VALIDATION_FIX_SUMMARY.md` - Validation implementation
- `docs/CHUNK_40_FIX_IMPLEMENTATION.md` - Chunk processing behavior
- `docs/POLLBATCHES_VALIDATION_VERIFICATION.md` - Upload validation

