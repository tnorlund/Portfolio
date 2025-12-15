# ChromaDB Compaction Migration: File Mapping

**Status**: Planning
**Created**: December 15, 2024
**Last Updated**: December 15, 2024

## Overview

This document provides a detailed mapping of files being migrated from Lambda handlers to the `receipt_chroma` package.

## File Movement Summary

### Files Moving to receipt_dynamo_stream (NEW Package)

| Source (Lambda) | Destination (receipt_dynamo_stream) | Lines | Status |
|----------------|-------------------------------------|-------|--------|
| `processor/parsers.py` | `parsing/parsers.py` | ~211 | ✅ Ready to move |
| `processor/change_detector.py` | `change_detection/detector.py` | ~65 | ✅ Ready to move |
| `processor/compaction_run.py` | `parsing/compaction_run.py` | ~100 | ✅ Ready to move |
| `processor/models.py` (partial) | `models.py` | ~72 | ✅ Extract models |

**Subtotal for receipt_dynamo_stream**: ~448 lines

### Files Moving to receipt_chroma

| Source (Lambda) | Destination (receipt_chroma) | Lines | Status |
|----------------|------------------------------|-------|--------|
| `compaction/operations.py` | `compaction/operations.py` | ~956 | ✅ Ready to move |
| `compaction/compaction_run.py` | `compaction/compaction_run.py` | ~583 | ✅ Ready to move |
| `compaction/efs_snapshot_manager.py` | `compaction/efs_snapshot_manager.py` | ~360 | ✅ Ready to move |
| `compaction/models.py` (partial) | `compaction/models.py` | ~114 | ✅ Extract models |

**Subtotal for receipt_chroma**: ~2,013 lines

**Total Lines Moving**: ~2,461 lines of business logic

### Files Staying in Lambda

| File | Purpose | Lines | Reason to Keep |
|------|---------|-------|----------------|
| `enhanced_compaction_handler.py` | Lambda orchestration | ~1,037 | AWS Lambda-specific |
| `stream_processor.py` | Lambda orchestration | ~333 | AWS Lambda-specific |
| `compaction/metadata_handler.py` | Orchestration wrapper | ~406 | AWS orchestration |
| `compaction/label_handler.py` | Orchestration wrapper | ~396 | AWS orchestration |
| `compaction/message_builder.py` | SQS message handling | ~200 | AWS SQS-specific |
| `processor/message_builder.py` | Message construction | ~150 | AWS-specific |
| `processor/sqs_publisher.py` | SQS publishing | ~100 | AWS SQS-specific |
| `utils/` (various) | Observability | ~500 | AWS CloudWatch-specific |

**Total Lines Remaining in Lambda**: ~3,122 lines (down from ~5,583)

## Detailed File Analysis

### 1. `compaction/operations.py`

**Purpose**: Core ChromaDB update/remove operations

**Functions Moving**:
```python
update_receipt_metadata(collection, image_id, receipt_id, changes, ...)
  → Pure ChromaDB operation, queries DynamoDB for entity list
  → Dependencies: receipt_dynamo.DynamoClient, logging

remove_receipt_metadata(collection, image_id, receipt_id, ...)
  → Pure ChromaDB operation, deletes embeddings
  → Dependencies: receipt_dynamo.DynamoClient, logging

update_word_labels(collection, chromadb_id, changes, ...)
  → Pure ChromaDB operation, updates single word label
  → Dependencies: None (pure logic)

remove_word_labels(collection, chromadb_id, ...)
  → Pure ChromaDB operation, removes single word label
  → Dependencies: None (pure logic)

reconstruct_label_metadata(record_snapshot, entity_data)
  → Pure domain logic, reconstructs metadata dictionary
  → Dependencies: None (pure logic)
```

**Import Changes Needed**:
```python
# No changes - already uses receipt_dynamo
```

**Why Move**: Pure business logic, reusable outside Lambda

---

### 2. `compaction/compaction_run.py`

**Purpose**: Delta merging and compaction run processing

**Functions Moving**:
```python
process_compaction_runs(compaction_runs, collection, logger, ...)
  → Orchestrates delta merging for multiple runs
  → Dependencies: receipt_chroma (ChromaClient, S3), boto3, receipt_dynamo

merge_compaction_deltas(chroma_client, compaction_runs, collection, ...)
  → Pure ChromaDB operation, merges deltas into snapshot
  → Dependencies: receipt_chroma (ChromaClient)

_download_and_extract_delta(s3_client, bucket, delta_prefix, ...)
  → S3 and filesystem operations
  → Dependencies: boto3, tarfile

_merge_collection_from_delta(...)
  → Pure ChromaDB operation, copies collection data
  → Dependencies: receipt_chroma (ChromaClient)
```

**Import Changes Needed**:
```python
# No changes - already uses receipt_chroma
```

**Why Move**: Core compaction business logic, reusable

---

### 3. `compaction/efs_snapshot_manager.py`

**Purpose**: EFS snapshot caching and management

**Class Moving**:
```python
class EFSSnapshotManager:
    """Manages ChromaDB snapshots using EFS + S3 hybrid approach."""

    Methods:
    - get_current_efs_version()
    - set_efs_version()
    - get_latest_s3_version()
    - download_snapshot_to_efs()
    - ensure_snapshot_available()
    - cleanup_old_snapshots()
    - sync_to_s3()

get_efs_snapshot_manager(collection, logger, metrics)
  → Factory function for creating manager instances
```

**Import Changes Needed**:
```python
# No changes - already uses receipt_chroma.s3
```

**Why Move**: Infrastructure for ChromaDB, not Lambda-specific

---

## New Package: receipt_dynamo_stream

### Overview

A new lightweight package for DynamoDB stream processing that can be deployed in zip-based Lambda functions.

**Key Characteristics**:
- ✅ **Lightweight**: No heavy dependencies (no ChromaDB, no numpy)
- ✅ **Zip Lambda Compatible**: Can be packaged in standard Lambda deployment
- ✅ **Focused**: Only stream processing and change detection
- ✅ **Reusable**: Can be used by any service processing DynamoDB streams

**Dependencies**:
```
receipt_dynamo_stream/
├── receipt_dynamo (entities, constants)
└── boto3 (standard AWS SDK)
```

**Package Structure**:
```
receipt_dynamo_stream/
├── __init__.py
├── py.typed
├── parsing/
│   ├── __init__.py
│   ├── parsers.py          # DynamoDB stream parsing
│   └── compaction_run.py   # Compaction run detection
├── change_detection/
│   ├── __init__.py
│   └── detector.py         # Field change detection
└── models.py               # Stream domain models
```

---

### Files Moving to receipt_dynamo_stream

#### 1. `processor/parsers.py` → `receipt_dynamo_stream/parsing/parsers.py`

**Purpose**: DynamoDB stream record parsing

**Functions Moving**:
```python
detect_entity_type(sk: str) -> Optional[str]
  → Detects entity type from DynamoDB sort key pattern
  → Dependencies: None (pure logic)

parse_entity(image, entity_type, image_type, pk, sk, metrics) -> Entity
  → Parses DynamoDB JSON image into typed entity
  → Dependencies: receipt_dynamo entities

parse_stream_record(record, metrics) -> ParsedStreamRecord
  → Full DynamoDB stream record parsing
  → Dependencies: receipt_dynamo entities, local parse_entity
```

**Import Changes Needed**:
```python
# Change:
from .models import ParsedStreamRecord

# To:
from receipt_dynamo_stream.models import ParsedStreamRecord
```

**Why Move**: DynamoDB stream parsing is reusable, doesn't need ChromaDB

#### 2. `processor/compaction_run.py` → `receipt_dynamo_stream/parsing/compaction_run.py`

**Purpose**: Compaction run message parsing

**Functions Moving**:
```python
is_compaction_run(pk: str, sk: str) -> bool
  → Detects if record is a compaction run
  → Dependencies: None (pure logic)

parse_compaction_run(record) -> Optional[Dict]
  → Parses compaction run from stream record
  → Dependencies: None (DynamoDB JSON parsing)
```

**Import Changes Needed**: None (standalone functions)

**Why Move**: Reusable compaction run detection logic

#### 3. `processor/change_detector.py` → `receipt_dynamo_stream/change_detection/detector.py`

**Purpose**: Detect ChromaDB-relevant field changes

**Functions Moving**:
```python
get_chromadb_relevant_changes(entity_type, old_entity, new_entity)
  → Pure domain logic, compares entity fields
  → Dependencies: receipt_dynamo entities

CHROMADB_RELEVANT_FIELDS (constant)
  → Configuration dictionary
```

**Import Changes Needed**:
```python
# Change:
from .models import FieldChange

# To:
from receipt_chroma.stream.models import FieldChange
```

**Why Move**: Pure domain logic, reusable for any stream processing

---

### 5. Domain Models

#### `compaction/models.py` → `receipt_chroma/compaction/models.py`

**Models Moving**:
```python
@dataclass
class StreamMessage:
    """Parsed stream message for compaction."""
    # Domain type - represents a compaction operation

@dataclass
class MetadataUpdateResult:
    """Result from processing a metadata update."""
    # Domain type - represents operation result

@dataclass
class LabelUpdateResult:
    """Result from processing a label update."""
    # Domain type - represents operation result
```

**Models Staying in Lambda**:
```python
@dataclass
class LambdaResponse:
    """Lambda handler response."""
    # AWS Lambda-specific - stays in both places
```

**Why Move**: Domain types belong with business logic

#### `processor/models.py` → `receipt_chroma/stream/models.py`

**Models Moving**:
```python
@dataclass
class FieldChange:
    """Represents a change in a single field."""
    # Domain type - represents a field-level change

@dataclass
class ParsedStreamRecord:
    """Parsed DynamoDB stream record."""
    # Domain type - represents parsed stream data
```

**Models Staying in Lambda**:
```python
@dataclass
class LambdaResponse:
    """Lambda handler response."""
    # AWS Lambda-specific

class ChromaDBCollection(Enum):
    """Collection enumeration."""
    # Should move to receipt_dynamo.constants (already exists there)
```

**Why Move**: Domain types used in business logic

---

## Files NOT Moving (Lambda Orchestration)

### Lambda Handlers

#### `enhanced_compaction_handler.py`

**Responsibilities (Keep in Lambda)**:
- Lambda event routing (`lambda_handler`)
- AWS observability (CloudWatch, EMF metrics)
- MetricsAccumulator class (AWS-specific)
- Error handling and response formatting
- Lock management orchestration
- EFS vs S3 mode selection
- Phase-based processing (download, process, upload)

**Updated Imports**:
```python
# OLD:
from compaction import (
    update_receipt_metadata,
    merge_compaction_deltas,
    # ...
)

# NEW:
from compaction import (
    # Keep orchestration functions
    process_sqs_messages,
    categorize_stream_messages,
    # ...
)
from receipt_chroma.compaction import (
    # Import business logic
    update_receipt_metadata,
    merge_compaction_deltas,
    # ...
)
```

#### `stream_processor.py`

**Responsibilities (Keep in Lambda)**:
- Lambda event routing (`lambda_handler`)
- AWS observability (CloudWatch, EMF metrics)
- Batch processing and truncation
- Timeout protection
- Circuit breaker logic
- Error tracking and reporting

**Updated Imports**:
```python
# Keep local imports for AWS-specific code
from processor import (
    build_messages_from_records,
    publish_messages,
    # ...
)
```

---

### Orchestration Wrappers

#### `compaction/metadata_handler.py`

**Responsibilities (Keep in Lambda)**:
- Snapshot download/upload orchestration
- Lock management integration
- Metrics collection for AWS
- Error handling and retry logic
- SQS receipt handle management

**Core Logic (Delegates to receipt_chroma)**:
```python
# Calls receipt_chroma for actual work
from receipt_chroma.compaction import update_receipt_metadata

def process_metadata_updates(...):
    # Download snapshot (orchestration)
    # Call update_receipt_metadata (business logic)
    # Upload snapshot (orchestration)
    # Track metrics (AWS-specific)
```

#### `compaction/label_handler.py`

**Responsibilities (Keep in Lambda)**:
- Similar orchestration to metadata_handler
- Label-specific workflow

---

### AWS-Specific Modules

#### `processor/parsers.py`

**Why Keep in Lambda**:
- Parses DynamoDB stream record format (AWS-specific)
- Handles DynamoDB JSON format conversion
- Entity type detection from DynamoDB keys

**Functions**:
```python
detect_entity_type(sk: str) -> Optional[str]
  → Parses DynamoDB SK pattern

parse_entity(image, entity_type, ...) -> Entity
  → Converts DynamoDB JSON to typed entity

parse_stream_record(record) -> ParsedStreamRecord
  → Full DynamoDB stream record parsing
```

#### `processor/sqs_publisher.py`

**Why Keep in Lambda**:
- AWS SQS client management
- Message batching for SQS limits
- Error handling for SQS failures

**Functions**:
```python
publish_messages(messages, metrics) -> int
  → Publishes to SQS queues
```

---

## Import Dependency Graph

### Before Migration

```
enhanced_compaction_handler.py (container Lambda)
├── compaction/
│   ├── operations.py (business logic)
│   ├── compaction_run.py (business logic)
│   ├── efs_snapshot_manager.py (business logic)
│   ├── metadata_handler.py (orchestration)
│   ├── label_handler.py (orchestration)
│   └── message_builder.py (orchestration)
├── receipt_chroma (package)
│   ├── ChromaClient
│   └── LockManager
└── receipt_dynamo (package)
    └── DynamoClient

stream_processor.py (zip Lambda)
├── processor/
│   ├── parsers.py (business logic)
│   ├── change_detector.py (business logic)
│   ├── compaction_run.py (business logic)
│   ├── message_builder.py (AWS-specific)
│   └── sqs_publisher.py (AWS-specific)
└── receipt_dynamo (package)
```

### After Migration (Three-Package Architecture)

```
enhanced_compaction_handler.py (container Lambda)
├── compaction/
│   ├── metadata_handler.py (orchestration)
│   ├── label_handler.py (orchestration)
│   └── message_builder.py (orchestration)
├── receipt_chroma (package)  ← Heavy deps, container only
│   ├── ChromaClient
│   ├── LockManager
│   └── compaction/  ← NEW
│       ├── operations.py (business logic)
│       ├── compaction_run.py (business logic)
│       └── efs_snapshot_manager.py (business logic)
├── receipt_dynamo_stream (package)  ← NEW, lightweight
│   └── models (for message types)
└── receipt_dynamo (package)

stream_processor.py (zip Lambda)  ← Stays lightweight!
├── processor/
│   ├── message_builder.py (AWS-specific)
│   └── sqs_publisher.py (AWS-specific)
├── receipt_dynamo_stream (package)  ← NEW, lightweight
│   ├── parsing/
│   │   ├── parsers.py (business logic)
│   │   └── compaction_run.py (business logic)
│   ├── change_detection/
│   │   └── detector.py (business logic)
│   └── models (domain types)
└── receipt_dynamo (package)

Package Dependency Flow:
stream_processor (zip) → receipt_dynamo_stream → receipt_dynamo
                              ↓ (via SQS)
compaction_handler (container) → receipt_chroma → receipt_dynamo
                               → receipt_dynamo_stream (models only)
```

---

## Migration Order

Recommended order to minimize risk:

1. **Phase 1**: Create structure and models
   - Create `receipt_chroma/compaction/` and `receipt_chroma/stream/`
   - Create model files
   - Update package `__init__.py` files

2. **Phase 2**: Move independent modules
   - Move `change_detector.py` (no dependencies on other moved files)
   - Move `efs_snapshot_manager.py` (minimal dependencies)

3. **Phase 3**: Move core operations
   - Move `operations.py` (used by handlers)
   - Move `compaction_run.py` (uses operations)

4. **Phase 4**: Update Lambda imports
   - Update `metadata_handler.py` imports
   - Update `label_handler.py` imports
   - Update main handler imports

5. **Phase 5**: Test and validate
   - Run unit tests
   - Run integration tests
   - Deploy to dev environment

---

## Size Comparison

| Category | Lines of Code | Percentage |
|----------|--------------|------------|
| **Moving to receipt_chroma** | ~2,150 | 39% |
| **Staying in Lambda (orchestration)** | ~1,800 | 33% |
| **Staying in Lambda (AWS-specific)** | ~1,533 | 28% |
| **Total Lambda Code** | ~5,483 | 100% |

After migration:
- `receipt_chroma` grows by ~2,150 lines (business logic)
- Lambda shrinks by ~2,150 lines (keeps orchestration/AWS-specific)
- **Separation achieved**: Business logic separate from infrastructure

---

## Verification Matrix

| File | Lines Moving | Import Changes | Test Changes | Risk Level |
|------|-------------|----------------|--------------|------------|
| `operations.py` | 956 | None | Update paths | Medium |
| `compaction_run.py` | 583 | None | Update paths | Medium |
| `efs_snapshot_manager.py` | 360 | None | Update paths | Low |
| `change_detector.py` | 65 | 1 import | Update paths | Low |
| `models.py` (compaction) | 114 | None | New tests | Low |
| `models.py` (stream) | 72 | None | New tests | Low |
| Lambda handlers | 0 | Multiple | None | Medium |

**Overall Risk**: Medium - managed through incremental testing

---

## Success Metrics

After migration, we should see:

1. **Code Organization**:
   - ✓ All ChromaDB business logic in `receipt_chroma`
   - ✓ All AWS orchestration in Lambda handlers
   - ✓ Clear import boundaries

2. **Reusability**:
   - ✓ `receipt_chroma.compaction` can be imported by other projects
   - ✓ Operations work without Lambda context
   - ✓ No AWS dependencies in business logic

3. **Testability**:
   - ✓ Unit tests don't require AWS mocking
   - ✓ Integration tests use real ChromaDB
   - ✓ E2E tests only mock AWS services

4. **Maintainability**:
   - ✓ Changes to business logic happen in package
   - ✓ Changes to AWS integration happen in Lambda
   - ✓ Clear ownership of each module

