# receipt-chroma

`receipt-chroma` owns the receipt vector-store boundary: a resource-safe
ChromaDB client, receipt embedding payloads, S3 snapshot and delta operations,
and the compaction helpers used by the deployed embedding pipeline.

## Deployed architecture

The upload path queries Chroma Cloud when it is enabled. Writes are emitted as
receipt-scoped deltas in S3, and DynamoDB compaction records trigger the async
compactor. The compactor merges those deltas into compressed, versioned S3
snapshots and advances each collection's pointer atomically.

The package does not manage EFS and no longer supports the retired ECS Chroma
HTTP service. Local persistent clients are still used while creating deltas,
compacting snapshots, and running offline tools and tests.

Both currently deployed S3 delta layouts remain supported. They should only be
consolidated after the remaining producers and stored objects have been
migrated.

## Public API

The package root is intentionally small and lazy:

```python
from receipt_chroma import ChromaClient, LockManager
```

Import workflow-specific APIs from their owning modules:

```python
from receipt_chroma.embedding.orchestration import (
    EmbeddingConfig,
    create_embeddings_and_compaction_run,
)
from receipt_chroma.s3 import download_snapshot_atomic, upload_snapshot_atomic
```

`ChromaClient` accepts three modes:

- `read`: query/get only; collections are never created implicitly.
- `write`: create or update a persistent, ephemeral, or Chroma Cloud
  collection.
- `delta`: create a local delta database before uploading it to S3.

Always close persistent clients before copying or uploading their directory.
The context-manager form is preferred:

```python
from receipt_chroma import ChromaClient

with ChromaClient(
    persist_directory="/tmp/receipt-vectors",
    mode="write",
    metadata_only=True,
) as client:
    client.upsert(
        collection_name="lines",
        ids=["line-1"],
        embeddings=[[0.1, 0.2, 0.3]],
        documents=["example receipt line"],
        metadatas=[{"image_id": "image-1", "receipt_id": 1}],
    )
```

For Chroma Cloud, pass `cloud_api_key`, `cloud_tenant`, and `cloud_database`.
The embedding upload service reads these values from the corresponding
`CHROMA_CLOUD_*` environment variables.

## Development

Python 3.12 is the production and test baseline. From this directory:

```bash
python3.12 -m pip install -e ".[test,dev]"
python3.12 -m pytest tests/unit
python3.12 -m pytest tests/integration
```

The S3 integration suite uses Moto and does not require writes to a live AWS
account. A Pulumi deployment is a separate operation and is not performed by
the package tests.
