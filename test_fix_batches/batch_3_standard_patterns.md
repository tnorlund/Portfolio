# Batch 3: Standard Pattern Files (5-7 failures each)

## Files to Fix:
- `tests/integration/test__receipt_letter.py` (7 failures)
- `tests/integration/test__receipt_validation_summary.py` (6 failures)
- `tests/integration/test__receipt_field.py` (6 failures)
- `tests/integration/test__queue.py` (6 failures)
- `tests/integration/test__job_log.py` (6 failures)
- `tests/integration/test__receipt_structure_analysis.py` (5 failures)

## Common Patterns Identified:
1. **Standard Validation Message Updates**: Converting from verbose to simple format
2. **Exception Type Standardization**: Moving from generic to specific exceptions
3. **Parameter Name Consistency**: Ensuring snake_case parameter names

## Codex Prompt:

```
I need you to fix failing integration tests in these Python test files. These are lower-volume failures but follow predictable patterns that can be systematically fixed.

Please analyze each failing test and apply these standard fixes:

1. **Validation Message Format Standardization**:
   - Change `"X parameter is required and cannot be None"` to `"x cannot be None"`
   - Change `"Letters parameter is required and cannot be None"` to `"letters cannot be None"`
   - Change `"Summary parameter is required and cannot be None"` to `"summary cannot be None"`
   - Change `"Field parameter is required and cannot be None"` to `"field cannot be None"`

2. **Exception Type Updates**:
   - `pytest.raises(Exception, match=".*already exists.*")` → `pytest.raises(EntityAlreadyExistsError, match="already exists")`
   - `pytest.raises(Exception, match=".*does not exist.*")` → `pytest.raises(EntityNotFoundError, match="Entity does not exist: {EntityName}")`
   - `pytest.raises(Exception, match=".*Something unexpected.*")` → `pytest.raises(DynamoDBError, match="Something unexpected")`

3. **Parameter Case Fixes**:
   - Ensure parameter names in error messages are lowercase
   - `"Letter must be"` → `"letter must be"`
   - `"Summary must be"` → `"summary must be"`
   - `"Field must be"` → `"field must be"`

4. **List Validation Messages**:
   - `"All X must be instances of the Y class"` → `"All items in x must be Y instances"` (where applicable)
   - Check if this pattern exists in these files

5. **Add Standard Imports**:
   ```python
   from receipt_dynamo.data.shared_exceptions import (
       EntityAlreadyExistsError,
       EntityNotFoundError,
       DynamoDBError,
   )
   ```

First run tests to see current failures:
```bash
python -m pytest tests/integration/test__receipt_letter.py --tb=short -v
python -m pytest tests/integration/test__receipt_validation_summary.py --tb=short -v
python -m pytest tests/integration/test__receipt_field.py --tb=short -v
python -m pytest tests/integration/test__queue.py --tb=short -v
python -m pytest tests/integration/test__job_log.py --tb=short -v  
python -m pytest tests/integration/test__receipt_structure_analysis.py --tb=short -v
```

Apply fixes systematically across all 6 files. These should be straightforward pattern applications.

Goal: Reduce the combined 36 failures in these 6 files to under 12 total.
```