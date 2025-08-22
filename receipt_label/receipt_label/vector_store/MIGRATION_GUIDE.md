# Vector Store Migration Guide

This guide helps you migrate from the old scattered ChromaDB files to the new organized `vector_store` module.

## Overview of Changes

### Old Structure (Scattered Files)
```
receipt_label/utils/
├── chroma_client.py            # 451 lines - main client
├── chroma_client_old.py        # Legacy version
├── chroma_client_refactored.py # 313 lines - alternative
├── chroma_compactor.py         # Compaction logic
├── chroma_hash.py             # 315 lines - hash utilities
└── chroma_s3_helpers.py       # S3 operations

receipt_label/decision_engine/
└── chroma_integration.py       # Decision engine integration
```

### New Structure (Organized Module)
```
receipt_label/vector_store/
├── __init__.py                 # Public API exports
├── client/
│   ├── base.py                 # Abstract interface
│   ├── chromadb_client.py      # Unified ChromaDB client
│   └── factory.py              # Client factory & singletons
├── storage/
│   ├── hash_calculator.py      # Directory hashing
│   ├── s3_operations.py        # S3 upload/download
│   └── snapshot_manager.py     # High-level snapshots
├── compaction/
│   ├── delta_processor.py      # Delta file processing
│   └── compaction_engine.py    # Full compaction orchestration
└── integration/
    ├── decision_engine.py      # Decision engine integration
    └── pattern_matching.py     # Pattern storage/retrieval
```

## Migration Examples

### Basic Client Creation

**Old Way:**
```python
# Multiple confusing options
from receipt_label.utils.chroma_client import ChromaDBClient, get_chroma_client
from receipt_label.utils.chroma_client_refactored import get_word_client

# Unclear which one to use!
client1 = ChromaDBClient(persist_directory="/tmp", mode="read")
client2 = get_chroma_client(persist_directory="/tmp") 
client3 = get_word_client("/tmp", mode="read")
```

**New Way:**
```python
# Clean, obvious API
from receipt_label.vector_store import VectorClient

# Primary way - explicit and clear
client = VectorClient.create_chromadb_client(
    persist_directory="/tmp",
    mode="read"
)

# Legacy compatibility still works
from receipt_label.vector_store import get_chroma_client
client = get_chroma_client(persist_directory="/tmp")
```

### Storage Operations

**Old Way:**
```python
from receipt_label.utils.chroma_s3_helpers import (
    upload_snapshot_with_hash,
    download_snapshot_from_s3
)
from receipt_label.utils.chroma_hash import calculate_chromadb_hash

# Scattered functions
upload_snapshot_with_hash(local_dir, bucket, prefix, collection)
hash_result = calculate_chromadb_hash(directory)
```

**New Way:**
```python
# Organized classes
from receipt_label.vector_store import SnapshotManager, HashCalculator

# High-level operations
manager = SnapshotManager(bucket="my-bucket", s3_prefix="snapshots/")
snapshot_result = manager.create_snapshot(
    vector_client=client,
    collection_name="words",
    upload_to_s3=True
)

# Or lower-level operations
calculator = HashCalculator()
hash_result = calculator.calculate_directory_hash(directory)
```

### Compaction Operations

**Old Way:**
```python
# Logic scattered across multiple files
from receipt_label.utils.chroma_compactor import ChromaCompactor
from receipt_label.utils.lock_manager import LockManager

# Manual orchestration required
compactor = ChromaCompactor(bucket="my-bucket")
# ... manual lock management, delta processing, snapshot creation
```

**New Way:**
```python
# Complete orchestration in one class
from receipt_label.vector_store import CompactionEngine

engine = CompactionEngine(
    bucket_name="my-bucket",
    collection_name="words",
    database_name="production"
)

# One method does everything
result = engine.run_compaction(max_deltas=50)
```

### Decision Engine Integration

**Old Way:**
```python
from receipt_label.decision_engine.chroma_integration import ChromaDecisionHelper
from receipt_label.utils.chroma_client import get_chroma_client

client = get_chroma_client()
helper = ChromaDecisionHelper(client, config)
reliability = await helper.get_merchant_reliability("Walmart")
```

**New Way:**
```python
from receipt_label.vector_store import VectorClient, VectorDecisionEngine

client = VectorClient.create_chromadb_client(mode="read")
engine = VectorDecisionEngine(client, collection_name="merchant_patterns")
reliability = await engine.get_merchant_reliability("Walmart")
```

## Migration Steps

### Step 1: Update Imports

Replace old imports with new ones. The new module provides backward compatibility, so you can migrate gradually:

```python
# OLD
from receipt_label.utils.chroma_client import get_chroma_client

# NEW (backward compatible)
from receipt_label.vector_store import get_chroma_client

# BEST (new API)
from receipt_label.vector_store import VectorClient
client = VectorClient.create_chromadb_client(mode="read")
```

### Step 2: Replace Scattered Functions with Classes

**For S3 Operations:**
```python
# OLD
from receipt_label.utils.chroma_s3_helpers import upload_delta_to_s3

# NEW
from receipt_label.vector_store import S3Operations
s3_ops = S3Operations(bucket_name="my-bucket")
delta_key = s3_ops.upload_delta(local_directory, "deltas/")
```

**For Hash Calculations:**
```python
# OLD  
from receipt_label.utils.chroma_hash import calculate_chromadb_hash

# NEW
from receipt_label.vector_store import HashCalculator
result = HashCalculator.calculate_directory_hash(directory)
```

### Step 3: Use High-Level Operations

Instead of manually orchestrating operations, use the high-level classes:

```python
# Instead of manually managing snapshots, deltas, locks, etc.
from receipt_label.vector_store import CompactionEngine, SnapshotManager

# High-level compaction
engine = CompactionEngine(bucket_name="bucket", collection_name="words") 
result = engine.run_compaction()

# High-level snapshots
manager = SnapshotManager(bucket_name="bucket")
snapshot = manager.create_snapshot(client, "words", upload_to_s3=True)
```

### Step 4: Remove Old Files (After Migration)

Once you've migrated all code, you can safely remove these old files:
- `receipt_label/utils/chroma_client_old.py`
- `receipt_label/utils/chroma_client_refactored.py` 
- `receipt_label/utils/chroma_compactor.py`

Keep these for compatibility during migration:
- `receipt_label/utils/chroma_client.py` (provides legacy imports)
- `receipt_label/utils/chroma_hash.py` (until fully migrated)
- `receipt_label/utils/chroma_s3_helpers.py` (until fully migrated)

## Benefits of the New Structure

1. **Clear Organization**: Files are organized by functionality, not technology
2. **Better Names**: No more `chroma_client_refactored.py` vs `chroma_client.py` confusion
3. **Extensible**: Easy to add other vector stores (Pinecone, Weaviate, etc.)
4. **High-Level Operations**: Complete workflows in single method calls
5. **Backward Compatible**: Existing code continues to work during migration
6. **Better Testing**: Each component can be tested independently
7. **Consistent Interface**: All vector stores implement the same interface

## Common Migration Patterns

### Pattern 1: Basic Client Usage
```python
# OLD
from receipt_label.utils.chroma_client import ChromaDBClient
client = ChromaDBClient(persist_directory="/tmp", mode="read")

# NEW
from receipt_label.vector_store import VectorClient  
client = VectorClient.create_chromadb_client(persist_directory="/tmp", mode="read")
```

### Pattern 2: Infrastructure Integration
```python
# OLD - scattered imports and manual setup
from receipt_label.utils.chroma_s3_helpers import produce_embedding_delta
from receipt_label.utils.lock_manager import LockManager
# ... manual coordination

# NEW - high-level operations
from receipt_label.vector_store import CompactionEngine
engine = CompactionEngine(bucket_name="bucket", collection_name="words")
result = engine.run_compaction()
```

### Pattern 3: Decision Engine Integration
```python
# OLD
from receipt_label.decision_engine.chroma_integration import ChromaDecisionHelper

# NEW
from receipt_label.vector_store import VectorDecisionEngine
```

## Testing Your Migration

Use the included test script to verify everything works:

```bash
cd receipt_label
python -m receipt_label.vector_store.test_structure
```

This will verify that all imports work correctly and the basic API is functional.

## Support for Multiple Vector Stores

The new architecture makes it easy to support multiple vector store backends:

```python
from receipt_label.vector_store import VectorClient

# ChromaDB (current)
chroma_client = VectorClient.create_chromadb_client(mode="read")

# Future: Pinecone support
# pinecone_client = VectorClient.create_pinecone_client(api_key="key")

# Future: Weaviate support  
# weaviate_client = VectorClient.create_weaviate_client(url="http://localhost:8080")

# All clients implement the same interface!
for client in [chroma_client]:  # Add others when available
    collections = client.list_collections()
    count = client.count("my_collection")
```

## Need Help?

If you encounter issues during migration:

1. Check that all imports work with the test script
2. Use backward compatibility imports initially 
3. Migrate one component at a time
4. The old files remain available during transition
5. Consult this guide for common patterns

The new structure is designed to make vector store operations clearer, more maintainable, and more powerful while preserving all existing functionality.