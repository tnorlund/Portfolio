# Batch 1: High Priority Files (20+ failures each)

## Files to Fix:
- `tests/integration/test__receipt_word_label.py` (24 failures)
- `tests/integration/test__receipt_line_item_analysis.py` (20 failures) 
- `tests/integration/test__receipt_chatgpt_validation.py` (20 failures)

## Common Patterns Identified:
1. **Exception Type Mismatches**: Tests using `pytest.raises(Exception)` should use specific exception types:
   - `EntityAlreadyExistsError` for "already exists" scenarios
   - `EntityNotFoundError` for "does not exist" scenarios  
   - `DynamoDBError` for "Something unexpected" (UnknownError cases)

2. **Validation Message Format Issues**: Tests expecting old verbose format should expect simple format:
   - `"X parameter is required and cannot be None"` → `"x cannot be None"`
   - `"X must be an instance of the Y class"` → `"x must be an instance of the Y class"`

3. **Parameter Name Casing**: Tests expecting capitalized names should expect lowercase:
   - `"Receipt"` → `"receipt"`
   - `"Analysis"` → `"analysis"`

## Codex Prompt:

```
I need you to fix failing integration tests in these Python test files. The failures are due to test expectations not matching the actual error messages and exception types from a refactored base operations system.

Please analyze each failing test and apply these systematic fixes:

1. **Update Exception Types**: 
   - Change `pytest.raises(Exception, match=".*already exists.*")` to `pytest.raises(EntityAlreadyExistsError, match="already exists")`
   - Change `pytest.raises(Exception, match=".*does not exist.*")` to `pytest.raises(EntityNotFoundError, match="Entity does not exist: {EntityName}")`
   - Change `pytest.raises(Exception, match=".*Unknown error.*")` to `pytest.raises(DynamoDBError, match="Something unexpected")`

2. **Fix Validation Messages**:
   - Change `"X parameter is required and cannot be None"` to `"x cannot be None"`  
   - Change `"X must be an instance of the Y class"` to `"x must be an instance of the Y class"`
   - Ensure parameter names are lowercase in error messages

3. **Add Missing Imports**: Add any missing exception imports at the top:
   ```python
   from receipt_dynamo.data.shared_exceptions import (
       EntityAlreadyExistsError,
       EntityNotFoundError, 
       DynamoDBError,
   )
   ```

First run the tests to see current failures:
```bash
python -m pytest tests/integration/test__receipt_word_label.py --tb=short -v
python -m pytest tests/integration/test__receipt_line_item_analysis.py --tb=short -v  
python -m pytest tests/integration/test__receipt_chatgpt_validation.py --tb=short -v
```

Then apply the fixes systematically to each failing test case. Focus on the patterns above rather than trying to understand the business logic.

Goal: Reduce the combined 64 failures in these 3 files to under 20 total.
```