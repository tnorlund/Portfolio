# Split Receipt Script - Current State

## ✅ What's Complete

### 1. Main Split Script (`scripts/split_receipt.py`)
- ✅ Loads original receipt data from DynamoDB
- ✅ Re-clusters using two-phase approach (angle-based splitting + smart merging)
- ✅ Creates new receipt records with recalculated coordinates
- ✅ Migrates ReceiptWordLabels to new receipts
- ✅ Saves records locally first (for rollback)
- ✅ Saves to DynamoDB (receipts, lines, words)
- ✅ **Creates embeddings directly** (realtime, no NDJSON queue)
- ✅ **Creates CompactionRun directly** (triggers compaction via streams)
- ✅ Waits for compaction to complete
- ✅ Adds labels after embeddings exist

### 2. Rollback Script (`scripts/rollback_split_receipt.py`)
- ✅ Deletes in correct order (reverse of creation)
- ✅ Handles all entity types (labels, words, lines, letters, metadata, compaction runs, receipts)
- ✅ Supports dry-run mode
- ⚠️ Note: ChromaDB embeddings remain orphaned (compactor doesn't delete them)

### 3. Order of Operations (Verified Correct)

```
1. Create Receipt, ReceiptLine, ReceiptWord in DynamoDB ✅
   └─ Available for embedding and label processing

2. Create embeddings directly (realtime) ✅
   ├─ embed_lines_realtime()
   ├─ embed_words_realtime()
   ├─ Upload deltas to S3
   └─ Create CompactionRun in DynamoDB

3. Wait for CompactionRun to complete ✅
   └─ Polls DynamoDB for both lines_state and words_state = COMPLETED

4. Add ReceiptWordLabel entities ✅
   └─ Labels update ChromaDB metadata via streams
```

### 4. Pulumi Configuration
- ✅ `artifacts_bucket_name` exported
- ✅ `chromadb_bucket_name` exported (as `embedding_chromadb_bucket_name`)
- ✅ Script loads from Pulumi automatically

## 🔄 Current Implementation: Direct Embedding (Not NDJSON)

**We changed from NDJSON queue approach to direct embedding** (like `combine_receipts`):

### Why the Change?
- **Simpler**: No queue dependency
- **More control**: We know exactly when embeddings are created
- **Consistent**: Matches `combine_receipts` approach
- **Faster**: No queue processing delay

### How It Works:
1. Script creates embeddings in realtime using `embed_lines_realtime()` and `embed_words_realtime()`
2. Uploads deltas to S3
3. Creates CompactionRun directly in DynamoDB
4. CompactionRun triggers compaction via DynamoDB streams
5. Wait for compaction to complete
6. Add labels

## 📋 Deletion Order (For Rollback)

**Correct order** (reverse of creation):
1. ReceiptWordLabel (references words)
2. ReceiptWord (references lines)
3. ReceiptLine (references receipt)
4. ReceiptLetter (optional)
5. ReceiptMetadata (optional)
6. CompactionRun (optional)
7. Receipt (top-level)

**Note**: ChromaDB embeddings remain orphaned when ReceiptWord/ReceiptLine are deleted (compactor doesn't handle these DELETE events).

## 🧪 Testing Status

- ✅ Dry run tested successfully
- ✅ Re-clustering works (2 clusters found for test image)
- ✅ Local save works
- ⏳ **Not yet tested**: Full run with DynamoDB writes and embedding creation

## 🚀 Ready to Run

The script is ready to run for real:

```bash
# Full run (with embedding)
python scripts/split_receipt.py \
    --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
    --original-receipt-id 1

# Or skip embedding if needed
python scripts/split_receipt.py \
    --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
    --original-receipt-id 1 \
    --skip-embedding
```

## 📝 What's Not Included (But Available)

- Clustering improvements (`cluster.py`, `scan.py`) - still modified but not committed
- Development scripts (download, recluster, merge) - untracked
- Local test data - untracked

## ⚠️ Known Limitations

1. **Orphaned Embeddings**: When rolling back, ChromaDB embeddings remain (no automatic deletion)
2. **No NDJSON Queue**: We use direct embedding instead (simpler, but different from upload workflow)
3. **Manual ChromaDB Cleanup**: If needed, must manually delete embeddings using ChromaDB IDs

## 🎯 Next Steps

1. **Test full run** on one of the problematic images
2. **Verify in DynamoDB** that new receipts are created correctly
3. **Verify compaction** completes successfully
4. **Verify labels** are added and update ChromaDB
5. **Test rollback** if needed

