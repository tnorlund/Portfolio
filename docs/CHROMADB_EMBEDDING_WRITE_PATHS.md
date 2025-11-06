# ChromaDB Embedding Write Paths Review

## Overview

This document reviews all places where line and word embeddings are written to ChromaDB, S3, and EFS to verify the pipeline is working correctly.

## Architecture Flow

```
1. OCR Processing → Creates lines/words in DynamoDB
2. Embedding Generation → Creates ChromaDB deltas locally
3. Delta Upload → Uploads deltas to S3
4. Compaction Trigger → Creates COMPACTION_RUN in DynamoDB
5. Stream Processor → Reads COMPACTION_RUN from DynamoDB stream
6. Compaction → Merges deltas into snapshot
7. Snapshot Upload → Uploads snapshot to S3 (and optionally EFS)
```

## Components Writing Embeddings

### 1. Embedding Producers (Write Deltas to S3)

#### 1.1 `infra/upload_images/container_ocr/handler/embedding_processor.py`

**Location**: `EmbeddingProcessor._create_embeddings_and_deltas()`

**Function**:
- Creates embeddings for lines and words
- Uploads deltas to S3 using `upload_bundled_delta_to_s3()`
- Creates `COMPACTION_RUN` record in DynamoDB

**S3 Paths**:
- Lines: `lines/delta/{run_id}/delta.tar.gz`
- Words: `words/delta/{run_id}/delta.tar.gz`

**Key Code**:
```python
# Create local ChromaDB deltas
delta_lines_dir = os.path.join(tempfile.gettempdir(), f"lines_{run_id}")
delta_words_dir = os.path.join(tempfile.gettempdir(), f"words_{run_id}")

line_client = VectorClient.create_chromadb_client(
    persist_directory=delta_lines_dir,
    mode="delta",
    metadata_only=True
)

# Upsert embeddings
upsert_embeddings(
    line_client=line_client,
    word_client=word_client,
    line_embed_fn=embed_lines_realtime,
    word_embed_fn=embed_words_realtime,
    ctx={"lines": lines, "words": words},
    merchant_name=merchant_name,
)

# Upload to S3
upload_bundled_delta_to_s3(
    local_delta_dir=delta_lines_dir,
    bucket=self.chromadb_bucket,
    delta_prefix=f"lines/delta/{run_id}/",
    metadata={...}
)
```

**Infrastructure**: Container-based Lambda (`infra/upload_images/infra.py`)

#### 1.2 `infra/upload_images/container/handler.py`

**Location**: `_process_single()`

**Function**: Similar to `embedding_processor.py`, creates embeddings and uploads deltas

**S3 Paths**: Same as above

**Infrastructure**: Container-based Lambda

#### 1.3 `infra/upload_images/embed_from_ndjson.py`

**Location**: `_process_single()`

**Function**: Legacy embedding production path

**S3 Paths**: Same as above

**Infrastructure**: Lambda function

### 2. Compaction Processor (Merges Deltas into Snapshots)

#### 2.1 `infra/chromadb_compaction/lambdas/enhanced_compaction_handler.py`

**Location**: `process_stream_messages()`

**Function**:
- Reads `COMPACTION_RUN` messages from DynamoDB stream
- Downloads current snapshot from S3
- Downloads deltas from S3
- Merges deltas into snapshot
- Uploads updated snapshot back to S3
- Optionally syncs to EFS (if `CHROMADB_STORAGE_MODE` is set)

**Storage Modes**:
- **S3-only**: Downloads snapshot from S3, merges delta, uploads back to S3
- **EFS + S3 hybrid**: Downloads snapshot from S3, copies to EFS, merges delta, uploads to S3, syncs EFS

**S3 Snapshot Paths**:
- Lines: `lines/snapshot/timestamped/{version}/...`
- Words: `words/snapshot/timestamped/{version}/...`
- Pointer: `lines/snapshot/latest-pointer.txt` (points to latest version)

**EFS Paths** (if enabled):
- Lines: `{CHROMA_ROOT}/snapshots/lines/{version}/...`
- Words: `{CHROMA_ROOT}/snapshots/words/{version}/...`

**Key Code**:
```python
# Download snapshot (atomic)
download_result = download_snapshot_atomic(
    bucket=bucket,
    collection=collection.value,
    local_path=snapshot_dir,
    verify_integrity=True,
)

# Download and extract delta
s3.download_file(bucket, tar_key, tar_path)
tar.extractall(delta_dir)

# Merge delta into snapshot
snapshot_client = ChromaDBClient(
    persist_directory=snapshot_dir, mode="snapshot"
)
delta_client = ChromaDBClient(
    persist_directory=delta_dir, mode="read"
)

data = delta_client.get_collection(collection_name).get(...)
snapshot_client.upsert_vectors(
    collection_name=collection_name,
    ids=ids,
    embeddings=embeddings,
    documents=documents,
    metadatas=metadatas,
)

# Upload snapshot back to S3 (atomic pointer update)
upload_snapshot_atomic(
    bucket=bucket,
    collection=collection.value,
    local_snapshot_path=snapshot_dir,
)
```

**Infrastructure**: Container-based Lambda (`infra/chromadb_compaction/infrastructure.py`)

#### 2.2 `infra/chromadb_compaction/lambdas/compaction/compaction_run.py`

**Location**: `process_compaction_runs()`

**Function**: Processes `COMPACTION_RUN` entities (S3-only mode)

**S3 Paths**: Same as above

**Infrastructure**: Part of compaction Lambda

### 3. Utility Functions

#### 3.1 `receipt_label/receipt_label/utils/chroma_s3_helpers.py`

**Functions**:
- `upload_bundled_delta_to_s3()`: Creates tar.gz and uploads delta to S3
- `upload_delta_to_s3()`: Uploads delta directory files individually
- `download_snapshot_atomic()`: Downloads snapshot using atomic pointer
- `upload_snapshot_atomic()`: Uploads snapshot and updates atomic pointer
- `bundle_directory_to_tar_gz()`: Creates tar.gz from directory

**Delta Upload Format**:
```
s3://bucket/{collection}/delta/{run_id}/delta.tar.gz
```

**Snapshot Format**:
```
s3://bucket/{collection}/snapshot/timestamped/{version}/
  ├── chroma.sqlite3
  ├── ... (other ChromaDB files)
```

**Pointer File**:
```
s3://bucket/{collection}/snapshot/latest-pointer.txt
  (contains: {version})
```

## Verification Checklist

### ✅ Delta Creation and Upload

- [ ] Are deltas being created locally?
  - Check: Lambda logs should show "Creating embeddings..."
  - Check: Lambda logs should show "Uploading line delta to s3://..."
  
- [ ] Are deltas being uploaded to S3?
  - Check: S3 bucket should have `lines/delta/*/delta.tar.gz` files
  - Check: Lambda logs should show upload success
  
- [ ] Are COMPACTION_RUN records being created?
  - Check: DynamoDB `COMPACTION_RUN` table should have records
  - Check: Records should have `lines_delta_prefix` and `words_delta_prefix`

### ✅ Compaction Processing

- [ ] Is the compaction Lambda being triggered?
  - Check: DynamoDB stream should show events
  - Check: Lambda logs should show "Processing stream messages..."
  
- [ ] Are snapshots being downloaded?
  - Check: Lambda logs should show "Downloading snapshot..."
  - Check: Lambda logs should show snapshot version
  
- [ ] Are deltas being merged?
  - Check: Lambda logs should show "Merging {N} vectors..."
  - Check: Lambda logs should show merge success
  
- [ ] Are snapshots being uploaded?
  - Check: Lambda logs should show "Uploading snapshot..."
  - Check: S3 should have new snapshot version in `snapshot/timestamped/`
  - Check: Pointer file should be updated

### ✅ Snapshot Contents

- [ ] Do snapshots contain data?
  - Check: Download snapshot and query ChromaDB
  - Check: Collection count should be > 0
  - Check: `dev.test_chromadb_line_query.py` script
  
- [ ] Are IDs formatted correctly?
  - Format: `IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}`
  - Format: `IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}`

## Common Issues

### Issue 1: Empty Snapshots

**Symptoms**:
- Snapshot download succeeds but collection has 0 records
- `dev.test_chromadb_line_query.py` shows 0 total IDs

**Possible Causes**:
1. **Deltas not being compacted**: Check if compaction Lambda is running
2. **Compaction failing silently**: Check Lambda logs for errors
3. **Pointer pointing to empty snapshot**: Check snapshot version history
4. **Data in deltas but not merged**: Check compaction Lambda logs for merge operations

**Debug Steps**:
```bash
# Check deltas in S3
aws s3 ls s3://chromadb-dev-shared-buckets-vectors-c239843/lines/delta/ --recursive

# Check snapshots
aws s3 ls s3://chromadb-dev-shared-buckets-vectors-c239843/lines/snapshot/timestamped/ --recursive

# Check pointer
aws s3 cp s3://chromadb-dev-shared-buckets-vectors-c239843/lines/snapshot/latest-pointer.txt -

# Check COMPACTION_RUN records
aws dynamodb scan --table-name ReceiptsTable-... --filter-expression "begins_with(GSI1PK, :pk)" --expression-attribute-values '{":pk":{"S":"COMPACTION_RUN#"}}'
```

### Issue 2: Deltas Not Being Created

**Symptoms**:
- No delta files in S3
- No COMPACTION_RUN records in DynamoDB

**Possible Causes**:
1. **Embedding Lambda not being triggered**: Check Lambda invocations
2. **Embedding Lambda failing**: Check Lambda logs
3. **Merchant resolution failing**: Check Lambda logs for merchant resolution errors
4. **Missing environment variables**: Check Lambda configuration

**Debug Steps**:
```bash
# Check Lambda logs
aws logs tail /aws/lambda/upload-images-dev-process-ocr --follow

# Check if deltas are being uploaded
aws s3 ls s3://chromadb-dev-shared-buckets-vectors-c239843/lines/delta/ --recursive | tail -20
```

### Issue 3: Compaction Not Triggering

**Symptoms**:
- Deltas exist in S3 but snapshots aren't updating
- COMPACTION_RUN records exist but not being processed

**Possible Causes**:
1. **DynamoDB stream not enabled**: Check stream configuration
2. **Stream processor Lambda not running**: Check Lambda invocations
3. **Lock contention**: Check Lambda logs for lock errors
4. **Compaction Lambda failing**: Check Lambda logs

**Debug Steps**:
```bash
# Check DynamoDB stream
aws dynamodb describe-stream --stream-arn <stream-arn>

# Check stream processor Lambda
aws logs tail /aws/lambda/chromadb-dev-stream-processor --follow

# Check compaction Lambda
aws logs tail /aws/lambda/chromadb-dev-docker-dev --follow
```

## Infrastructure Files

### Line Embeddings Infrastructure

- **Producer**: `infra/upload_images/infra.py` (Container Lambda)
- **Compaction**: `infra/chromadb_compaction/infrastructure.py` (Container Lambda)
- **Client**: `receipt_label/receipt_label/vector_store/client/chromadb_client.py`

### Word Embeddings Infrastructure

- **Producer**: Same as lines (shared Lambda)
- **Compaction**: Same as lines (shared Lambda, processes both collections)
- **Client**: Same as lines (shared client)

## Environment Variables

### Embedding Producer Lambdas

- `CHROMADB_BUCKET`: S3 bucket name for ChromaDB storage
- `DYNAMODB_TABLE_NAME`: DynamoDB table name
- `OPENAI_API_KEY`: OpenAI API key for embeddings

### Compaction Lambda

- `CHROMADB_BUCKET`: S3 bucket name for ChromaDB storage
- `DYNAMODB_TABLE_NAME`: DynamoDB table name
- `CHROMADB_STORAGE_MODE`: `"s3"`, `"efs"`, or `"auto"` (default: `"auto"`)
- `CHROMA_ROOT`: EFS mount path (if using EFS)

## Next Steps

1. **Verify Delta Creation**: Check if deltas are being created and uploaded to S3
2. **Verify Compaction**: Check if compaction Lambda is processing deltas
3. **Verify Snapshots**: Check if snapshots contain data after compaction
4. **Check Logs**: Review Lambda logs for any errors or warnings
5. **Test Query**: Use `dev.test_chromadb_line_query.py` to verify data is queryable

## Related Files

- `dev.test_chromadb_line_query.py` - Test script to verify ChromaDB queries
- `docs/CHROMADB_LINE_QUERY_TESTING.md` - Testing documentation
- `infra/chromadb_compaction/README.md` - Compaction documentation
- `receipt_label/receipt_label/utils/chroma_s3_helpers.py` - S3 utilities

