# Phase 3: Complex Fixes - Sequential with Parallel Sub-tasks

## Overview
**Objective**: Fix data layer issues, type annotations, and pylint errors
**Duration**: 45 minutes total
**Strategy**: Sequential order with parallel sub-tasks where safe

## Task 3.1: Fix Data Layer (fix-data-layer)

**Duration**: 20 minutes
**Strategy**: Sequential due to inheritance chains
**Files**: ~50 data layer files

### Inheritance Chain Analysis
```
_base.py (DynamoClientProtocol)
  ↓
dynamo_client.py (DynamoClient)
  ↓
_*.py mixins (inherit from DynamoClientProtocol)
```

### Sequential Processing Order
1. **Foundation files** (5 min):
   ```bash
   # Critical base files that others depend on
   python -m black receipt_dynamo/data/_base.py
   python -m black receipt_dynamo/data/dynamo_client.py
   python -m black receipt_dynamo/data/shared_exceptions.py
   ```

2. **Mixin files in batches** (12 min):
   ```bash
   # Batch 1: Core mixins (no inter-dependencies)
   python -m black receipt_dynamo/data/_image.py
   python -m black receipt_dynamo/data/_line.py
   python -m black receipt_dynamo/data/_word.py
   python -m black receipt_dynamo/data/_letter.py

   # Batch 2: Receipt mixins (may depend on core)
   python -m black receipt_dynamo/data/_receipt*.py

   # Batch 3: Job system mixins
   python -m black receipt_dynamo/data/_job*.py
   python -m black receipt_dynamo/data/_queue.py
   python -m black receipt_dynamo/data/_instance.py
   ```

3. **Utility and specialized files** (3 min):
   ```bash
   # These can be done in parallel as they're independent
   python -m black receipt_dynamo/data/_geometry.py &
   python -m black receipt_dynamo/data/_gpt.py &
   python -m black receipt_dynamo/data/_ocr.py &
   python -m black receipt_dynamo/data/_pulumi.py &
   wait
   ```

## Task 3.2: Fix Type Annotations (fix-type-annotations)

**Duration**: 15 minutes
**Strategy**: Parallel by file category
**Dependencies**: After data layer formatting

### Type Issues by Category

1. **Generic Type Hints** (5 min, parallel):
   ```python
   # Common patterns to fix:
   List[str] → list[str]  # Python 3.9+ style
   Dict[str, Any] → dict[str, Any]
   Optional[int] → int | None
   ```

2. **AWS Type Annotations** (5 min, parallel):
   ```python
   # Already fixed with boto3-stubs, verify:
   client: DynamoDBClient = boto3.client("dynamodb")
   s3_client: S3Client = boto3.client("s3")
   ```

3. **Protocol and ABC Compliance** (5 min, sequential):
   ```python
   # Ensure all mixins properly implement DynamoClientProtocol
   # Check method signatures match protocol definitions
   ```

### Parallel Type Checking Script
```python
# scripts/fix_types_parallel.py
import concurrent.futures
from pathlib import Path

def fix_file_types(filepath):
    """Fix type annotations in a single file"""
    # Read file
    # Apply transformations
    # Write back
    # Return results

# Process files in parallel by category
categories = {
    'entities': Path('receipt_dynamo/entities').glob('*.py'),
    'services': Path('receipt_dynamo/services').glob('*.py'),
    'data_utils': [f for f in Path('receipt_dynamo/data').glob('*.py')
                   if not f.name.startswith('_')]
}

for category, files in categories.items():
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(fix_file_types, f) for f in files]
        results = [f.result() for f in concurrent.futures.as_completed(futures)]
```

## Task 3.3: Fix Pylint Errors (fix-pylint-errors)

**Duration**: 10 minutes
**Strategy**: Issue-specific fixing
**Dependencies**: After formatting and type fixes

### Known Issues

1. **Method Name Error** (2 min):
   ```python
   # File: receipt_dynamo/data/_job.py:346
   # Error: Instance of '_Job' has no '_getJobWithStatus' member
   # Fix: Rename method or fix reference
   ```

2. **Import Issues** (3 min):
   ```bash
   # Check for circular imports
   python -c "import receipt_dynamo.data._job"
   ```

3. **Unused Variables** (5 min, parallel):
   ```bash
   # Fix unused variables in all files
   python -m pylint receipt_dynamo --disable=all --enable=W0612
   ```

### Pylint Error Categories
```python
error_fixes = {
    'E1101': 'no-member',           # Method/attribute not found
    'E0401': 'import-error',        # Import issues
    'E1121': 'too-many-function-args', # Function call issues
    'W0612': 'unused-variable',     # Unused variables
    'W0613': 'unused-argument',     # Unused arguments
}
```

## Validation Between Tasks

### After Each Task
```bash
# Quick validation
python -m black --check receipt_dynamo/data/
python -m pylint receipt_dynamo/data/ --errors-only
python -m mypy receipt_dynamo/data/ --no-error-summary
```

## Success Criteria

1. **Data layer formatting**: All data layer files pass black
2. **Type consistency**: Clean mypy run on all source files
3. **Pylint errors resolved**: Zero E-level and F-level pylint issues
4. **Import integrity**: All modules import successfully
5. **Test compatibility**: Tests still run after changes

## Risk Mitigation

- **Git commits after each task**: Easy rollback if issues arise
- **Test validation**: Run subset of tests after data layer changes
- **Incremental approach**: Fix and validate one category at a time
- **Dependency tracking**: Maintain import graph throughout process
