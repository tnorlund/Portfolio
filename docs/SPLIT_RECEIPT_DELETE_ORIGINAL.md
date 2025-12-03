# Split Receipt - Delete Original Receipt Feature

## Overview

The `split_receipt.py` script now supports automatically deleting the original receipt and its ChromaDB embeddings after the split completes. This ensures a clean transition from the incorrectly clustered receipt to the correctly clustered receipts.

## New Feature: `--delete-original` Flag

When `--delete-original` is provided, the script will:

1. ✅ Split the receipt into correctly clustered receipts
2. ✅ Wait for compaction to complete for all new receipts
3. ✅ Delete embeddings from ChromaDB (BEFORE deleting from DynamoDB)
4. ✅ Delete DynamoDB records in the correct order
5. ✅ Ensure no orphaned embeddings remain

## Order of Operations

### 1. Split and Create New Receipts
- Creates new Receipt, ReceiptLine, ReceiptWord, ReceiptLetter records
- Uploads CDN images
- Exports NDJSON to S3
- Creates embeddings and CompactionRun records

### 2. Wait for Compaction (First Time)
- Waits for compaction to complete for all new receipts
- This ensures embeddings exist before adding labels

### 3. Add Labels
- Adds ReceiptWordLabel records after compaction completes

### 4. Wait for Compaction (Second Time - Only if `--delete-original`)
- Waits again to ensure ALL compaction is complete
- This ensures new receipts are fully embedded before deleting the original

### 5. Delete Original Receipt (Only if `--delete-original`)

#### 5a. Delete ChromaDB Embeddings (FIRST)
- **Why first?** We need the receipt data (lines, words) to construct ChromaDB IDs
- Downloads main snapshot collections (lines and words)
- Constructs ChromaDB IDs from receipt data:
  - Lines: `IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}`
  - Words: `IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}`
- Deletes embeddings from both collections
- Uploads updated snapshots back to S3

#### 5b. Delete DynamoDB Records (SECOND)
Deletes in reverse order of creation:

1. **ReceiptWordLabel** (references words)
2. **ReceiptWord** (references lines)
3. **ReceiptLine** (references receipt)
4. **ReceiptLetter** (references words, optional)
5. **ReceiptMetadata** (references receipt, optional)
6. **CompactionRun** (references receipt, optional)
7. **Receipt** (top-level entity)

## Safety Features

### Compaction Check
- Only deletes if ALL compaction runs completed successfully
- If compaction fails or times out, original receipt is NOT deleted
- User can manually delete later after compaction completes

### Error Handling
- If ChromaDB deletion fails, continues with DynamoDB deletion
- Individual deletion errors are logged but don't stop the process
- All errors are caught and logged for debugging

### Dry Run Support
- `--dry-run` mode does NOT delete anything
- Original receipt is always preserved in dry-run mode

## Usage

```bash
# Split receipt and delete original (waits for compaction)
python scripts/split_receipt.py \
    --image-id 13da1048-3888-429f-b2aa-b3e15341da5e \
    --original-receipt-id 1 \
    --delete-original

# Split receipt but keep original (for testing)
python scripts/split_receipt.py \
    --image-id 13da1048-429f-b2aa-b3e15341da5e \
    --original-receipt-id 1
```

## ChromaDB Embedding Deletion

The script deletes embeddings directly from the main ChromaDB snapshot collections:

1. **Downloads** the latest snapshot for each collection (lines, words)
2. **Opens** the collection in write mode
3. **Deletes** embeddings by ID using `collection.delete(ids=[...])`
4. **Uploads** the updated snapshot back to S3

This ensures embeddings are removed from ChromaDB before the DynamoDB records are deleted, preventing orphaned embeddings.

## Why Delete ChromaDB Embeddings First?

The compactor does NOT automatically delete embeddings when ReceiptLine or ReceiptWord records are deleted from DynamoDB. The compactor only processes:
- `RECEIPT_METADATA` - Updates/removes merchant metadata
- `RECEIPT_WORD_LABEL` - Updates/removes label metadata

It explicitly ignores:
- `RECEIPT_LINE` - No deletion handler
- `RECEIPT_WORD` - No deletion handler
- `RECEIPT` - No deletion handler

Therefore, we must manually delete embeddings from ChromaDB before deleting from DynamoDB to prevent orphaned embeddings.

## Rollback

If something goes wrong, you can rollback by:
1. Using the local JSON files saved in `--output-dir`
2. Re-creating the original receipt from the saved data
3. The new receipts can be deleted using `rollback_split_receipt.py`

## Summary

✅ **Splits** incorrectly clustered receipt into correctly clustered receipts
✅ **Waits** for compaction to complete
✅ **Deletes** ChromaDB embeddings (prevents orphaned embeddings)
✅ **Deletes** DynamoDB records in correct order
✅ **Safe** - only deletes if compaction completes successfully

