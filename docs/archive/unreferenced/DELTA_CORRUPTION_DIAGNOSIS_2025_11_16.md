# Delta Corruption Diagnosis - November 16, 2025

## Execution Details

- **Execution ID**: `a3ecfe1f-c2a8-441b-b5de-46213043a5fe`
- **Step Function**: `line-ingest-sf-dev-1554303`
- **Status**: FAILED
- **Error**: `ChunkProcessingFailed`
- **Start Time**: 2025-11-16T17:12:03.866000-08:00
- **End Time**: 2025-11-16T17:14:02.721000-08:00

## Failed Chunks

### Chunk 35
- **Error**: `Query error: Database error: error returned from database: (code: 14) unable to open database file`
- **Deltas**: 10 deltas
- **Sample Delta**: `lines/delta/lines/receipt_lines/a1a9ba0796834485bc680a6d530d869f/`
- **Batch ID**: `c6f9b640-f702-4dcb-8c09-f0a1bf0aa804`
- **OpenAI Batch ID**: `batch_69190164463c8190ad9dbb4818f7db4a`
- **Delta Created**: 2025-11-16T17:12:38+00:00
- **File Status**: ✅ File exists in S3 (417,792 bytes), but ChromaDB cannot open it

### Chunk 36
- **Error**: `Error executing plan: Error sending backfill request to compactor: Error purging logs`
- **Deltas**: 10 deltas
- **Sample Delta**: `lines/delta/lines/receipt_lines/c87ad914cd0b44e494c3eaa938dad75f/`
- **Different Error Type**: This is a ChromaDB internal error, not a file corruption error

### Chunk 37
- **Error**: `Query error: Database error: error returned from database: (code: 14) unable to open database file`
- **Deltas**: 10 deltas
- **Sample Delta**: `lines/delta/lines/receipt_lines/21759aacda6c42a2b1ac483c5db93695/`
- **Same Error Type**: SQLite file locking/corruption issue

## Root Cause Analysis

### Evidence

1. **Delta Files Exist**: All delta files are present in S3 with non-zero file sizes
2. **ChromaDB Cannot Open**: ChromaDB fails to open the SQLite files with error code 14 ("unable to open database file")
3. **Timing**: Deltas were created at 17:12:38, just before the execution started processing them
4. **Pattern**: Multiple chunks failed with the same error, suggesting a systematic issue

### Likely Cause

The deltas were **uploaded while ChromaDB clients were still open**, causing SQLite files to be locked or corrupted during upload. This matches the issue described in `docs/DELTA_CORRUPTION_ERROR_ANALYSIS.md`.

### Why Line-Ingest Fails But Word-Ingest Works

Possible explanations:
1. **Different Delta Ages**: Line deltas in these chunks may have been created before the client closing fix was deployed
2. **Timing**: These specific batches may have been processed during a window when the fix wasn't fully deployed
3. **Volume**: Line ingestion processes more batches, increasing the chance of hitting corrupted deltas

## Diagnostic Script

Created `dev.test_delta_corruption.py` to:
1. Download deltas from S3
2. Test if ChromaDB can open them
3. Trace deltas back to their source batches through step function executions
4. Check file structure and integrity

### Usage Examples

```bash
# Test a specific delta
python dev.test_delta_corruption.py \
  --delta-key "lines/delta/lines/receipt_lines/a1a9ba0796834485bc680a6d530d869f/" \
  --bucket chromadb-dev-shared-buckets-vectors-c239843

# Test a specific chunk from an execution
python dev.test_delta_corruption.py \
  --execution-id a3ecfe1f-c2a8-441b-b5de-46213043a5fe \
  --chunk-index 35 \
  --delta-index 0 \
  --bucket chromadb-dev-shared-buckets-vectors-c239843

# Test all chunks from an execution (requires chromadb)
python dev.test_delta_corruption.py \
  --execution-id a3ecfe1f-c2a8-441b-b5de-46213043a5fe \
  --test-all-chunks \
  --bucket chromadb-dev-shared-buckets-vectors-c239843
```

## Failed Delta Keys

### Chunk 35 Deltas
```
lines/delta/lines/receipt_lines/a1a9ba0796834485bc680a6d530d869f/
lines/delta/lines/receipt_lines/bf738e8714de4199aedebe5720dd022d/
lines/delta/lines/receipt_lines/8526071f1bbb4b16a3ddd4ac89a10fe6/
lines/delta/lines/receipt_lines/ff62bf52421e4011877d3d845c2c204b/
lines/delta/lines/receipt_lines/8da9ff9a12754276a1bf65f5c9381795/
lines/delta/lines/receipt_lines/5eb02ed79ef4482db63bc158095977d1/
lines/delta/lines/receipt_lines/80f99e90c6d540baba8e0158bf51c3cb/
lines/delta/lines/receipt_lines/0fb9bca02eb44a9caa27dbbf903bdf4c/
lines/delta/lines/receipt_lines/2448a170fd324787976443aea409bf5b/
lines/delta/lines/receipt_lines/4e32da5cc9a94f859cb572ff07df575e/
```

### Chunk 36 Deltas
```
lines/delta/lines/receipt_lines/c87ad914cd0b44e494c3eaa938dad75f/
lines/delta/lines/receipt_lines/5b126aa68f3a4bd2b872ebd6ecf8dd4a/
lines/delta/lines/receipt_lines/13f90fa23d604d19b514d7807dff032e/
lines/delta/lines/receipt_lines/e12914ed0dfe47129e3c5a00b4fd59e4/
lines/delta/lines/receipt_lines/46a0139f6896473d8c530be6516c57d1/
lines/delta/lines/receipt_lines/e0385f09f740447095b52c12931d5937/
lines/delta/lines/receipt_lines/665de665abea4faca125ebb6c24cfdbd/
lines/delta/lines/receipt_lines/5ca548ae1088452b9a2afa31616f38a9/
lines/delta/lines/receipt_lines/3985de4d40d84551acb07bb399a6d27a/
lines/delta/lines/receipt_lines/b840af309b764977a57463809c28b6f1/
```

### Chunk 37 Deltas
```
lines/delta/lines/receipt_lines/21759aacda6c42a2b1ac483c5db93695/
lines/delta/lines/receipt_lines/05b1d3ecbcc6434197ca133653c70bc4/
lines/delta/lines/receipt_lines/72f2273df60b41a2b99d0061d565aca0/
lines/delta/lines/receipt_lines/0fa67b95b8e344ee94defd7984718083/
lines/delta/lines/receipt_lines/138f8087a9f546dda6790fc1b87e2136/
lines/delta/lines/receipt_lines/f45fa296a2f0484da3d378717b1b14da/
lines/delta/lines/receipt_lines/34caea1c1e5048be82eda049d01084c2/
lines/delta/lines/receipt_lines/756e7280519c4cf396ccff4cffdcd6e8/
lines/delta/lines/receipt_lines/4393232db2d946b8b7a73c898b968035/
lines/delta/lines/receipt_lines/49409e46754746768534c79d37289dc4/
```

## Next Steps

### Immediate Actions

1. **Verify Client Closing Fix**: Confirm that `persist_and_upload_delta()` in `chromadb_client.py` is calling `_close_client_for_upload()` before uploading
2. **Check Deployment Timeline**: Determine when the client closing fix was deployed vs when these deltas were created
3. **Re-process Failed Batches**: The batches that created these corrupted deltas should be re-processed:
   - Chunk 35: 10 batch IDs (starting with `c6f9b640-f702-4dcb-8c09-f0a1bf0aa804`)
   - Chunk 36: 10 batch IDs (starting with `cb2f12c0-680a-46b3-97a8-bc006b9489bb`)
   - Chunk 37: 10 batch IDs (starting with `21759aacda6c42a2b1ac483c5db93695`)

### Long-term Solutions

1. **Delta Validation**: Add validation in `download_and_merge_delta()` to check if delta can be opened before processing
2. **Retry Logic**: Implement retry logic that re-processes batches when delta corruption is detected
3. **Monitoring**: Add CloudWatch metrics/alarms for delta corruption errors
4. **Cleanup**: Periodically identify and re-process batches with corrupted deltas

## Related Documentation

- `docs/DELTA_CORRUPTION_ERROR_ANALYSIS.md` - Original analysis of delta corruption
- `docs/EXECUTION_DEBUG_2025_11_15.md` - Previous execution debugging
- `docs/LINE_VS_WORD_INGESTION_COMPARISON.md` - Comparison of line vs word workflows

## Lambda Logs

The errors were found in `/aws/lambda/embedding-vector-compact-lambda-dev`:
- Chunk 35 error at 2025-11-16T17:14:03.989Z
- Chunk 36 error at 2025-11-16T17:14:02.660Z
- Chunk 37 error at 2025-11-16T17:14:06.016Z

