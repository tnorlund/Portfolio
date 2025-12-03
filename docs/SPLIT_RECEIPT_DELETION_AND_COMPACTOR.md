# Split Receipt Deletion and Compactor Behavior

## Question 1: Does `split_receipt.py` wait for compaction before deleting records?

### Answer: **NO - `split_receipt.py` does NOT delete any records**

The script **only creates new receipts** and **preserves the original receipt** for rollback:

```python
# From split_receipt.py line 1452:
print(f"   Original receipt {original_receipt_id} kept for rollback")
```

**Order of operations:**
1. ✅ Creates new Receipt, ReceiptLine, ReceiptWord, ReceiptLetter records
2. ✅ Saves to DynamoDB
3. ✅ Exports NDJSON to S3
4. ✅ Creates embeddings and CompactionRun records
5. ✅ **Waits for compaction to complete** (line 1384-1402)
6. ✅ Adds labels AFTER compaction completes
7. ❌ **Does NOT delete anything** - original receipt is preserved

**Why wait for compaction?**
- The script waits for compaction to complete **before adding labels** (not before deletion)
- This ensures labels are added to embeddings that already exist in ChromaDB
- If compaction fails/times out, labels are still added (they'll update when compaction completes later)

## Question 2: How does the DynamoDB stream and enhanced compactor remove receipt lines and words from ChromaDB?

### Answer: **They DON'T - The compactor does NOT delete embeddings**

### Key Finding: Compactor Only Processes Metadata and Labels

The enhanced compactor **explicitly ignores** `ReceiptLine` and `ReceiptWord` deletions:

#### Evidence from Code:

1. **`detect_entity_type()` in `parsers.py`** (line 25-41):
   ```python
   def detect_entity_type(sk: str) -> Optional[str]:
       if "#METADATA" in sk:
           return "RECEIPT_METADATA"
       if "#LABEL#" in sk:
           return "RECEIPT_WORD_LABEL"
       if "#COMPACTION_RUN#" in sk:
           return "COMPACTION_RUN"
       return None  # ReceiptLine and ReceiptWord return None!
   ```

2. **`parse_entity()` in `parsers.py`** (line 90-93):
   ```python
   if entity_type == "RECEIPT_METADATA":
       return item_to_receipt_metadata(complete_item)
   if entity_type == "RECEIPT_WORD_LABEL":
       return item_to_receipt_word_label(complete_item)
   # No handlers for ReceiptLine or ReceiptWord!
   ```

3. **Tests verify this behavior**:
   - `test_parse_receipt_line_ignored` - confirms ReceiptLine is ignored
   - `test_parse_receipt_word_ignored` - confirms ReceiptWord is ignored

### What the Compactor DOES Process:

#### ✅ RECEIPT_METADATA
- **Stream Event**: `MODIFY` or `REMOVE`
- **Compactor Action**:
  - `update_receipt_metadata()` - Updates merchant metadata on embeddings
  - `remove_receipt_metadata()` - Removes merchant metadata from embeddings
- **Effect**: Updates/removes metadata fields (merchant_name, address, etc.) but **embeddings remain**

#### ✅ RECEIPT_WORD_LABEL
- **Stream Event**: `MODIFY` or `REMOVE`
- **Compactor Action**:
  - `update_word_labels()` - Updates label metadata on word embeddings
  - `remove_word_labels()` - Removes label metadata from word embeddings
- **Effect**: Updates/removes label metadata but **embeddings remain**

#### ❌ RECEIPT_LINE / RECEIPT_WORD
- **Stream Event**: `REMOVE` (when deleted from DynamoDB)
- **Compactor Action**: **NONE** - these entity types are ignored
- **Effect**: **Embeddings remain orphaned in ChromaDB**

### How Deletions Work:

When you delete `ReceiptLine` or `ReceiptWord` from DynamoDB:

1. **DynamoDB Stream Event**: Creates a `REMOVE` event
2. **Stream Processor**: Receives the event
3. **Parser**: `detect_entity_type()` returns `None` (not recognized)
4. **Result**: Event is **ignored**, no message sent to compactor
5. **ChromaDB**: Embeddings remain with no way to reference them back to DynamoDB

### ChromaDB ID Format:

Embeddings are stored with IDs that reference DynamoDB entities:
- **Lines**: `IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}`
- **Words**: `IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}`

When the DynamoDB record is deleted, the ChromaDB embedding remains with this ID, but there's no corresponding DynamoDB record anymore.

### Impact of Orphaned Embeddings:

1. **Storage**: Orphaned embeddings consume ChromaDB storage
2. **Search Results**: Orphaned embeddings may appear in search results
3. **No Automatic Cleanup**: There's no way to automatically clean them up
4. **Manual Cleanup Required**: Must manually delete from ChromaDB using the ID format

### Summary:

| Entity Type | Deletion from DynamoDB | Compactor Action | Embedding Deleted? |
|------------|------------------------|------------------|-------------------|
| ReceiptLine | ✅ Deleted | ❌ Ignored | ❌ **NO - Orphaned** |
| ReceiptWord | ✅ Deleted | ❌ Ignored | ❌ **NO - Orphaned** |
| ReceiptWordLabel | ✅ Deleted | ✅ Removes label metadata | ❌ **NO - Embedding remains** |
| ReceiptMetadata | ✅ Deleted | ✅ Removes merchant metadata | ❌ **NO - Embeddings remain** |
| Receipt | ✅ Deleted | ❌ Ignored | ❌ **NO - All embeddings orphaned** |

### Conclusion:

**The enhanced compactor does NOT delete embeddings when ReceiptLine or ReceiptWord are deleted from DynamoDB.** This is by design - the compactor only handles metadata updates, not entity lifecycle management.

If you need to clean up embeddings when deleting receipts:
1. **Manual cleanup**: Delete embeddings from ChromaDB using the ID format
2. **Soft delete**: Mark receipts as deleted instead of deleting them
3. **Future enhancement**: Add deletion handlers to the compactor (would require code changes)

