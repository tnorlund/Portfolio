# ChromaDB Compaction Migration: Quick Reference

**Quick access guide for common commands and patterns during migration.**

## Two-Package Architecture

We're creating/enhancing **two packages**:
- **`receipt_dynamo_stream`** (NEW) - Lightweight stream processing (zip Lambda)
- **`receipt_chroma`** (ENHANCED) - ChromaDB operations (container Lambda)

Plus orchestration code in Lambda handlers (not a package)

## Essential Commands

### Setup

```bash
# Create receipt_dynamo_stream package
cd /Users/tnorlund/Portfolio
mkdir -p receipt_dynamo_stream/receipt_dynamo_stream/{parsing,change_detection}
cd receipt_dynamo_stream
python -m venv venv
source venv/bin/activate
pip install -e .

# Update receipt_chroma package
cd ../receipt_chroma
source venv/bin/activate
mkdir -p receipt_chroma/compaction
pip install -e .

# Install receipt_dynamo
pip install -e ../receipt_dynamo
```

### File Operations

```bash
# Copy files to package
cp ../../infra/chromadb_compaction/lambdas/compaction/operations.py \
   receipt_chroma/compaction/

cp ../../infra/chromadb_compaction/lambdas/compaction/compaction_run.py \
   receipt_chroma/compaction/

cp ../../infra/chromadb_compaction/lambdas/compaction/efs_snapshot_manager.py \
   receipt_chroma/compaction/

cp ../../infra/chromadb_compaction/lambdas/processor/change_detector.py \
   receipt_chroma/stream/
```

### Testing

```bash
# Run all tests
pytest tests/ -v

# Run with coverage
pytest tests/ --cov=receipt_chroma --cov-report=html

# Run specific test file
pytest tests/unit/test_compaction_operations.py -v

# Run specific test
pytest tests/unit/test_compaction_operations.py::TestUpdateReceiptMetadata::test_update_metadata_for_words_collection -v

# View coverage
open htmlcov/index.html
```

### Verification

```bash
# Test imports
python -c "from receipt_chroma.compaction import update_receipt_metadata; print('✓')"
python -c "from receipt_chroma.stream import get_chromadb_relevant_changes; print('✓')"

# Check for migration imports in Lambda
cd ../../infra/chromadb_compaction/lambdas/
grep -r "receipt_chroma.compaction" .
grep -r "receipt_chroma.stream" .

# Type check (optional)
cd ../../../receipt_chroma
mypy receipt_chroma/compaction/ receipt_chroma/stream/
```

### Deployment

```bash
# Deploy to dev
cd infra/
pulumi up --stack dev --yes

# View logs
pulumi logs --follow --since 5m

# Check deployment status
pulumi stack output
```

## Import Patterns

### In receipt_chroma Package Files

```python
# compaction/operations.py - no changes needed
from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.data.dynamo_client import DynamoClient

# compaction/models.py
from receipt_dynamo.constants import ChromaDBCollection

# stream/change_detector.py
# Change:
from .models import FieldChange
# To:
from receipt_chroma.stream.models import FieldChange
```

### In Lambda Handler Files

```python
# enhanced_compaction_handler.py
# Change:
from compaction import merge_compaction_deltas, get_efs_snapshot_manager

# To:
from receipt_chroma.compaction import merge_compaction_deltas, get_efs_snapshot_manager

# Keep local orchestration imports:
from compaction import process_sqs_messages, categorize_stream_messages
```

### In Package __init__.py Files

```python
# receipt_chroma/compaction/__init__.py
from receipt_chroma.compaction.operations import (
    update_receipt_metadata,
    remove_receipt_metadata,
    # ...
)

# receipt_chroma/__init__.py
from receipt_chroma.compaction import (
    update_receipt_metadata,
    # ...
)
```

## File Locations

### What Moves to receipt_dynamo_stream (NEW Package)

| Source | Destination |
|--------|-------------|
| `lambdas/processor/parsers.py` | `receipt_dynamo_stream/parsing/parsers.py` |
| `lambdas/processor/compaction_run.py` | `receipt_dynamo_stream/parsing/compaction_run.py` |
| `lambdas/processor/change_detector.py` | `receipt_dynamo_stream/change_detection/detector.py` |
| Model classes (partial) | `receipt_dynamo_stream/models.py` |

### What Moves to receipt_chroma

| Source | Destination |
|--------|-------------|
| `lambdas/compaction/operations.py` | `receipt_chroma/compaction/operations.py` |
| `lambdas/compaction/compaction_run.py` | `receipt_chroma/compaction/compaction_run.py` |
| `lambdas/compaction/efs_snapshot_manager.py` | `receipt_chroma/compaction/efs_snapshot_manager.py` |
| Model classes (partial) | `receipt_chroma/compaction/models.py` |

### What Stays in Lambda

- `enhanced_compaction_handler.py` - Lambda orchestration (container)
- `stream_processor.py` - Lambda orchestration (zip)
- `compaction/metadata_handler.py` - Orchestration wrapper
- `compaction/label_handler.py` - Orchestration wrapper
- `compaction/message_builder.py` - SQS handling
- `processor/message_builder.py` - Message construction
- `processor/sqs_publisher.py` - SQS publishing

## Common Issues

### Import Error After Move

```
ImportError: cannot import name 'update_receipt_metadata' from 'receipt_chroma.compaction'
```

**Fix:**
```bash
cd receipt_chroma
pip install -e .
```

### Circular Import

```
ImportError: cannot import name 'X' from partially initialized module
```

**Fix:**
- Check for circular dependencies
- Move imports to function level if needed
- Ensure `__init__.py` imports in correct order

### Module Not Found

```
ModuleNotFoundError: No module named 'receipt_dynamo'
```

**Fix:**
```bash
cd receipt_dynamo
pip install -e .
```

### Tests Failing After Import Update

**Fix:**
- Update test imports to match new locations
- Update mock paths in tests
- Ensure fixtures still work with new structure

## Test Templates

### Unit Test Template

```python
import pytest
from unittest.mock import Mock, MagicMock
from receipt_chroma.compaction.operations import update_receipt_metadata

def test_update_metadata_basic():
    # Arrange
    mock_collection = MagicMock()
    mock_collection.name = "words"
    mock_logger = Mock()

    # Act
    result = update_receipt_metadata(
        collection=mock_collection,
        image_id="test",
        receipt_id=1,
        changes={"merchant_name": "Test"},
        logger=mock_logger,
    )

    # Assert
    assert result >= 0
```

### Integration Test Template

```python
import pytest
import tempfile
from receipt_chroma import ChromaClient
from receipt_chroma.compaction.operations import update_receipt_metadata

@pytest.fixture
def temp_chroma():
    temp_dir = tempfile.mkdtemp()
    client = ChromaClient(persist_directory=temp_dir, mode="delta")
    yield client
    client.close()

def test_update_with_real_chromadb(temp_chroma):
    # Setup
    collection = temp_chroma.get_or_create_collection("words")

    # Test
    # ... your test code ...
```

## Rollback Quick Reference

### Quick Rollback (Lambda Only)

```bash
cd infra/chromadb_compaction/lambdas/

# Revert files
git checkout HEAD~1 enhanced_compaction_handler.py
git checkout HEAD~1 compaction/metadata_handler.py
git checkout HEAD~1 compaction/label_handler.py

# Redeploy
cd ../../
pulumi up --yes
```

### Full Rollback

```bash
# Create rollback branch
git branch migration-rollback-$(date +%Y%m%d)

# Revert commits (use actual commit hashes)
git revert <commit-4>
git revert <commit-3>
git revert <commit-2>
git revert <commit-1>

# Remove package changes
cd receipt_chroma
rm -rf receipt_chroma/compaction/ receipt_chroma/stream/
pip install -e .

# Deploy
cd ../infra
pulumi up --yes
```

## Verification Checklist

Quick checklist during implementation:

```bash
# ✓ Files copied
ls receipt_chroma/receipt_chroma/compaction/operations.py
ls receipt_chroma/receipt_chroma/compaction/compaction_run.py
ls receipt_chroma/receipt_chroma/compaction/efs_snapshot_manager.py
ls receipt_chroma/receipt_chroma/stream/change_detector.py

# ✓ Models created
ls receipt_chroma/receipt_chroma/compaction/models.py
ls receipt_chroma/receipt_chroma/stream/models.py

# ✓ Imports work
python -c "from receipt_chroma.compaction import update_receipt_metadata"
python -c "from receipt_chroma.stream import get_chromadb_relevant_changes"

# ✓ Tests pass
pytest receipt_chroma/tests/ -v

# ✓ No migration imports in wrong places
grep -r "from compaction import" infra/chromadb_compaction/lambdas/*.py
# Should only be in handler files

# ✓ Package exports correct
python -c "import receipt_chroma; print(receipt_chroma.__all__)"
```

## Git Operations

### Commit Strategy

```bash
# Commit in logical chunks
git add receipt_chroma/receipt_chroma/compaction/models.py
git add receipt_chroma/receipt_chroma/stream/models.py
git commit -m "compaction: create domain models"

git add receipt_chroma/receipt_chroma/compaction/*.py
git commit -m "compaction: move business logic to package"

git add infra/chromadb_compaction/lambdas/
git commit -m "compaction: update Lambda imports"
```

### Viewing Changes

```bash
# See what changed
git status
git diff

# See specific file changes
git diff receipt_chroma/receipt_chroma/__init__.py

# See commit history
git log --oneline -10
```

## Monitoring After Deployment

### CloudWatch Metrics to Watch

```
CompactionLambdaSuccess        # Should stay stable
CompactionLambdaError          # Should stay low
CompactionMetadataUpdates      # Should be > 0
CompactionLabelUpdates         # Should be > 0
StreamProcessorProcessedRecords # Should be > 0
```

### Log Patterns to Check

```bash
# Check for errors
pulumi logs --since 30m | grep ERROR

# Check for specific operations
pulumi logs --since 30m | grep "metadata update"
pulumi logs --since 30m | grep "label update"

# Check import errors
pulumi logs --since 30m | grep "ImportError"
pulumi logs --since 30m | grep "ModuleNotFoundError"
```

## Documentation Links

- **Overview**: [MIGRATION_OVERVIEW.md](./MIGRATION_OVERVIEW.md) - Why and what
- **Architecture**: [TWO_PACKAGE_RATIONALE.md](./TWO_PACKAGE_RATIONALE.md) - Why two packages
- **Implementation**: [MIGRATION_IMPLEMENTATION.md](./MIGRATION_IMPLEMENTATION.md) - Step-by-step guide
- **File Mapping**: [MIGRATION_FILE_MAPPING.md](./MIGRATION_FILE_MAPPING.md) - What moves where
- **Testing**: [MIGRATION_TESTING.md](./MIGRATION_TESTING.md) - Test strategy
- **Rollback**: [MIGRATION_ROLLBACK.md](./MIGRATION_ROLLBACK.md) - Safety net

## Need Help?

1. Check [MIGRATION_IMPLEMENTATION.md](./MIGRATION_IMPLEMENTATION.md) Troubleshooting section
2. Review [Common Issues](#common-issues) above
3. Check [MIGRATION_ROLLBACK.md](./MIGRATION_ROLLBACK.md) if needed
4. Create GitHub issue with details

---

**Last Updated**: December 15, 2024

