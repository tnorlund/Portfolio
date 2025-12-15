# Validation Code Not Used by Polling Lambda

## Problem

The validation code we deployed is **NOT being used** by the polling Lambda that creates deltas!

## Root Cause

The polling Lambda (`embedding-line-poll` and `embedding-word-poll`) uses `produce_embedding_delta()` from `legacy_helpers.py`, which:

1. ✅ Closes the ChromaDB client (lines 114-132)
2. ❌ **BUT** uses `S3Operations.upload_delta()` directly (line 150)
3. ❌ **NOT** `persist_and_upload_delta()` which has validation

## Code Flow

### Polling Lambda Flow
```
poll.py:save_line_embeddings_as_delta()
  → produce_embedding_delta() [legacy_helpers.py]
    → Creates ChromaDB client
    → Upserts vectors
    → Closes client (_close_client_for_upload)
    → S3Operations.upload_delta()  ❌ NO VALIDATION
```

### What We Deployed
```
persist_and_upload_delta() [chromadb_client.py]
  → Closes client
  → Uploads to S3
  → _validate_delta_after_upload()  ✅ VALIDATION
  → Retry logic if validation fails
```

## Impact

- **Delta uploaded at 10:14:34 PST** (48 seconds after execution started)
- **Validation code deployed at 10:10:14 PST** (4 minutes before delta upload)
- **BUT validation was NOT used** because `produce_embedding_delta()` doesn't call `persist_and_upload_delta()`

## Solution

Update `produce_embedding_delta()` in `legacy_helpers.py` to use `persist_and_upload_delta()` instead of `S3Operations.upload_delta()`.

### Current Code (line 150):
```python
s3_key = s3_ops.upload_delta(
    local_directory=delta_dir,
    delta_prefix=full_delta_prefix,
    collection_name=collection_name,
    database_name=database_name,
)
```

### Should Be:
```python
# Use ChromaDBClient's persist_and_upload_delta which includes validation
client.persist_and_upload_delta(
    bucket=bucket_name,
    s3_prefix=full_delta_prefix,
    max_retries=3,
    validate_after_upload=True,
)
```

## Why This Happened

The validation code was added to `ChromaDBClient.persist_and_upload_delta()`, but:
- The polling Lambda uses `produce_embedding_delta()` (legacy helper)
- `produce_embedding_delta()` creates a client but doesn't use its `persist_and_upload_delta()` method
- Instead, it manually closes the client and uses `S3Operations.upload_delta()` directly

## Next Steps

1. Update `produce_embedding_delta()` to use `client.persist_and_upload_delta()`
2. Deploy the fix
3. Monitor for new delta corruption errors

