# Enhanced Compactor Review for Split Receipt Flow

## Overview

This document reviews the enhanced compactor to ensure our split receipt approach doesn't break anything.

## Current Split Receipt Flow

1. **Add Receipt, ReceiptLine, ReceiptWord to DynamoDB**
2. **Export NDJSON and queue** (NDJSON worker creates CompactionRun)
3. **Wait for CompactionRun to complete** (embeddings merged into ChromaDB)
4. **Add ReceiptWordLabel entities** (labels update ChromaDB metadata via streams)

## Enhanced Compactor Processing Order

Looking at `enhanced_compaction_handler.py` lines 658-698:

```python
# Merge deltas first
if compaction_run_msgs:
    merged, per_run_results = merge_compaction_deltas(...)

# Apply metadata updates
if metadata_msgs:
    md_results = apply_metadata_updates_in_memory(...)

# Apply label updates
if label_msgs:
    lb_results = apply_label_updates_in_memory(...)
```

**Processing order:**
1. CompactionRun (merge embeddings)
2. Metadata updates
3. Label updates

## CompactionRun Processing Requirements

From `compaction_run.py` lines 55-270:

**What CompactionRun processing needs:**
- ✅ CompactionRun entity in DynamoDB (for run_id, image_id, receipt_id, delta_prefix)
- ✅ Delta tarball in S3 (contains embeddings)
- ✅ Snapshot in S3/EFS (target for merging)
- ❌ **Does NOT need ReceiptLine/ReceiptWord in DynamoDB**

**What it does:**
1. Reads CompactionRun from DynamoDB (line 68) - only if delta_prefix not in message
2. Downloads snapshot from S3/EFS
3. Downloads delta tarball from S3
4. Merges embeddings into snapshot
5. Uploads updated snapshot
6. Marks CompactionRun as completed

**Conclusion:** ✅ CompactionRun processing is independent of ReceiptLine/ReceiptWord existence in DynamoDB.

## Label Update Processing Requirements

From `compaction/operations.py` lines 422-607:

**What label updates need:**
- ✅ ReceiptWordLabel entity (from DynamoDB stream)
- ✅ Embedding must exist in ChromaDB (checked at line 456)
- ✅ Optionally reads ReceiptWordLabel from DynamoDB if snapshot missing (line 471-483)

**What it does:**
1. Parses ChromaDB ID from label entity
2. **Checks if embedding exists** (line 456):
   ```python
   result = collection.get(ids=[chromadb_id], include=["metadatas"])
   if not result["ids"]:
       logger.warning("Word embedding not found", chromadb_id=chromadb_id)
       return 0  # ← Fails silently if embedding doesn't exist
   ```
3. Updates metadata with label information
4. Updates ChromaDB record

**Conclusion:** ✅ Label updates are safe - they fail silently if embedding doesn't exist yet.

## Potential Issues

### Issue 1: Labels Added Before Embeddings Exist

**Scenario:**
1. Add Receipt, ReceiptLine, ReceiptWord to DynamoDB
2. Export NDJSON and queue
3. **Add labels immediately** (before compaction completes)
4. Compaction processes later

**What happens:**
- Label update message arrives at compactor
- Compactor checks if embedding exists (line 456)
- Embedding doesn't exist yet → returns 0 (fails silently)
- Compaction completes later
- **Labels are NOT updated in ChromaDB** ❌

**Solution:** ✅ Our approach waits for compaction to complete before adding labels.

### Issue 2: CompactionRun Created Before ReceiptLine/ReceiptWord

**Scenario:**
1. Create CompactionRun
2. Add ReceiptLine/ReceiptWord later

**What happens:**
- CompactionRun processing doesn't read ReceiptLine/ReceiptWord from DynamoDB
- It only needs the delta tarball in S3
- ✅ **No issue** - CompactionRun is independent

**Solution:** ✅ Our approach adds ReceiptLine/ReceiptWord first, then creates CompactionRun (via NDJSON worker).

### Issue 3: Multiple CompactionRuns for Same Receipt

**Scenario:**
- Multiple CompactionRuns created for same receipt
- Labels added after first compaction completes
- Second compaction runs later

**What happens:**
- Each CompactionRun is processed independently
- Labels update metadata on existing embeddings
- ✅ **No issue** - labels persist across compactions

**Solution:** ✅ Our approach creates one CompactionRun per receipt, waits for it to complete.

## Recommended Order (Verified Safe)

### ✅ Correct Order:

```
1. Add Receipt, ReceiptLine, ReceiptWord to DynamoDB
   └─ Needed for label processing later

2. Export NDJSON and queue
   └─ NDJSON worker creates CompactionRun

3. Wait for CompactionRun to complete
   └─ Ensures embeddings exist in ChromaDB

4. Add ReceiptWordLabel entities
   └─ Labels will update ChromaDB metadata via streams
```

### ❌ Incorrect Order (Would Break):

```
1. Add Receipt, ReceiptLine, ReceiptWord
2. Add labels immediately ← WRONG: Embeddings don't exist yet
3. Export NDJSON and queue
4. Compaction completes later
   └─ Labels were skipped (embedding didn't exist when label update processed)
```

## Compactor Dependencies

### CompactionRun Processing:
- **Needs:** CompactionRun entity, delta tarball in S3, snapshot
- **Doesn't need:** ReceiptLine, ReceiptWord, ReceiptWordLabel
- ✅ **Independent** - can run without ReceiptLine/ReceiptWord in DynamoDB

### Metadata Update Processing:
- **Needs:** ReceiptMetadata entity, ReceiptLine/ReceiptWord (to get all word IDs)
- **Used for:** Updating merchant info across all embeddings
- ⚠️ **Note:** Not relevant for our split receipt flow (we don't update ReceiptMetadata)

### Label Update Processing:
- **Needs:** ReceiptWordLabel entity, embedding in ChromaDB
- **Doesn't need:** ReceiptLine, ReceiptWord (only for reconstruction if snapshot missing)
- ✅ **Safe** - fails silently if embedding doesn't exist

## Conclusion

✅ **Our approach is safe and correct:**

1. **ReceiptLine/ReceiptWord added first** - Available for label processing
2. **CompactionRun created via NDJSON** - Independent of ReceiptLine/ReceiptWord
3. **Wait for compaction** - Ensures embeddings exist before labels
4. **Labels added last** - Will successfully update ChromaDB metadata

**No breaking changes to enhanced compactor** - it handles all cases gracefully:
- CompactionRun processing is independent
- Label updates fail silently if embedding doesn't exist (we avoid this by waiting)
- All operations are idempotent and safe

## Edge Cases Handled

1. **Label update before embedding exists:**
   - Compactor returns 0 (fails silently)
   - ✅ We avoid this by waiting

2. **Multiple CompactionRuns:**
   - Each processed independently
   - ✅ We create one per receipt

3. **CompactionRun without ReceiptLine/ReceiptWord:**
   - Compactor doesn't need them
   - ✅ We add them first anyway (for labels)

4. **Label update after compaction:**
   - Compactor updates metadata successfully
   - ✅ This is our intended flow

