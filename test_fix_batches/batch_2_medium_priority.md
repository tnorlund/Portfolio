# Batch 2: Medium Priority Files (10-17 failures each)

## Files to Fix:
- `tests/integration/test__receipt_label_analysis.py` (17 failures)
- `tests/integration/test__receipt_validation_category.py` (13 failures)
- `tests/integration/test__receipt_validation_result.py` (11 failures)

## Common Patterns Identified:
1. **Parameter Name Inconsistencies**: 
   - `"receiptlabelanalysis"` → `"receipt_label_analysis"`
   - `"validationcategory"` → `"validation_category"`
   - `"validationresult"` → `"validation_result"`

2. **Exception Mapping Issues**: Tests expecting generic `Exception` for specific DynamoDB errors
3. **Validation Message Content**: Tests expecting different error message formats

## Codex Prompt:

```
I need you to fix failing integration tests in these Python test files that handle receipt validation and analysis. The failures are due to parameter name mismatches and exception type inconsistencies.

Please analyze each failing test and apply these fixes:

1. **Fix Parameter Names in Error Messages**:
   - Change `"receiptlabelanalysis cannot be None"` to `"receipt_label_analysis cannot be None"`
   - Change `"validationcategory cannot be None"` to `"validation_category cannot be None"`
   - Change `"validationresult cannot be None"` to `"validation_result cannot be None"`
   - Ensure all compound parameter names use snake_case

2. **Update Exception Types for DynamoDB Operations**:
   - For add operations with ConditionalCheckFailedException: `EntityAlreadyExistsError`
   - For update/delete operations with ConditionalCheckFailedException: `EntityNotFoundError`
   - For UnknownError cases: `DynamoDBError` with message `"Something unexpected"`

3. **Fix Validation Message Patterns**:
   - Ensure capitalized class names become lowercase parameter names in error messages
   - `"Analysis must be an instance"` → `"analysis must be an instance"`
   - `"Category must be an instance"` → `"category must be an instance"`

4. **Add Required Imports**:
   ```python
   from receipt_dynamo.data.shared_exceptions import (
       EntityAlreadyExistsError,
       EntityNotFoundError,
       DynamoDBError,
       DynamoDBThroughputError,
       DynamoDBServerError,
       DynamoDBValidationError,
       DynamoDBAccessError,
   )
   ```

First run tests to identify current failures:
```bash
python -m pytest tests/integration/test__receipt_label_analysis.py --tb=short -v
python -m pytest tests/integration/test__receipt_validation_category.py --tb=short -v
python -m pytest tests/integration/test__receipt_validation_result.py --tb=short -v
```

Focus on systematic pattern fixes rather than understanding business logic.

Goal: Reduce the combined 41 failures in these 3 files to under 15 total.
```