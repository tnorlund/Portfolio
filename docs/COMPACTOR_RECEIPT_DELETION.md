# Enhanced Compactor - Receipt Deletion Support

## Overview

The enhanced compactor now automatically deletes all embeddings for a receipt when the Receipt entity is deleted from DynamoDB. This prevents orphaned embeddings and ensures ChromaDB stays in sync with DynamoDB.

## Implementation

### 1. Entity Detection

**File**: `infra/chromadb_compaction/lambdas/processor/parsers.py`

- Updated `detect_entity_type()` to recognize RECEIPT entities
- SK pattern: `RECEIPT#{receipt_id:05d}` (exactly 2 parts)
- Added `item_to_receipt` import and parsing support

### 2. Message Building

**File**: `infra/chromadb_compaction/lambdas/processor/message_builder.py`

- Updated `_extract_entity_data()` to handle RECEIPT entity type
- RECEIPT deletions target both collections (LINES and WORDS)
- Updated `categorize_stream_messages()` to return receipt_deletions

### 3. Deletion Operation

**File**: `infra/chromadb_compaction/lambdas/compaction/operations.py`

- Added `delete_receipt_embeddings()` function
- Queries DynamoDB for all ReceiptLine and ReceiptWord entities for the receipt
- Constructs ChromaDB IDs:
  - Lines: `IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}`
  - Words: `IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}`
- Deletes embeddings using `collection.delete(ids=[...])`

### 4. Receipt Handler

**File**: `infra/chromadb_compaction/lambdas/compaction/receipt_handler.py` (NEW)

- `process_receipt_deletions()`: Processes receipt deletions for a collection
- `apply_receipt_deletions_in_memory()`: Applies deletions to in-memory collection
- Handles snapshot download/upload and error handling

### 5. Main Handler Integration

**File**: `infra/chromadb_compaction/lambdas/enhanced_compaction_handler.py`

- Updated `categorize_stream_messages()` call to unpack 4 values (added receipt_deletions)
- Added receipt deletion processing in the main loop
- Processes deletions AFTER metadata/label updates but BEFORE closing client
- Tracks `total_receipt_deletions` in response

## Flow

1. **DynamoDB Stream Event**: Receipt entity deleted → REMOVE event
2. **Stream Processor**: Parses event, detects RECEIPT entity type
3. **Message Builder**: Creates StreamMessage for both collections (LINES, WORDS)
4. **Compactor**:
   - Downloads snapshot
   - Queries DynamoDB for all ReceiptLine/ReceiptWord entities
   - Constructs ChromaDB IDs
   - Deletes embeddings from collection
   - Uploads updated snapshot

## Benefits

✅ **Automatic Cleanup**: No manual deletion needed
✅ **No Orphaned Embeddings**: Embeddings are deleted when receipt is deleted
✅ **Works for Any Deletion**: Not just split_receipt.py, but any receipt deletion
✅ **Efficient**: Uses DynamoDB query to construct IDs (no collection scanning)
✅ **Safe**: Processes deletions after metadata/label updates

## Comparison with Manual Deletion

### Before (Manual in Script)
- Script had to manually delete embeddings
- Required direct ChromaDB access
- Only worked for split_receipt.py
- Risk of orphaned embeddings if script failed

### After (Automatic via Compactor)
- Compactor automatically handles deletion
- Works for ANY receipt deletion (not just split script)
- No manual intervention needed
- Consistent with other compactor operations

## Usage

No changes needed! When you delete a Receipt from DynamoDB:

1. DynamoDB stream fires REMOVE event
2. Compactor processes the event
3. Embeddings are automatically deleted from ChromaDB

The `split_receipt.py` script can now optionally skip manual deletion and let the compactor handle it automatically (though it can still do manual deletion for immediate cleanup).

## Testing

To test:
1. Delete a Receipt entity from DynamoDB
2. Check compactor logs for receipt deletion processing
3. Verify embeddings are removed from ChromaDB collections

