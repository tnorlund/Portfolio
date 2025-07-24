# Batch 4: Low Volume Files (1-4 failures each)

## Files to Fix:
- `tests/integration/test__job.py` (4 failures)
- `tests/integration/test__word.py` (3 failures)
- `tests/integration/test__places_cache.py` (3 failures)
- `tests/integration/test__ocr_job.py` (3 failures)
- `tests/integration/test__receipt_word.py` (2 failures)
- `tests/integration/test__line.py` (2 failures)
- `tests/integration/test__letter.py` (2 failures)
- `tests/integration/test__job_dependency.py` (2 failures)
- `tests/integration/test__receipt.py` (1 failure)
- `tests/integration/test__job_resource.py` (1 failure)

## Common Patterns Identified:
1. **Isolated Issues**: Each file has only a few failures, likely specific edge cases
2. **Exception Type Issues**: Probably generic Exception usage
3. **Message Format Issues**: Standard validation message patterns

## Codex Prompt:

```
I need you to fix the remaining failing integration tests in these Python test files. These are low-volume failures (1-4 per file) but should follow the same systematic patterns we've been applying.

Please analyze each failing test and apply these fixes:

1. **Standard Exception Type Updates**:
   - `pytest.raises(Exception, match=".*already exists.*")` → `pytest.raises(EntityAlreadyExistsError, match="already exists")`
   - `pytest.raises(Exception, match=".*does not exist.*")` → `pytest.raises(EntityNotFoundError, match="Entity does not exist: {EntityName}")`
   - `pytest.raises(Exception, match=".*Something unexpected.*")` → `pytest.raises(DynamoDBError, match="Something unexpected")`

2. **Validation Message Updates**:
   - `"X parameter is required and cannot be None"` → `"x cannot be None"`
   - `"X must be an instance of the Y class"` → `"x must be an instance of the Y class"`
   - Ensure all parameter names are lowercase in error messages

3. **Specific Known Issues**:
   - For `test__word.py`: Check for `EntityAlreadyExistsError` vs `ValueError` mismatches
   - For `test__job.py`: Look for "Something unexpected" vs other error message mismatches
   - For `test__places_cache.py` and `test__ocr_job.py`: Standard parameter validation issues

4. **Add Required Imports** (only add what's needed):
   ```python
   from receipt_dynamo.data.shared_exceptions import (
       EntityAlreadyExistsError,
       EntityNotFoundError,
       DynamoDBError,
   )
   ```

First run tests to identify specific failures:
```bash
python -m pytest tests/integration/test__job.py --tb=short -v
python -m pytest tests/integration/test__word.py --tb=short -v
python -m pytest tests/integration/test__places_cache.py --tb=short -v
python -m pytest tests/integration/test__ocr_job.py --tb=short -v
python -m pytest tests/integration/test__receipt_word.py --tb=short -v
python -m pytest tests/integration/test__line.py --tb=short -v
python -m pytest tests/integration/test__letter.py --tb=short -v
python -m pytest tests/integration/test__job_dependency.py --tb=short -v
python -m pytest tests/integration/test__receipt.py --tb=short -v
python -m pytest tests/integration/test__job_resource.py --tb=short -v
```

Since these are low-volume failures, take time to understand each specific failure and apply the appropriate pattern fix.

Goal: Reduce the combined 25 failures in these 10 files to under 8 total.
```