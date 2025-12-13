# Receipt Lifecycle Management

This module provides unified functions for creating and deleting receipts across DynamoDB and ChromaDB. It's designed to be used by:

- **Split receipt scripts** (`scripts/split_receipt.py`)
- **Combine receipt scripts** (`infra/combine_receipts_step_functions/lambdas/combine_receipts_logic.py`)
- **Receipt agent workflows**

## 📖 Detailed Walkthrough

For a complete explanation of how receipts are added and deleted, including DynamoDB and ChromaDB operations, see **[RECEIPT_LIFECYCLE_WALKTHROUGH.md](./RECEIPT_LIFECYCLE_WALKTHROUGH.md)**.

## Overview

The lifecycle module handles the complete lifecycle of receipts:

1. **Creation**: Save to DynamoDB, create embeddings in ChromaDB, export NDJSON, wait for compaction
2. **Deletion**: Delete embeddings from ChromaDB, delete from DynamoDB in correct order

## Main Functions

### `create_receipt()`

Creates a receipt with all associated entities in DynamoDB and ChromaDB.

```python
from receipt_agent.lifecycle import create_receipt, ReceiptCreationResult
from receipt_dynamo import DynamoClient
from receipt_dynamo.entities import Receipt, ReceiptLine, ReceiptWord

client = DynamoClient("ReceiptsTable-dc5be22")

result: ReceiptCreationResult = create_receipt(
    client=client,
    receipt=receipt,
    receipt_lines=receipt_lines,
    receipt_words=receipt_words,
    receipt_letters=receipt_letters,  # Optional
    receipt_labels=receipt_labels,  # Optional
    receipt_metadata=receipt_metadata,  # Optional
    chromadb_bucket="chromadb-bucket",
    artifacts_bucket="artifacts-bucket",
    merchant_name="Store Name",  # Optional
    create_embeddings_flag=True,
    export_ndjson_flag=True,
    wait_for_compaction_flag=False,  # Set to True to wait for compaction
    dry_run=False,
)

if result.success:
    print(f"Created receipt {result.receipt_id} with compaction run {result.compaction_run_id}")
else:
    print(f"Error: {result.error}")
```

### `delete_receipt()`

Deletes a receipt and all associated entities from DynamoDB. **ChromaDB embeddings are automatically deleted by the enhanced compactor** when the Receipt entity is deleted (via DynamoDB streams).

**Important**: The enhanced compactor is the source of truth for ChromaDB. It queries DynamoDB for ReceiptLine/ReceiptWord entities to construct ChromaDB IDs and deletes embeddings from S3/EFS snapshots. Do not manually delete embeddings.

```python
from receipt_agent.lifecycle import delete_receipt, ReceiptDeletionResult

result: ReceiptDeletionResult = delete_receipt(
    client=client,
    image_id="image-uuid",
    receipt_id=1,
    receipt_labels=receipt_labels,  # Optional, fetched if not provided
    receipt_letters=receipt_letters,  # Optional, fetched if not provided
)

if result.success:
    print(f"Deleted receipt {result.receipt_id}")
    print(f"DynamoDB deleted: {result.dynamodb_deleted}")
    print("Enhanced compactor will automatically delete embeddings from ChromaDB via DynamoDB streams")
else:
    print(f"Error: {result.error}")
```

## Lower-Level Functions

### Embedding Management

```python
from receipt_agent.lifecycle import create_embeddings

# Create embeddings
run_id = create_embeddings(
    client=client,
    chromadb_bucket="chromadb-bucket",
    image_id="image-uuid",
    receipt_id=1,
    receipt_lines=receipt_lines,
    receipt_words=receipt_words,
    merchant_name="Store Name",  # Optional
)

# Note: Embedding deletion is handled automatically by the enhanced compactor
# when Receipt entities are deleted from DynamoDB. Do not manually delete embeddings.
```

### Compaction Management

```python
from receipt_agent.lifecycle import wait_for_compaction, check_compaction_status

# Wait for compaction to complete
run_id = wait_for_compaction(
    client=client,
    image_id="image-uuid",
    receipt_id=1,
    max_wait_seconds=300,
    poll_interval=5,
    initial_wait_seconds=10,
)

# Check compaction status
run_id, lines_state, words_state = check_compaction_status(
    client=client,
    image_id="image-uuid",
    receipt_id=1,
)
```

### NDJSON Export

```python
from receipt_agent.lifecycle import export_receipt_ndjson

export_receipt_ndjson(
    client=client,
    artifacts_bucket="artifacts-bucket",
    image_id="image-uuid",
    receipt_id=1,
    receipt_lines=receipt_lines,  # Optional, fetched if not provided
    receipt_words=receipt_words,  # Optional, fetched if not provided
)
```

## Module Structure

```
receipt_agent/lifecycle/
├── __init__.py              # Public API exports
├── receipt_manager.py        # Main create/delete functions
├── embedding_manager.py      # Embedding creation/deletion
├── compaction_manager.py    # Compaction waiting/status
└── ndjson_manager.py        # NDJSON export
```

## Key Features

1. **Unified API**: Single entry point for receipt creation/deletion
2. **Automatic ChromaDB Deletion**: Enhanced compactor automatically deletes embeddings when Receipt is deleted (via DynamoDB streams). The compactor is the source of truth for ChromaDB - it queries DynamoDB for lines/words and deletes embeddings from S3/EFS snapshots.
3. **Proper Ordering**: DynamoDB deletion happens in correct order (reverse of creation)
4. **Error Handling**: Graceful error handling with result objects
5. **Flexible**: Optional flags for embedding creation, NDJSON export, compaction waiting
6. **Dry Run Support**: Can test without actually saving to DynamoDB

## Migration Guide

### From `split_receipt.py`

**Before:**
```python
# Save to DynamoDB
client.add_receipt(receipt)
client.add_receipt_lines(receipt_lines)
client.add_receipt_words(receipt_words)

# Create embeddings
run_id = create_embeddings_and_compaction_run(...)

# Export NDJSON
export_receipt_ndjson_to_s3(...)

# Wait for compaction
wait_for_compaction_complete(...)
```

**After:**
```python
from receipt_agent.lifecycle import create_receipt

result = create_receipt(
    client=client,
    receipt=receipt,
    receipt_lines=receipt_lines,
    receipt_words=receipt_words,
    chromadb_bucket=chromadb_bucket,
    artifacts_bucket=artifacts_bucket,
    create_embeddings_flag=True,
    export_ndjson_flag=True,
    wait_for_compaction_flag=True,
)
```

### From `combine_receipts_logic.py`

**Before:**
```python
# Save to DynamoDB
client.add_receipt(records["receipt"])
client.add_receipt_lines(records["receipt_lines"])
client.add_receipt_words(records["receipt_words"])
client.add_receipt_letters(records["receipt_letters"])

# Create embeddings (inline code)
# Export NDJSON (inline code)
```

**After:**
```python
from receipt_agent.lifecycle import create_receipt

result = create_receipt(
    client=client,
    receipt=records["receipt"],
    receipt_lines=records["receipt_lines"],
    receipt_words=records["receipt_words"],
    receipt_letters=records["receipt_letters"],
    receipt_labels=migrated_labels,
    receipt_metadata=receipt_metadata,
    chromadb_bucket=chromadb_bucket,
    artifacts_bucket=artifacts_bucket,
    create_embeddings_flag=True,
    export_ndjson_flag=True,
)
```

## Notes

- **ChromaDB Deletion**: The enhanced compactor is the **source of truth** for ChromaDB. It automatically deletes embeddings when a Receipt entity is deleted from DynamoDB (via DynamoDB streams). The compactor queries DynamoDB for ReceiptLine/ReceiptWord entities to construct ChromaDB IDs and deletes embeddings from S3/EFS snapshots. **Do not manually delete embeddings** - modifying local snapshots is not the source of truth.

- **Deletion Order**: If you delete ReceiptLine/ReceiptWord entities before the Receipt, the compactor won't be able to query them to construct ChromaDB IDs. To ensure proper deletion, delete the Receipt entity while lines/words still exist, or delete the Receipt first (if foreign key constraints allow).

- **Compaction**: The `wait_for_compaction_flag` is optional - you can also call `wait_for_compaction()` separately

- **Dry Run**: When `dry_run=True`, DynamoDB operations are skipped but embeddings are still created (useful for testing)

