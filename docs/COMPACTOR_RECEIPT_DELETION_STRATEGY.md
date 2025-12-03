# Receipt Deletion Strategy - Manual vs Automatic

## Two Approaches

### Option 1: Manual Deletion (Current Implementation in split_receipt.py)

**Pros:**
- ✅ Immediate deletion (no waiting for compactor)
- ✅ Works even if compactor hasn't processed yet
- ✅ Can use receipt data before DynamoDB deletion

**Cons:**
- ❌ Requires direct ChromaDB access
- ❌ Only works in scripts that have receipt data
- ❌ Duplicate work if compactor also processes it

### Option 2: Automatic Deletion (Enhanced Compactor)

**Pros:**
- ✅ Works for ANY receipt deletion (not just split script)
- ✅ Automatic - no manual intervention needed
- ✅ Consistent with other compactor operations

**Cons:**
- ❌ Requires ReceiptLine/ReceiptWord entities to still exist in DynamoDB
- ❌ If lines/words are deleted first, compactor can't construct IDs
- ❌ Delayed (waits for stream processing)

## The Problem

When deleting in reverse order (as required by foreign key constraints):
1. Delete ReceiptWordLabel
2. Delete ReceiptWord
3. Delete ReceiptLine
4. Delete Receipt

By the time the Receipt is deleted, the ReceiptLine and ReceiptWord entities are already gone from DynamoDB. The compactor can't query them to construct ChromaDB IDs.

## Solution: Hybrid Approach

### For split_receipt.py:
1. **Manual deletion FIRST** (before DynamoDB deletion)
   - Uses receipt data we already have loaded
   - Immediate cleanup
   - Ensures no orphaned embeddings

2. **Then delete from DynamoDB** (in reverse order)
   - Compactor will try to process Receipt deletion
   - But won't find lines/words (already deleted)
   - Will log a warning and skip (expected behavior)

### For Other Deletions:
- If Receipt is deleted but lines/words still exist:
  - Compactor will automatically delete embeddings ✅
- If Receipt is deleted after lines/words:
  - Compactor will log warning and skip (embeddings may be orphaned)
  - This is expected when deleting in reverse order

## Recommendation

**Keep both approaches:**
- Manual deletion in `split_receipt.py` for immediate cleanup
- Automatic deletion in compactor for other use cases
- Compactor gracefully handles missing lines/words (logs warning)

This gives us:
- ✅ Immediate cleanup for split script
- ✅ Automatic cleanup for other deletions
- ✅ No errors when lines/words are already deleted

