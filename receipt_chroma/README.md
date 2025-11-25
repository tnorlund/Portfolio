# receipt-chroma

A Python interface for accessing ChromaDB for receipt vector storage and retrieval.

## Features

- **Proper Resource Management**: Implements context manager support and explicit `close()` method to prevent SQLite file locking issues
- **Clean API**: Simple, consistent interface for ChromaDB operations
- **Multiple Modes**: Support for read-only, read-write, delta, and snapshot modes
- **AWS Integration**: Built-in support for S3 operations and EFS access

## Installation

```bash
pip install receipt-chroma
```

## Usage

### Basic Usage with Context Manager (Recommended)

```python
from receipt_chroma import ChromaClient

# Use as context manager for automatic cleanup
with ChromaClient(persist_directory="/path/to/db") as client:
    collection = client.get_collection("my_collection")
    results = client.query(
        collection_name="my_collection",
        query_texts=["search query"],
        n_results=10
    )
    # Client is automatically closed when exiting the context
```

### Manual Resource Management

```python
from receipt_chroma import ChromaClient

client = ChromaClient(persist_directory="/path/to/db")
try:
    # Upsert vectors
    client.upsert(
        collection_name="my_collection",
        ids=["id1", "id2"],
        documents=["doc1", "doc2"],
        metadatas=[{"key": "value1"}, {"key": "value2"}]
    )

    # Query vectors
    results = client.query(
        collection_name="my_collection",
        query_texts=["search query"],
        n_results=10
    )
finally:
    # Always close the client to release SQLite connections
    client.close()
```

### Uploading to S3

```python
import boto3
from receipt_chroma import ChromaClient

client = ChromaClient(persist_directory="/tmp/chromadb", mode="delta")
try:
    # Perform operations
    client.upsert(...)

    # CRITICAL: Close client before uploading to prevent file locking
    client.close()

    # Now safe to upload files to S3
    s3_client = boto3.client("s3")
    # ... upload files ...
finally:
    client.close()  # Safe to call multiple times
```

## Why This Package?

This package addresses [GitHub issue #5868](https://github.com/chroma-core/chroma/issues/5868) where ChromaDB's `PersistentClient` doesn't expose a `close()` method, causing SQLite file locking issues when uploading databases to S3.

### Key Improvements

1. **Explicit `close()` method**: Properly releases SQLite connections and file locks
2. **Context manager support**: Automatic cleanup with `with` statements
3. **Consolidated accessors**: All ChromaDB operations in one clean package
4. **Better testing**: Dedicated package allows for comprehensive test coverage

## Migration from Existing Code

If you're currently using ChromaDB clients from `receipt_label` or `infra/`, you can migrate to this package:

### Before:
```python
from receipt_label.utils.chroma_client import ChromaDBClient

client = ChromaDBClient(persist_directory="/path/to/db")
# ... use client ...
# No explicit close() - relies on GC
```

### After:
```python
from receipt_chroma import ChromaClient

with ChromaClient(persist_directory="/path/to/db") as client:
    # ... use client ...
    # Automatic cleanup
```

## Development

```bash
# Install with dev dependencies
pip install -e ".[dev,test]"

# Run tests
pytest

# Format code
black receipt_chroma/

# Lint
pylint receipt_chroma/
```

## License

MIT

