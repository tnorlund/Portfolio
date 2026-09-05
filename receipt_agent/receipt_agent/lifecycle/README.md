# Receipt Lifecycle Management

This module provides unified functions for creating and deleting receipts in
DynamoDB. It's designed to be used by:

- Split receipt scripts
- Combine receipt scripts
- Receipt agent workflows

## Overview

1. **Creation**: save the Receipt and its child entities to DynamoDB, then
   export NDJSON to S3. Embeddings are produced downstream by the DynamoDB
   stream processor as native `*_EMBEDDING` items, so this module never
   writes vectors directly.
2. **Deletion**: delete the Receipt entity. Child records are *not*
   cascaded; callers that need a full cascade must delete them explicitly.

## API

### `create_receipt`

```python
from receipt_agent.lifecycle import create_receipt

result = create_receipt(
    client=dynamo_client,
    receipt=receipt,
    receipt_lines=lines,
    receipt_words=words,
    receipt_letters=letters,          # optional
    receipt_labels=labels,            # optional
    artifacts_bucket="artifacts-bucket",
    export_ndjson_flag=True,
    dry_run=False,
)
print(result.success, result.error)
```

### `delete_receipt`

```python
from receipt_agent.lifecycle import delete_receipt

result = delete_receipt(
    client=dynamo_client,
    image_id=image_id,
    receipt_id=receipt_id,
)
print(result.success, result.dynamodb_deleted)
```

Only the Receipt entity is removed (the legacy ``receipt_labels`` /
``receipt_letters`` keyword arguments are accepted but ignored).
`ReceiptLine`, `ReceiptWord`, `ReceiptPlace`, and the native embedding items
remain until the caller deletes them; the DynamoDB stream processor keeps the
embedding items' metadata fresh while they exist.

### `export_receipt_ndjson`

Writes the receipt's line/word NDJSON export to the artifacts bucket. Used by
`create_receipt` and available standalone for re-exports.

## Result types

- `ReceiptCreationResult` — `image_id`, `receipt_id`, `success`, `error`
- `ReceiptDeletionResult` — `image_id`, `receipt_id`, `dynamodb_deleted`,
  `success`, `error`
