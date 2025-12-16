# ChromaDB Compaction Migration: Implementation Guide

**Status**: Implementation - Phase 2 (receipt_chroma integration)
**Created**: December 15, 2024
**Last Updated**: December 16, 2024

## Architecture Overview

This guide implements **two separate packages** to optimize Lambda deployment:

1. **`receipt_dynamo_stream`** (NEW) - Lightweight stream processing
   - ✅ Zip Lambda compatible (fast, cheap)
   - DynamoDB stream parsing and change detection

2. **`receipt_chroma`** (ENHANCED) - ChromaDB compaction operations
   - ⚠️ Container Lambda required (heavy dependencies)
   - Compaction operations and snapshot management

3. **Lambda Handlers** - AWS orchestration
   - Event routing and SQS integration

## Table of Contents

1. [Prerequisites](#prerequisites)
2. [Package Overview](#package-overview)
3. [Step-by-Step Implementation](#step-by-step-implementation)
4. [Verification Steps](#verification-steps)
5. [Troubleshooting](#troubleshooting)

## Prerequisites

### Environment Setup

```bash
# Ensure you're on the upload-refactor branch
git checkout upload-refactor

# Navigate to project root
cd /Users/tnorlund/Portfolio
```

### Required Tools

- Python 3.12
- pytest for testing
- virtualenv for isolated environments

## Package Overview

### receipt_dynamo_stream (NEW Package)

**Purpose**: Lightweight DynamoDB stream processing

**Characteristics**:
- ✅ Zip Lambda compatible (no heavy dependencies)
- ✅ Fast deployment (~5-10 seconds)
- ✅ Reusable across any stream processing service

**Dependencies**:
- `receipt_dynamo` (entities, constants)
- `boto3` (AWS SDK - included in Lambda runtime)

**Contains**:
- DynamoDB stream parsing
- Entity change detection
- Compaction run identification
- Stream message models

### receipt_chroma (Existing Package - Enhanced)

**Purpose**: ChromaDB operations and compaction logic

**Characteristics**:
- ⚠️ Container Lambda required (heavy dependencies)
- ⚠️ Slower deployment (~30-60 seconds)
- ✅ Full ChromaDB functionality

**Dependencies**:
- `chromadb` (vector database)
- `numpy`, `scipy` (heavy numerical libraries)
- `receipt_dynamo` (entities)
- `receipt_dynamo_stream` (message models)

**New Additions**:
- Compaction operations
- EFS snapshot management
- Delta merging

## Step-by-Step Implementation

> **Progress update (Dec 16, 2024):** Phase 1 is complete. The `receipt_dynamo_stream` package is scaffolded and installable, and stream parsing/change detection now live outside the Lambda. Continue with Phase 2 to lift the enhanced compactor business logic out of `infra/chromadb_compaction/lambdas/compaction/` into `receipt_chroma/compaction/` so the Lambda keeps only AWS wiring (env/config, boto3 clients, logging/metrics).

### Phase 1: Create receipt_dynamo_stream Package

#### Step 1.1: Create Package Structure

```bash
cd /Users/tnorlund/Portfolio

# Create new package directory
mkdir -p receipt_dynamo_stream
cd receipt_dynamo_stream

# Create Python package structure
mkdir -p receipt_dynamo_stream/parsing
mkdir -p receipt_dynamo_stream/change_detection
mkdir -p tests/unit
mkdir -p tests/integration

# Create __init__.py files
touch receipt_dynamo_stream/__init__.py
touch receipt_dynamo_stream/py.typed
touch receipt_dynamo_stream/parsing/__init__.py
touch receipt_dynamo_stream/change_detection/__init__.py
touch tests/__init__.py
```

#### Step 1.2: Create pyproject.toml

```bash
cat > pyproject.toml << 'EOF'
[build-system]
requires = ["setuptools>=68.0", "wheel"]
build-backend = "setuptools.build_meta"

[project]
name = "receipt-dynamo-stream"
version = "0.1.0"
description = "Lightweight DynamoDB stream processing for receipt system"
readme = "README.md"
requires-python = ">=3.12"
dependencies = [
    "boto3>=1.28.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.4.0",
    "pytest-cov>=4.1.0",
    "pytest-mock>=3.11.1",
    "black>=23.7.0",
    "mypy>=1.5.0",
    "pylint>=2.17.0",
]

[tool.setuptools.packages.find]
where = ["."]
include = ["receipt_dynamo_stream*"]

[tool.pytest.ini_options]
testpaths = ["tests"]
python_files = ["test_*.py"]
python_classes = ["Test*"]
python_functions = ["test_*"]

[tool.black]
line-length = 88
target-version = ['py312']

[tool.mypy]
python_version = "3.12"
warn_return_any = true
warn_unused_configs = true
EOF
```

#### Step 1.3: Create Package Files

**`receipt_dynamo_stream/models.py`**:

```python
"""Stream processing domain models."""

from dataclasses import dataclass
from typing import Any, Optional, Union

from receipt_dynamo.entities.receipt_metadata import ReceiptMetadata
from receipt_dynamo.entities.receipt_word_label import ReceiptWordLabel


@dataclass(frozen=True)
class FieldChange:
    """Field-level change in DynamoDB stream event."""
    old: Any
    new: Any


@dataclass(frozen=True)
class ParsedStreamRecord:
    """Parsed and typed DynamoDB stream record."""
    entity_type: str
    old_entity: Optional[Union[ReceiptMetadata, ReceiptWordLabel]]
    new_entity: Optional[Union[ReceiptMetadata, ReceiptWordLabel]]
    pk: str
    sk: str


@dataclass(frozen=True)
class StreamMessage:
    """Message for SQS compaction queue."""
    entity_type: str
    entity_data: dict
    changes: dict
    event_name: str
    collection: str
    record_snapshot: Optional[dict] = None
    source: str = "dynamodb_stream"
    timestamp: Optional[str] = None
    stream_record_id: Optional[str] = None


__all__ = ["FieldChange", "ParsedStreamRecord", "StreamMessage"]
```

#### Step 1.4: Copy Stream Processing Files

```bash
cd receipt_dynamo_stream

# Copy parsers
cp ../infra/chromadb_compaction/lambdas/processor/parsers.py \
   receipt_dynamo_stream/parsing/parsers.py

# Copy compaction run detector
cp ../infra/chromadb_compaction/lambdas/processor/compaction_run.py \
   receipt_dynamo_stream/parsing/compaction_run.py

# Copy change detector
cp ../infra/chromadb_compaction/lambdas/processor/change_detector.py \
   receipt_dynamo_stream/change_detection/detector.py
```

#### Step 1.5: Update Imports in receipt_dynamo_stream Files

**In `receipt_dynamo_stream/parsing/parsers.py`**:

```python
# Change:
from .models import ParsedStreamRecord

# To:
from receipt_dynamo_stream.models import ParsedStreamRecord
```

**In `receipt_dynamo_stream/change_detection/detector.py`**:

```python
# Change:
from .models import FieldChange

# To:
from receipt_dynamo_stream.models import FieldChange
```

#### Step 1.6: Create Package __init__.py

**`receipt_dynamo_stream/__init__.py`**:

```python
"""Lightweight DynamoDB stream processing package.

This package provides business logic for processing DynamoDB streams
without heavy dependencies, making it suitable for zip-based Lambda deployment.
"""

__version__ = "0.1.0"

from receipt_dynamo_stream.models import (
    FieldChange,
    ParsedStreamRecord,
    StreamMessage,
)

from receipt_dynamo_stream.parsing.parsers import (
    detect_entity_type,
    parse_entity,
    parse_stream_record,
)

from receipt_dynamo_stream.parsing.compaction_run import (
    is_compaction_run,
    parse_compaction_run,
)

from receipt_dynamo_stream.change_detection.detector import (
    get_chromadb_relevant_changes,
    CHROMADB_RELEVANT_FIELDS,
)

__all__ = [
    "__version__",
    # Models
    "FieldChange",
    "ParsedStreamRecord",
    "StreamMessage",
    # Parsing
    "detect_entity_type",
    "parse_entity",
    "parse_stream_record",
    "is_compaction_run",
    "parse_compaction_run",
    # Change detection
    "get_chromadb_relevant_changes",
    "CHROMADB_RELEVANT_FIELDS",
]
```

**`receipt_dynamo_stream/parsing/__init__.py`**:

```python
"""DynamoDB stream parsing utilities."""

from receipt_dynamo_stream.parsing.parsers import (
    detect_entity_type,
    parse_entity,
    parse_stream_record,
)

from receipt_dynamo_stream.parsing.compaction_run import (
    is_compaction_run,
    parse_compaction_run,
)

__all__ = [
    "detect_entity_type",
    "parse_entity",
    "parse_stream_record",
    "is_compaction_run",
    "parse_compaction_run",
]
```

**`receipt_dynamo_stream/change_detection/__init__.py`**:

```python
"""Field change detection for ChromaDB-relevant fields."""

from receipt_dynamo_stream.change_detection.detector import (
    get_chromadb_relevant_changes,
    CHROMADB_RELEVANT_FIELDS,
)

__all__ = [
    "get_chromadb_relevant_changes",
    "CHROMADB_RELEVANT_FIELDS",
]
```

#### Step 1.7: Install receipt_dynamo_stream

```bash
# From receipt_dynamo_stream directory
pip install -e .

# Verify installation
python -c "import receipt_dynamo_stream; print(receipt_dynamo_stream.__version__)"
```

### Phase 2: Update receipt_chroma Package

#### Step 2.1: Create Compaction Module

```bash
cd ../receipt_chroma/receipt_chroma

# Create compaction module
mkdir -p compaction
touch compaction/__init__.py
```

#### Step 2.2: Copy Compaction Files

```bash
# Copy operations
cp ../../infra/chromadb_compaction/lambdas/compaction/operations.py \
   compaction/operations.py

# Copy compaction run logic
cp ../../infra/chromadb_compaction/lambdas/compaction/compaction_run.py \
   compaction/compaction_run.py

# Copy EFS manager
cp ../../infra/chromadb_compaction/lambdas/compaction/efs_snapshot_manager.py \
   compaction/efs_snapshot_manager.py
```

#### Step 2.3: Create Compaction Models

**`receipt_chroma/compaction/models.py`**:

```python
"""Compaction operation result models."""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional


@dataclass(frozen=True)
class MetadataUpdateResult:
    """Result from processing a metadata update."""
    database: str
    collection: str
    updated_count: int
    image_id: str
    receipt_id: int
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "database": self.database,
            "collection": self.collection,
            "updated_count": self.updated_count,
            "image_id": self.image_id,
            "receipt_id": self.receipt_id,
        }
        if self.error:
            result["error"] = self.error
        return result


@dataclass(frozen=True)
class LabelUpdateResult:
    """Result from processing a label update."""
    chromadb_id: str
    updated_count: int
    event_name: str
    changes: List[str]
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        result = {
            "chromadb_id": self.chromadb_id,
            "updated_count": self.updated_count,
            "event_name": self.event_name,
            "changes": self.changes,
        }
        if self.error:
            result["error"] = self.error
        return result


__all__ = ["MetadataUpdateResult", "LabelUpdateResult"]
```

#### Step 2.4: Create Compaction __init__.py

**`receipt_chroma/compaction/__init__.py`**:

```python
"""ChromaDB compaction operations (container Lambda required)."""

from receipt_chroma.compaction.operations import (
    update_receipt_metadata,
    remove_receipt_metadata,
    update_word_labels,
    remove_word_labels,
    reconstruct_label_metadata,
)

from receipt_chroma.compaction.compaction_run import (
    merge_compaction_deltas,
    process_compaction_runs,
)

from receipt_chroma.compaction.efs_snapshot_manager import (
    EFSSnapshotManager,
    get_efs_snapshot_manager,
)

from receipt_chroma.compaction.models import (
    MetadataUpdateResult,
    LabelUpdateResult,
)

__all__ = [
    "update_receipt_metadata",
    "remove_receipt_metadata",
    "update_word_labels",
    "remove_word_labels",
    "reconstruct_label_metadata",
    "merge_compaction_deltas",
    "process_compaction_runs",
    "EFSSnapshotManager",
    "get_efs_snapshot_manager",
    "MetadataUpdateResult",
    "LabelUpdateResult",
]
```

#### Step 2.5: Update Main receipt_chroma __init__.py

Add to existing `receipt_chroma/__init__.py`:

```python
# Add after existing imports:

# Compaction operations
from receipt_chroma.compaction import (
    update_receipt_metadata,
    remove_receipt_metadata,
    update_word_labels,
    remove_word_labels,
    merge_compaction_deltas,
    EFSSnapshotManager,
    MetadataUpdateResult,
    LabelUpdateResult,
)

# Update __all__ to include new exports
```

#### Step 2.6: Reinstall receipt_chroma

```bash
cd ..  # Back to receipt_chroma root
pip install -e .

# Verify
python -c "from receipt_chroma.compaction import update_receipt_metadata; print('✓')"
```

#### Step 2.7: Move Remaining Compactor Business Logic Out of Lambda

Goal: keep `enhanced_compaction_handler.py` focused on AWS wiring and observability while `receipt_chroma` owns compaction logic.

- Move the non-AWS helpers from `infra/chromadb_compaction/lambdas/compaction/` into `receipt_chroma/compaction/`:
  - `metadata_handler.py`, `label_handler.py`, `message_builder.py`, `receipt_handler.py`
  - Re-export any callable helpers needed by the handler (e.g., `process_metadata_updates`, `process_label_updates`, `process_compaction_run_messages`, `apply_*_in_memory`)
- Update imports in `enhanced_compaction_handler.py` to pull these from `receipt_chroma.compaction` and only keep:
  - AWS clients/env loading
  - SQS batching/polling
  - Observability wrappers (metrics, traces, logging setup)
- Ensure `receipt_chroma` depends on `receipt_dynamo_stream` for the shared `StreamMessage` model to decode SQS payloads used in the compactor path.

### Phase 3: Update Lambda Handlers

#### Step 3.1: Update Stream Processor Lambda

**File**: `infra/chromadb_compaction/lambdas/stream_processor.py`

Change imports from:
```python
from processor import (
    parse_stream_record,
    get_chromadb_relevant_changes,
    build_messages_from_records,
    publish_messages,
)
```

To:
```python
# Import business logic from receipt_dynamo_stream
from receipt_dynamo_stream import (
    parse_stream_record,
    get_chromadb_relevant_changes,
)

# Keep AWS-specific orchestration local
from processor import (
    build_messages_from_records,
    publish_messages,
)
```

#### Step 3.2: Update Compaction Handler Lambda

**File**: `infra/chromadb_compaction/lambdas/enhanced_compaction_handler.py`

Change imports from:
```python
from compaction import (
    merge_compaction_deltas,
    get_efs_snapshot_manager,
    update_receipt_metadata,
    # ...
)
```

To:
```python
# Import business logic from receipt_chroma
from receipt_chroma.compaction import (
    merge_compaction_deltas,
    get_efs_snapshot_manager,
    update_receipt_metadata,
    remove_receipt_metadata,
    update_word_labels,
    remove_word_labels,
    MetadataUpdateResult,
    LabelUpdateResult,
)

# Import message models from receipt_dynamo_stream
from receipt_dynamo_stream import StreamMessage

# Keep orchestration logic local
from compaction import (
    process_sqs_messages,
    categorize_stream_messages,
    group_messages_by_collection,
    apply_metadata_updates_in_memory,
    apply_label_updates_in_memory,
)
```

#### Step 3.3: Update Orchestration Wrappers

**In `compaction/metadata_handler.py`**:

```python
# Change:
from .operations import update_receipt_metadata, remove_receipt_metadata

# To:
from receipt_chroma.compaction import update_receipt_metadata, remove_receipt_metadata
```

**In `compaction/label_handler.py`**:

```python
# Change:
from .operations import update_word_labels, remove_word_labels

# To:
from receipt_chroma.compaction import update_word_labels, remove_word_labels
```

### Phase 4: Update Lambda Deployment Configuration

#### Step 4.1: Update Stream Processor Dockerfile (Zip Lambda)

**File**: `infra/chromadb_compaction/stream_processor_dockerfile` (if using zip)

```dockerfile
# Ensure receipt_dynamo_stream is installed
COPY receipt_dynamo_stream /tmp/receipt_dynamo_stream
RUN pip install --no-cache-dir /tmp/receipt_dynamo_stream

# Note: receipt_chroma is NOT needed for stream processor
```

#### Step 4.2: Update Compaction Handler Dockerfile (Container Lambda)

**File**: `infra/chromadb_compaction/lambdas/Dockerfile`

```dockerfile
# Install both packages
COPY receipt_dynamo_stream /tmp/receipt_dynamo_stream
COPY receipt_chroma /tmp/receipt_chroma

RUN pip install --no-cache-dir /tmp/receipt_dynamo_stream
RUN pip install --no-cache-dir /tmp/receipt_chroma
```

## Verification Steps

### Verify receipt_dynamo_stream

```bash
cd /Users/tnorlund/Portfolio/receipt_dynamo_stream

# Test imports
python << EOF
from receipt_dynamo_stream import (
    parse_stream_record,
    get_chromadb_relevant_changes,
    FieldChange,
    StreamMessage,
)
print("✓ All imports successful")
EOF

# Run tests
pytest tests/ -v
```

### Verify receipt_chroma

```bash
cd /Users/tnorlund/Portfolio/receipt_chroma

# Test imports
python << EOF
from receipt_chroma.compaction import (
    update_receipt_metadata,
    merge_compaction_deltas,
    EFSSnapshotManager,
)
print("✓ All imports successful")
EOF

# Run tests
pytest tests/ -v
```

### Verify Lambda Handlers

```bash
cd /Users/tnorlund/Portfolio/infra/chromadb_compaction/lambdas

# Test stream processor imports
python -c "from stream_processor import lambda_handler; print('✓ Stream processor OK')"

# Test compaction handler imports
python -c "from enhanced_compaction_handler import lambda_handler; print('✓ Compaction handler OK')"
```

## Troubleshooting

### ImportError: No module named 'receipt_dynamo_stream'

**Solution**:
```bash
cd receipt_dynamo_stream
pip install -e .
```

### Stream Processor Still Importing from Local processor/

**Solution**: Update imports in `stream_processor.py` to use `receipt_dynamo_stream`

### Compaction Handler Cannot Find receipt_chroma.compaction

**Solution**:
```bash
cd receipt_chroma
pip install -e .
```

### Circular Import Between Packages

**Solution**: Ensure proper dependency flow:
- `receipt_dynamo_stream` → `receipt_dynamo` (entities)
- `receipt_chroma` → `receipt_dynamo_stream` (models only)
- `receipt_chroma` → `receipt_dynamo` (entities)

## Deployment Checklist

Before deploying:

- [ ] `receipt_dynamo_stream` package created
- [ ] `receipt_dynamo_stream` tests passing
- [ ] `receipt_chroma` compaction module created
- [ ] `receipt_chroma` tests passing
- [ ] Stream processor imports updated
- [ ] Compaction handler imports updated
- [ ] Lambda Dockerfiles updated
- [ ] Local verification complete
- [ ] Ready for dev deployment

## Next Steps

1. **Deploy to Dev**: Follow deployment guide
2. **Monitor**: Check CloudWatch metrics
3. **Test E2E**: Trigger stream events and verify compaction
4. **Document**: Update package READMEs

## References

- [receipt_dynamo_stream Package](../../receipt_dynamo_stream/)
- [receipt_chroma Package](../../receipt_chroma/)
- [Lambda Handlers](../../infra/chromadb_compaction/lambdas/)
