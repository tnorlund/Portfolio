# Split Receipt Rollback Strategy

## Overview

If a split receipt operation needs to be rolled back, we can delete the new receipts and their sub-records. However, **embeddings in ChromaDB will remain orphaned** since the enhanced compactor does **NOT** delete embeddings when `ReceiptWord` or `ReceiptLine` entities are deleted.

## Key Finding: Compactor Does NOT Delete Embeddings

**The enhanced compactor only processes two entity types:**
1. `RECEIPT_METADATA` - Updates/removes merchant metadata
2. `RECEIPT_WORD_LABEL` - Updates/removes label metadata

**The compactor explicitly ignores:**
- `RECEIPT` - No handler
- `RECEIPT_LINE` - Explicitly ignored (see `test_parse_receipt_line_ignored`)
- `RECEIPT_WORD` - Explicitly ignored (see `test_parse_receipt_word_ignored`)

**Result**: When you delete `ReceiptWord` or `ReceiptLine` from DynamoDB, the corresponding embeddings remain in ChromaDB with no way to reference them back to DynamoDB.

## Deletion Order

Delete in **reverse order of creation** to avoid foreign key issues:

1. **ReceiptWordLabel** (references words)
2. **ReceiptWord** (references lines)
3. **ReceiptLine** (references receipt)
4. **ReceiptLetter** (references words, optional)
5. **ReceiptMetadata** (references receipt, optional)
6. **CompactionRun** (references receipt, optional)
7. **Receipt** (top-level entity)

## How Deletes Affect Enhanced Compactor

### ReceiptWordLabel DELETE
- **Stream Event**: `REMOVE`
- **Compactor Action**: Calls `remove_word_labels()`
- **Effect**: Removes label metadata from ChromaDB embedding (but embedding remains)
- **Safe**: ✅ Yes - only removes metadata, embedding stays

### ReceiptMetadata DELETE
- **Stream Event**: `REMOVE`
- **Compactor Action**: Calls `remove_receipt_metadata()`
- **Effect**: Removes merchant metadata from ChromaDB embeddings (but embeddings remain)
- **Safe**: ✅ Yes - only removes metadata, embeddings stay

### ReceiptWord/ReceiptLine DELETE
- **Stream Event**: `REMOVE`
- **Compactor Action**: **None** - these entity types are explicitly ignored
- **Evidence**:
  - `parsers.py` only processes `RECEIPT_METADATA`, `RECEIPT_WORD_LABEL`, and `COMPACTION_RUN`
  - Tests explicitly verify: `test_parse_receipt_line_ignored` and `test_parse_receipt_word_ignored`
- **Effect**: **Embeddings remain orphaned in ChromaDB**
- **Safe**: ⚠️ **No** - embeddings will be orphaned

### CompactionRun DELETE
- **Stream Event**: `REMOVE`
- **Compactor Action**: **None** - compactor doesn't process CompactionRun deletions
- **Effect**: CompactionRun record removed, but deltas remain in S3
- **Safe**: ✅ Yes - just removes the record

### Receipt DELETE
- **Stream Event**: `REMOVE`
- **Compactor Action**: **None** - no handler for Receipt entity
- **Effect**: Receipt removed, but all embeddings remain in ChromaDB
- **Safe**: ⚠️ **No** - embeddings will be orphaned

## Critical Issue: Orphaned Embeddings

**Problem**: When we delete `ReceiptWord` or `ReceiptLine`, the corresponding embeddings remain in ChromaDB with no way to reference them back to DynamoDB.

**ChromaDB IDs format**:
- Lines: `IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}`
- Words: `IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#WORD#{word_id:05d}`

**Impact**:
- Orphaned embeddings consume storage
- Search results may include orphaned embeddings
- No way to clean them up automatically

## Rollback Options

### Option 1: Delete Everything (Simple but Leaves Orphans)

```python
# Delete in reverse order
for receipt_id in new_receipt_ids:
    # 1. Delete labels
    labels = client.list_receipt_word_labels_for_receipt(image_id, receipt_id)
    for label in labels:
        client.delete_receipt_word_label(...)

    # 2. Delete words
    words = client.list_receipt_words_from_receipt(image_id, receipt_id)
    for word in words:
        client.delete_receipt_word(...)

    # 3. Delete lines
    lines = client.list_receipt_lines_from_receipt(image_id, receipt_id)
    for line in lines:
        client.delete_receipt_line(...)

    # 4. Delete receipt
    client.delete_receipt(image_id, receipt_id)

    # 5. Delete CompactionRun (if exists)
    runs = client.list_compaction_runs_for_receipt(image_id, receipt_id)
    for run in runs:
        client.delete_compaction_run(...)
```

**Result**: DynamoDB cleaned up, but ChromaDB embeddings remain orphaned.

### Option 2: Delete + Clean ChromaDB (Complete but Complex)

1. **Delete DynamoDB records** (same as Option 1)
2. **Manually delete ChromaDB embeddings**:
   ```python
   # Get all ChromaDB IDs for the receipt
   chromadb_ids = []
   for word in words:
       chromadb_ids.append(f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{word.line_id:05d}#WORD#{word.word_id:05d}")
   for line in lines:
       chromadb_ids.append(f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line.line_id:05d}")

   # Delete from ChromaDB
   lines_collection.delete(ids=[id for id in chromadb_ids if "WORD" not in id])
   words_collection.delete(ids=[id for id in chromadb_ids if "WORD" in id])
   ```

**Result**: Complete cleanup, but requires direct ChromaDB access.

### Option 3: Keep Receipts, Mark as Deleted (Safest)

Instead of deleting, mark receipts as deleted:

```python
# Add a "deleted" flag to receipt
receipt.deleted = True
client.update_receipt(receipt)
```

**Result**: No orphaned embeddings, can be restored later.

## Recommended Rollback Script

```python
#!/usr/bin/env python3
"""
Rollback script for split receipt operation.

Deletes new receipts and sub-records in correct order.
Note: ChromaDB embeddings will remain orphaned.
"""

import argparse
from receipt_dynamo import DynamoClient

def rollback_split_receipt(
    client: DynamoClient,
    image_id: str,
    new_receipt_ids: list[int],
    dry_run: bool = True
):
    """Rollback split receipt by deleting new receipts."""

    print(f"🔄 Rolling back split receipt for image: {image_id}")
    print(f"   Receipt IDs to delete: {new_receipt_ids}")
    print(f"   Dry run: {dry_run}")

    if dry_run:
        print("\n⚠️  DRY RUN - No changes will be made")

    for receipt_id in new_receipt_ids:
        print(f"\n📋 Processing receipt {receipt_id}...")

        # 1. Delete labels
        labels, _ = client.list_receipt_word_labels_for_receipt(image_id, receipt_id)
        print(f"   Found {len(labels)} labels")
        if not dry_run:
            for label in labels:
                client.delete_receipt_word_label(
                    image_id, receipt_id, label.line_id, label.word_id, label.label
                )
            print(f"   ✅ Deleted {len(labels)} labels")

        # 2. Delete words
        words = client.list_receipt_words_from_receipt(image_id, receipt_id)
        print(f"   Found {len(words)} words")
        if not dry_run:
            for word in words:
                client.delete_receipt_word(image_id, receipt_id, word.line_id, word.word_id)
            print(f"   ✅ Deleted {len(words)} words")

        # 3. Delete lines
        lines = client.list_receipt_lines_from_receipt(image_id, receipt_id)
        print(f"   Found {len(lines)} lines")
        if not dry_run:
            for line in lines:
                client.delete_receipt_line(image_id, receipt_id, line.line_id)
            print(f"   ✅ Deleted {len(lines)} lines")

        # 4. Delete letters (if any)
        try:
            letters = client.list_receipt_letters_from_receipt(image_id, receipt_id)
            print(f"   Found {len(letters)} letters")
            if not dry_run and letters:
                for letter in letters:
                    client.delete_receipt_letter(
                        image_id, receipt_id, letter.line_id, letter.word_id, letter.letter_id
                    )
                print(f"   ✅ Deleted {len(letters)} letters")
        except AttributeError:
            pass  # Letters might not be supported

        # 5. Delete metadata (if any)
        try:
            metadata = client.get_receipt_metadata(image_id, receipt_id)
            if metadata:
                print(f"   Found metadata")
                if not dry_run:
                    client.delete_receipt_metadata(image_id, receipt_id)
                    print(f"   ✅ Deleted metadata")
        except Exception:
            pass  # Metadata might not exist

        # 6. Delete CompactionRun (if any)
        runs, _ = client.list_compaction_runs_for_receipt(image_id, receipt_id)
        print(f"   Found {len(runs)} compaction runs")
        if not dry_run:
            for run in runs:
                client.delete_compaction_run(image_id, receipt_id, run.run_id)
            print(f"   ✅ Deleted {len(runs)} compaction runs")

        # 7. Delete receipt
        print(f"   Deleting receipt...")
        if not dry_run:
            client.delete_receipt(image_id, receipt_id)
            print(f"   ✅ Deleted receipt {receipt_id}")

    print(f"\n✅ Rollback complete!")
    print(f"⚠️  Note: ChromaDB embeddings remain orphaned (not automatically deleted)")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Rollback split receipt operation")
    parser.add_argument("--image-id", required=True, help="Image ID")
    parser.add_argument("--receipt-ids", required=True, nargs="+", type=int, help="Receipt IDs to delete")
    parser.add_argument("--dry-run", action="store_true", help="Dry run mode")
    args = parser.parse_args()

    # Setup client
    from scripts.split_receipt import setup_environment
    config = setup_environment()
    client = DynamoClient(config["table_name"])

    rollback_split_receipt(client, args.image_id, args.receipt_ids, args.dry_run)
```

## Summary

**Yes, you can rollback by deleting**, but:

1. ✅ **DynamoDB cleanup**: Works perfectly - delete in reverse order
2. ⚠️ **ChromaDB cleanup**: Embeddings remain orphaned (no automatic deletion)
3. ✅ **Label cleanup**: Compactor removes label metadata automatically
4. ✅ **Metadata cleanup**: Compactor removes merchant metadata automatically

**Recommendation**:
- For quick rollback: Use Option 1 (delete DynamoDB records)
- For complete cleanup: Use Option 2 (delete DynamoDB + manually clean ChromaDB)
- For safety: Use Option 3 (mark as deleted instead of deleting)

