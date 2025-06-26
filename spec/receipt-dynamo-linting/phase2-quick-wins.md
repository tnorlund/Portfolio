# Phase 2: Quick Wins - Parallel Formatting

## Overview
**Objective**: Fix all trivial formatting issues in parallel
**Duration**: 15 minutes total
**Parallel Tasks**: 4 concurrent processes

## Task 2.1: Format Entities (format-entities)

**Duration**: 5 minutes
**Files**: ~47 entity files

### Commands
```bash
# Format entity files
python -m black receipt_dynamo/entities/
python -m isort receipt_dynamo/entities/

# Verify
python -m black --check receipt_dynamo/entities/
```

### Expected Issues
- Long line breaks (conditional expressions)
- Import ordering
- Trailing whitespace

## Task 2.2: Format Services (format-services)

**Duration**: 3 minutes
**Files**: ~4 service files

### Commands
```bash
# Format service files
python -m black receipt_dynamo/services/
python -m isort receipt_dynamo/services/

# Verify
python -m black --check receipt_dynamo/services/
```

## Task 2.3: Format Unit Tests (format-tests-unit)

**Duration**: 8 minutes
**Files**: ~40 unit test files

### Commands
```bash
# Format unit tests
python -m black tests/unit/
python -m isort tests/unit/

# Verify formatting doesn't break tests
python -m pytest tests/unit/ --collect-only
```

## Task 2.4: Format Integration Tests (format-tests-integration)

**Duration**: 10 minutes
**Files**: ~40 integration test files

### Commands
```bash
# Format integration tests
python -m black tests/integration/
python -m isort tests/integration/

# Verify formatting doesn't break tests
python -m pytest tests/integration/ --collect-only
```

## Parallel Execution Script

```python
# scripts/parallel_format.py
import concurrent.futures
import subprocess
from pathlib import Path

def format_directory(directory, name):
    """Format a directory and return results"""
    try:
        # Run black
        subprocess.run(['python', '-m', 'black', directory], check=True)

        # Run isort
        subprocess.run(['python', '-m', 'isort', directory], check=True)

        # Verify
        result = subprocess.run(['python', '-m', 'black', '--check', directory],
                              capture_output=True, text=True)

        return {
            'task': name,
            'status': 'success' if result.returncode == 0 else 'failed',
            'files_formatted': len(list(Path(directory).glob('*.py'))),
            'output': result.stdout
        }
    except Exception as e:
        return {'task': name, 'status': 'error', 'error': str(e)}

# Execute all tasks in parallel
tasks = [
    ('receipt_dynamo/entities/', 'format-entities'),
    ('receipt_dynamo/services/', 'format-services'),
    ('tests/unit/', 'format-tests-unit'),
    ('tests/integration/', 'format-tests-integration')
]

with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
    futures = [executor.submit(format_directory, directory, name)
              for directory, name in tasks]

    results = [future.result() for future in concurrent.futures.as_completed(futures)]
```

## Success Criteria

1. **Zero black violations**: All targeted files pass `black --check`
2. **Import order fixed**: All files pass `isort --check-only`
3. **No test breakage**: `pytest --collect-only` succeeds for all test dirs
4. **Progress tracking**: Each task reports files processed

## Expected Output

```
Task Summary:
- format-entities: 47 files formatted ✓
- format-services: 4 files formatted ✓
- format-tests-unit: 40 files formatted ✓
- format-tests-integration: 40 files formatted ✓

Total: 131/191 files formatted (68% complete)
Remaining: Data layer files (requires careful ordering)
```

## Notes

- **Data layer excluded**: These files have inheritance dependencies
- **End-to-end tests excluded**: Small count, will handle in Phase 4
- **Safe parallelism**: These directories have no cross-dependencies
- **Fast feedback**: Each task completes in under 10 minutes
