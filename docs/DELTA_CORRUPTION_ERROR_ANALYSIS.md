# Delta Corruption Error Analysis

## Error: "unable to open database file" (code: 14)

### Root Cause

The error occurs when trying to open a delta file downloaded from S3 in `download_and_merge_delta()`. This happens when:

1. **Delta files were uploaded while ChromaDB client was still open** (before client closing fix was deployed)
   - SQLite files are locked/written to during upload
   - Files become corrupted or unreadable

2. **Corrupted download from S3**
   - Network issues during download
   - Partial file downloads

3. **Race condition** (less likely)
   - Multiple Lambdas trying to process the same delta simultaneously

### Error Location

**Function**: `download_and_merge_delta()` in `compaction.py`
**Line**: ~933 (when creating `PersistentClient(path=delta_temp)`)

**Error Flow**:
```
process_chunk_handler
  → process_chunk_deltas
    → download_and_merge_delta
      → download_from_s3 (downloads delta from S3)
      → chromadb.PersistentClient(path=delta_temp)  ← ERROR HERE
```

### Fixes Applied

1. **Added validation before opening delta**:
   - Check for SQLite files in delta directory
   - Provide better error messages if delta is corrupted

2. **Enhanced error handling**:
   - Wrap `PersistentClient` creation in try-except
   - Log detailed error information including delta_key and file structure
   - Re-raise with more context

3. **Fixed finally block**:
   - Check if `delta_client` exists before trying to close it
   - Prevents `NameError` if error occurs before client creation

### Prevention

The root cause is delta files being uploaded while ChromaDB clients are still open. This was fixed in:

- `receipt_label/receipt_label/vector_store/client/chromadb_client.py`:
  - `persist_and_upload_delta()` now calls `_close_client_for_upload()` before uploading
  - This ensures SQLite files are flushed and unlocked before upload

However, **existing delta files in S3 may still be corrupted** if they were uploaded before this fix was deployed.

### Solution

1. **Short-term**: The enhanced error handling will provide better diagnostics
2. **Long-term**:
   - All new delta files will be uploaded correctly (client closing fix deployed)
   - Corrupted delta files will fail gracefully with clear error messages
   - The ingestion step function will retry failed chunks (if retry logic is configured)

### Testing

To verify the fix:
1. Check Lambda logs for detailed error messages when delta corruption occurs
2. Verify that new delta files (uploaded after fix) can be opened successfully
3. Monitor for "unable to open database file" errors - they should decrease over time as old corrupted deltas are replaced

### Related Issues

- ChromaDB client closing workaround: `docs/CHROMADB_CLIENT_CLOSING_WORKAROUND.md`
- File handle leaks: Fixed by using `tempfile.mktemp` instead of `tempfile.NamedTemporaryFile`
- Error propagation: Fixed by raising exceptions instead of returning error objects

