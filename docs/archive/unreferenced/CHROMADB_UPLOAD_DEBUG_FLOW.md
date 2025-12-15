# ChromaDB Upload Process Debug Flow

## Date: November 14, 2025

## Problem Statement

ChromaDB snapshots in S3 and EFS are ending up corrupted (SQLite database disk image is malformed). This document traces each step in the upload → embedding → compaction → snapshot flow to identify where corruption occurs.

---

## Complete Flow Overview

```
1. Image Upload → OCR Processing
2. Embedding Creation → Delta Generation
3. Delta Upload to S3
4. Compaction Trigger (COMPACTION_RUN)
5. Compaction Process → Snapshot Creation
6. Snapshot Upload to S3/EFS
7. Snapshot Verification
```

---

## Step 1: Embedding Creation & Delta Generation

**Location**: `infra/upload_images/container_ocr/handler/embedding_processor.py`

**Function**: `EmbeddingProcessor._create_embeddings_and_deltas()`

### What Happens:

1. **Create temporary ChromaDB clients** (lines 400-405):
   ```python
   delta_lines_dir = tempfile.mkdtemp(prefix=f"lines_{run_id}_")
   delta_words_dir = tempfile.mkdtemp(prefix=f"words_{run_id}_")

   line_client = VectorClient.create_chromadb_client(
       persist_directory=delta_lines_dir,
       mode="delta",
       metadata_only=True
   )
   ```

2. **Generate embeddings** (lines 407-420):
   - Calls `upsert_embeddings()` which upserts vectors into local ChromaDB deltas
   - Embeddings include merchant context

3. **Upload delta to S3** (lines 422-450):
   ```python
   upload_bundled_delta_to_s3(
       local_delta_dir=delta_lines_dir,
       bucket=self.chromadb_bucket,
       delta_prefix=f"lines/delta/{run_id}/",
       metadata={...}
   )
   ```

### Potential Issues:

- ✅ **Delta creation**: Local ChromaDB delta should be valid
- ⚠️ **Delta upload**: Tarball creation/upload could corrupt files
- ⚠️ **Temp directory cleanup**: If Lambda times out, temp dirs might be incomplete

### Debug Points:

1. Verify delta ChromaDB is valid before upload
2. Verify tarball integrity after creation
3. Verify S3 upload completes successfully
4. Check Lambda timeout/cleanup behavior

---

## Step 2: Delta Upload to S3

**Location**: `receipt_label/receipt_label/utils/chroma_s3_helpers.py`

**Function**: `upload_bundled_delta_to_s3()`

### What Happens:

1. **Create tarball** (lines ~650-680):
   ```python
   tar_path = os.path.join(temp_dir, "delta.tar.gz")
   with tarfile.open(tar_path, "w:gz") as tar:
       tar.add(local_delta_dir, arcname=".")
   ```

2. **Upload to S3**:
   ```python
   s3.upload_file(tar_path, bucket, tar_key)
   ```

3. **Upload metadata**:
   - Creates `.snapshot_hash` file
   - Uploads metadata JSON

### Potential Issues:

- ⚠️ **Tarball creation**: If ChromaDB files are open/locked during tar creation
- ⚠️ **Compression**: Gzip compression could corrupt if files are being written
- ⚠️ **S3 upload**: Partial uploads if Lambda times out
- ⚠️ **File locking**: ChromaDB SQLite might be locked during tar creation

### Debug Points:

1. Verify ChromaDB client is closed before creating tarball
2. Verify tarball can be extracted and ChromaDB opened
3. Verify S3 upload completes (check ETag/checksum)
4. Test with large deltas (timeout scenarios)

---

## Step 3: Compaction Trigger

**Location**: `infra/upload_images/container_ocr/handler/embedding_processor.py`

**Function**: `EmbeddingProcessor._create_compaction_run()`

### What Happens:

1. **Create COMPACTION_RUN record** (lines ~460-490):
   ```python
   compaction_run = CompactionRun(
       run_id=run_id,
       image_id=image_id,
       receipt_id=receipt_id,
       state=CompactionState.PENDING,
       lines_delta_prefix=f"lines/delta/{run_id}/",
       words_delta_prefix=f"words/delta/{run_id}/",
   )
   self.dynamo.add_compaction_run(compaction_run)
   ```

2. **DynamoDB Stream triggers compaction**:
   - Stream processor detects COMPACTION_RUN
   - Sends SQS message to compaction queue

### Potential Issues:

- ✅ **COMPACTION_RUN creation**: Should be fine (DynamoDB write)
- ⚠️ **Stream processing**: Delay or missed messages

### Debug Points:

1. Verify COMPACTION_RUN is created
2. Verify DynamoDB stream processes it
3. Verify SQS message is sent

---

## Step 4: Compaction Process

**Location**: `infra/chromadb_compaction/lambdas/enhanced_compaction_handler.py`

**Function**: `process_stream_messages()`

### What Happens:

1. **Download snapshot from S3** (lines ~580-620):
   ```python
   download_result = download_snapshot_atomic(
       bucket=bucket,
       collection=collection.value,
       local_path=snapshot_dir,
       verify_integrity=True,
   )
   ```

2. **Download and extract delta** (lines ~625-650):
   ```python
   s3.download_file(bucket, tar_key, tar_path)
   with tarfile.open(tar_path, "r:gz") as tar:
       tar.extractall(delta_dir)
   ```

3. **Merge delta into snapshot** (lines ~655-700):
   ```python
   snapshot_client = ChromaDBClient(
       persist_directory=snapshot_dir, mode="snapshot"
   )
   delta_client = ChromaDBClient(
       persist_directory=delta_dir, mode="read"
   )

   # Get data from delta
   delta_data = delta_client.get_collection(collection_name).get(...)

   # Upsert into snapshot
   snapshot_client.get_collection(collection_name).upsert(...)
   ```

4. **Create new snapshot** (lines ~705-750):
   ```python
   # ChromaDB auto-persists, then upload to S3
   upload_snapshot_to_s3(snapshot_dir, new_version)
   ```

### Potential Issues:

- ⚠️ **Snapshot download**: Corrupted download from S3
- ⚠️ **Delta extraction**: Corrupted tarball extraction
- ⚠️ **ChromaDB merge**: SQLite corruption during upsert
- ⚠️ **Snapshot upload**: Incomplete upload if Lambda times out
- ⚠️ **Concurrent compaction**: Multiple compactions modifying same snapshot

### Debug Points:

1. Verify downloaded snapshot is valid before merge
2. Verify extracted delta is valid
3. Verify merge completes successfully
4. Verify new snapshot is valid before upload
5. Check for concurrent compaction conflicts
6. Verify snapshot upload completes fully

---

## Step 5: Snapshot Upload to S3

**Location**: `receipt_label/receipt_label/utils/chroma_s3_helpers.py`

**Function**: `upload_snapshot_atomic()`

### What Happens:

1. **Create timestamped snapshot directory** (lines ~1400-1450):
   ```python
   version_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
   versioned_prefix = f"{collection}/snapshot/timestamped/{version_id}/"
   ```

2. **Upload all files from snapshot directory**:
   ```python
   for root, dirs, files in os.walk(local_path):
       for file in files:
           relative_path = os.path.relpath(local_file, local_path)
           s3_key = f"{versioned_prefix}{relative_path}"
           s3.upload_file(local_file, bucket, s3_key)
   ```

3. **Calculate and upload hash** (for integrity verification):
   ```python
   hash_result = calculate_chromadb_hash(local_path)
   s3.put_object(
       Bucket=bucket,
       Key=f"{versioned_prefix}.snapshot_hash",
       Body=hash_content
   )
   ```

4. **Atomically update pointer** (with lock validation):
   ```python
   # Under lock, verify pointer hasn't changed
   # Then update pointer atomically
   s3.put_object(
       Bucket=bucket,
       Key=f"{collection}/snapshot/latest-pointer.txt",
       Body=version_id
   )
   ```

### Potential Issues:

- ⚠️ **File locking**: ChromaDB SQLite might be open during upload
- ⚠️ **Partial uploads**: If Lambda times out mid-upload, only some files uploaded
- ⚠️ **Concurrent writes**: ChromaDB writing while we're uploading
- ⚠️ **Atomic pointer update**: Race condition if multiple compactions finish
- ⚠️ **Hash calculation**: If files change during hash calculation
- ⚠️ **S3 eventual consistency**: Reading snapshot while it's being written

### Debug Points:

1. Verify ChromaDB client is closed before upload
2. Verify all files upload successfully (check file count)
3. Verify hash matches after upload
4. Verify pointer update is atomic (check lock is held)
5. Check for concurrent uploads (lock collisions)
6. Verify S3 upload completes fully (check ETags)

---

## Step 6: EFS Snapshot Sync (if enabled)

**Location**: `infra/chromadb_compaction/lambdas/compaction/efs_snapshot_manager.py`

**Function**: `EFSSnapshotManager.ensure_snapshot_on_efs()`

### What Happens:

1. **Download from S3 to EFS** (if not present):
   ```python
   # Download snapshot files to EFS
   s3.download_file(bucket, key, efs_path)
   ```

2. **Update EFS pointer**:
   ```python
   with open(version_file, 'w') as f:
       f.write(version)
   ```

### Potential Issues:

- ⚠️ **EFS write conflicts**: Multiple Lambdas writing to same EFS path
- ⚠️ **Partial downloads**: If download fails mid-way
- ⚠️ **File permissions**: EFS mount permissions

### Debug Points:

1. Verify EFS mount is working
2. Verify download completes fully
3. Check for concurrent EFS writes

---

## Debugging Checklist

### For Each Step:

- [ ] **Step 1 - Delta Creation**: Verify local ChromaDB delta is valid SQLite
- [ ] **Step 2 - Delta Upload**: Verify tarball integrity, S3 upload completion
- [ ] **Step 3 - Compaction Trigger**: Verify COMPACTION_RUN created, stream processed
- [ ] **Step 4 - Compaction**: Verify snapshot download, delta extraction, merge, new snapshot validity
- [ ] **Step 5 - Snapshot Upload**: Verify all files uploaded, pointer updated atomically
- [ ] **Step 6 - EFS Sync**: Verify EFS download completes, no conflicts

### Common Issues to Check:

1. **SQLite file locking**: ChromaDB SQLite files must be closed before tar/upload
2. **Lambda timeouts**: Partial operations if Lambda times out
3. **Concurrent operations**: Multiple compactions modifying same snapshot
4. **S3 eventual consistency**: Reading snapshot while it's being written
5. **EFS mount issues**: EFS not mounted or permissions wrong
6. **Memory/disk limits**: Ephemeral storage full, causing corruption

---

## Critical Issue: SQLite File Locking

**Most Likely Cause of Corruption**: ChromaDB SQLite database files are being uploaded while they're still open/locked by ChromaDB.

### The Problem:

1. **During Delta Creation** (Step 1):
   - ChromaDB client creates SQLite files in temp directory
   - Files remain open/locked while client is active
   - If tarball is created while files are locked → corruption

2. **During Compaction** (Step 4):
   - ChromaDB merges deltas into snapshot
   - SQLite files are open during merge
   - If snapshot is uploaded while files are open → corruption

3. **During Snapshot Upload** (Step 5):
   - `upload_snapshot_atomic()` calls `upload_snapshot_with_hash()`
   - This walks the directory and uploads files
   - **If ChromaDB client is still open, SQLite files are locked → corruption**

### Solution:

**Ensure ChromaDB clients are properly closed before any file operations:**

1. **In `_create_embeddings_and_deltas()`**:
   ```python
   # After upsert_embeddings()
   line_client = None  # Explicitly close
   word_client = None
   # THEN upload to S3
   ```

2. **In compaction handler**:
   ```python
   # After merge operations
   snapshot_client = None  # Close client
   delta_client = None
   # THEN upload snapshot
   ```

3. **In `upload_snapshot_atomic()`**:
   - Verify ChromaDB client is closed before calling
   - Add explicit check for SQLite file locks

---

## Next Steps for Debugging

### Immediate Actions:

1. **Add ChromaDB client cleanup**:
   - Ensure all ChromaDB clients are explicitly closed before file operations
   - Add context managers or try/finally blocks

2. **Add SQLite lock detection**:
   - Check if SQLite files are locked before tar/upload
   - Add retry logic if files are locked

3. **Add integrity verification**:
   - Verify SQLite database integrity before upload
   - Use `sqlite3` to check database integrity

4. **Add logging**:
   - Log when ChromaDB clients are created/closed
   - Log file lock status before operations
   - Log SQLite integrity checks

### Debug Scripts to Create:

1. **Verify delta integrity** (after Step 2):
   ```python
   # Download delta from S3, extract, verify SQLite
   ```

2. **Verify snapshot integrity** (after Step 5):
   ```python
   # Download snapshot from S3, verify SQLite
   ```

3. **Check for file locks**:
   ```python
   # Check if SQLite files are locked before operations
   ```

### CloudWatch Logs to Check:

1. **Upload Lambda logs**: Look for timeout errors, partial uploads
2. **Compaction Lambda logs**: Look for merge errors, upload failures
3. **Lambda metrics**: Check for timeouts, memory issues, disk full

---

## Files to Review/Modify

1. `infra/upload_images/container_ocr/handler/embedding_processor.py`:
   - Line 422-455: Ensure clients are closed before upload

2. `infra/chromadb_compaction/lambdas/enhanced_compaction_handler.py`:
   - Line 639-700: Ensure clients are closed before upload
   - Line 753-775: Verify client is closed before `upload_snapshot_atomic()`

3. `receipt_label/receipt_label/utils/chroma_s3_helpers.py`:
   - `upload_snapshot_with_hash()`: Add SQLite lock check
   - `upload_bundled_delta_to_s3()`: Add SQLite lock check

4. `receipt_label/receipt_label/vector_store/client/chromadb_client.py`:
   - Ensure proper cleanup in `__del__` or context manager

