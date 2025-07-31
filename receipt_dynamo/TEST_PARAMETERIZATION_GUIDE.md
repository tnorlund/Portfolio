# Test Parameterization and Cleanup Guide

## Overview

This guide documents the comprehensive process for refactoring integration and unit tests to reduce code duplication, improve maintainability, and ensure clean code quality. This process was successfully applied to `test__receipt.py`, reducing it from 2,787 to 1,231 lines (56% reduction) while maintaining 100% test coverage.

## Step-by-Step Process

### Phase 1: Analysis and Planning

#### 1.1 Identify Test Patterns
First, analyze the test file to identify repetitive patterns:

```bash
# Look for similar test function names
grep "^def test_" tests/integration/test_file.py | sort

# Count similar patterns
grep -c "ClientError" tests/integration/test_file.py
grep -c "ValidationError" tests/integration/test_file.py
```

Common patterns to look for:
- **Error handling tests**: Multiple tests for different error codes
- **Validation tests**: Similar tests for None, invalid types, invalid values
- **CRUD operations**: Similar tests for add/update/delete with different inputs
- **Batch operations**: Repeated tests for single vs batch operations

#### 1.2 Document Current State
Record the initial metrics:
- Total lines of code
- Number of test functions
- Test execution time
- Coverage percentage

### Phase 2: Parameterization Implementation

#### 2.1 Create Error Scenario Lists
Consolidate common error patterns into parameter lists:

```python
# Define at module level
ERROR_SCENARIOS = [
    # (error_code, expected_exception, error_message_match)
    ("ProvisionedThroughputExceededException", DynamoDBThroughputError, "Throughput exceeded"),
    ("InternalServerError", DynamoDBServerError, "DynamoDB server error"),
    ("ValidationException", EntityValidationError, "Validation error"),
    ("AccessDeniedException", DynamoDBError, "DynamoDB error during"),
    ("ResourceNotFoundException", OperationError, "DynamoDB resource not found"),
]

BATCH_ERROR_SCENARIOS = [
    # Additional scenarios for batch operations
    ("ConditionalCheckFailedException", EntityNotFoundError, "Cannot delete receipts: one or more receipts not found"),
]
```

#### 2.2 Refactor Error Tests
Replace multiple error handling tests with parameterized versions:

```python
# Before: 5 separate test functions
def test_add_receipt_provisioned_throughput_exceeded():
    # ... test code ...

def test_add_receipt_internal_server_error():
    # ... test code ...

# After: 1 parameterized test
@pytest.mark.parametrize("error_code,expected_exception,error_match", ERROR_SCENARIOS)
def test_add_receipt_client_errors(
    error_code, expected_exception, error_match, 
    dynamo_client, example_receipt, mocker
):
    # pylint: disable=too-many-positional-arguments
    mock_add = mocker.patch.object(
        dynamo_client._client, "put_item",
        side_effect=ClientError(
            {"Error": {"Code": error_code}}, "PutItem"
        )
    )
    
    with pytest.raises(expected_exception, match=error_match):
        dynamo_client.add_receipt(example_receipt)
    
    mock_add.assert_called_once()
```

#### 2.3 Consolidate Validation Tests
Group validation tests by operation type:

```python
# Single entity validation
@pytest.mark.parametrize("method_name,invalid_input,error_match", [
    ("add_receipt", None, "receipt cannot be None"),
    ("add_receipt", "not-a-receipt", "receipt must be an instance of Receipt"),
    ("update_receipt", None, "receipt cannot be None"),
    ("update_receipt", "not-a-receipt", "receipt must be an instance of Receipt"),
    ("delete_receipt", None, "receipt cannot be None"),
    ("delete_receipt", "not-a-receipt", "receipt must be an instance of Receipt"),
])
def test_single_receipt_validation(method_name, invalid_input, error_match, dynamo_client):
    method = getattr(dynamo_client, method_name)
    with pytest.raises(OperationError, match=error_match):
        method(invalid_input)

# Batch operation validation
@pytest.mark.parametrize("method_name,invalid_input,error_type,error_match", [
    ("add_receipts", None, "receipts cannot be None"),
    ("add_receipts", "not-a-list", "receipts must be a list"),
    ("update_entities", None, "receipts cannot be None"),
    ("update_entities", "not-a-list", "receipts must be a list"),
])
def test_batch_receipt_validation_basic(method_name, invalid_input, error_type, error_match, dynamo_client):
    # ... test implementation ...
```

### Phase 3: Handle Parallel Test Execution Issues

#### 3.1 Fix Non-Deterministic Parameters
Replace dynamic values with fixed ones to ensure consistent test collection:

```python
# Before: Non-deterministic UUID generation
@pytest.mark.parametrize("receipt_id", [str(uuid4()) for _ in range(3)])

# After: Fixed UUIDs
FIXED_UUIDS = [
    "550e8400-e29b-41d4-a716-446655440001",
    "550e8400-e29b-41d4-a716-446655440002", 
    "550e8400-e29b-41d4-a716-446655440003",
]
@pytest.mark.parametrize("receipt_id", FIXED_UUIDS)
```

#### 3.2 Configure Coverage for Parallel Execution
Update `pyproject.toml`:

```toml
[tool.coverage.run]
source = ["receipt_dynamo"]
omit = ["tests/*"]
parallel = true
concurrency = ["multiprocessing"]
data_file = ".coverage"
```

Create `.coveragerc`:

```ini
[run]
source = receipt_dynamo
omit = tests/*
parallel = True
concurrency = multiprocessing
data_file = .coverage

[report]
precision = 2
show_missing = True
skip_covered = False

[paths]
source =
    receipt_dynamo/
    */receipt_dynamo/
```

#### 3.3 Create Parallel Test Scripts
Create `pytest_parallel.sh`:

```bash
#!/bin/bash
# Wrapper script for running pytest with parallel execution and coverage

# Always set the required environment variable
export COVERAGE_CORE=sysmon

# Run pytest with all arguments passed to this script
exec pytest "$@" -n auto --cov=receipt_dynamo --cov-report=term-missing
```

### Phase 4: Code Quality Improvements

#### 4.1 Run Pylint Analysis
```bash
pylint tests/integration/test_file.py
```

Common issues to fix:
- Line too long (>79 characters)
- Protected member access
- Too many positional arguments
- Unused variables
- Unnecessary else after return

#### 4.2 Create Automated Fix Scripts
Create scripts to automatically fix common pylint issues:

```python
#!/usr/bin/env python3
"""Script to fix pylint issues in test files"""

import re

def fix_line_length(content):
    """Fix lines that are too long by splitting docstrings and comments."""
    # Implementation to split long lines
    
def fix_protected_access(content):
    """Add pylint disable comments for legitimate protected access."""
    lines = content.split('\n')
    fixed_lines = []
    
    for i, line in enumerate(lines):
        # Add disable comment for mocker.patch.object with _client
        if 'client._client' in line and 'mocker.patch.object' in line:
            indent = len(line) - len(line.lstrip())
            fixed_lines.append(' ' * indent + '# pylint: disable=protected-access')
        fixed_lines.append(line)
    
    return '\n'.join(fixed_lines)

def fix_too_many_args(content):
    """Add disable comments for parameterized tests with many arguments."""
    # Add pylint: disable=too-many-positional-arguments before affected functions
```

#### 4.3 Apply Pylint Fixes
1. Add appropriate pylint disable comments only where necessary
2. Fix genuine code issues (unused variables, unnecessary else)
3. Maintain code functionality while improving quality

### Phase 5: Verification and Documentation

#### 5.1 Run All Tests
Verify tests still pass after refactoring:

```bash
# Run tests in parallel with coverage
./pytest_parallel.sh tests/integration/test_file.py

# Verify coverage hasn't decreased
coverage report --show-missing
```

#### 5.2 Document Results
Create a summary document with:
- Before/after metrics (lines of code, test count)
- Coverage verification
- Performance improvements
- Patterns identified and consolidated

## Common Parameterization Patterns

### Pattern 1: Error Handling Tests
```python
ERROR_SCENARIOS = [
    (error_code, expected_exception, error_match),
    # ... more scenarios
]

@pytest.mark.parametrize("error_code,expected_exception,error_match", ERROR_SCENARIOS)
def test_operation_errors(error_code, expected_exception, error_match, client, mocker):
    # Mock the operation to raise ClientError
    # Assert expected exception is raised
```

### Pattern 2: Validation Tests
```python
VALIDATION_SCENARIOS = [
    (invalid_input, expected_error, error_message),
    # ... more scenarios
]

@pytest.mark.parametrize("invalid_input,expected_error,error_message", VALIDATION_SCENARIOS)
def test_input_validation(invalid_input, expected_error, error_message, client):
    # Test validation logic
```

### Pattern 3: CRUD Operations
```python
CRUD_OPERATIONS = [
    ("add", "put_item"),
    ("update", "update_item"),
    ("delete", "delete_item"),
]

@pytest.mark.parametrize("operation,dynamo_method", CRUD_OPERATIONS)
def test_crud_operations(operation, dynamo_method, client, entity):
    # Test CRUD operations
```

## Best Practices

1. **Use Fixed Values**: Always use fixed UUIDs, timestamps, and other values in parameterized tests
2. **Group Related Tests**: Keep parameterized scenarios logically grouped
3. **Maintain Readability**: Don't over-parameterize - some unique tests should remain separate
4. **Document Parameters**: Use clear, descriptive parameter names
5. **Preserve Coverage**: Ensure all original test scenarios are maintained
6. **Add Pylint Disables Judiciously**: Only disable specific warnings where necessary

## Expected Outcomes

- **50-60% reduction** in lines of code
- **Maintained or improved** test coverage
- **Faster test discovery** and potentially faster execution
- **Easier maintenance** - new test cases added by updating parameter lists
- **Cleaner code** with improved pylint scores

## Checklist for Agents

- [ ] Analyze test file for repetitive patterns
- [ ] Create parameter lists for common scenarios
- [ ] Implement parameterized tests
- [ ] Fix non-deterministic test parameters
- [ ] Configure coverage for parallel execution
- [ ] Run pylint and fix issues
- [ ] Verify all tests pass
- [ ] Verify coverage is maintained
- [ ] Document changes and results
- [ ] Create summary report

This guide provides a systematic approach to refactoring test files, ensuring consistency across the codebase while dramatically improving maintainability and reducing duplication.