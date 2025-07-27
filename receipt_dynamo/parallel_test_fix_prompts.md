# Parallel Test Fix Prompts

This document contains prompts for fixing failing tests in parallel. Each section can be assigned to a different developer or AI assistant.

## Background

We've refactored the error handling in the `base_operations` module to use dynamic error message generation. The new error messages follow these patterns:

### Key Error Message Changes:
1. **Entity Not Found**: `"{EntityName} does not exist"` → `"Entity does not exist: {EntityName}"`
2. **Entity Already Exists**: `"{EntityName} already exists"` → `"Entity already exists: {EntityName}"`
3. **Validation Errors**: Various specific messages → Standardized patterns
4. **Could not add/update/delete**: `"Could not {action} {entity} to/from the database"` → `"Could not {action} {entity} to/from DynamoDB"`

### Error Config Location:
The correct error messages are defined in: `receipt_dynamo/data/base_operations/error_config.py`

---

## File 1: test__receipt_word_label.py (30 failures)

**Task**: Fix 30 failing tests in `tests/integration/test__receipt_word_label.py`

**Common Patterns to Fix**:
- `"ReceiptWordLabel does not exist"` → `"Entity does not exist: ReceiptWordLabel"`
- `"ReceiptWordLabel already exists"` → `"Entity already exists: ReceiptWordLabel"`
- Parameter validation messages need to match those in `error_config.py`

**Failing Tests**:
```bash
grep "test__receipt_word_label.py" /tmp/all_failing_tests.txt
```

**Instructions**:
1. Read the error messages in `receipt_dynamo/data/base_operations/error_config.py`
2. Update the test assertions to match the new error message patterns
3. Use regex patterns where appropriate (e.g., `r"Entity does not exist: \w+"`)
4. Ensure all parameter validation tests match the exact messages from error_config.py

---

## File 2: test__receipt_letter.py (29 failures)

**Task**: Fix 29 failing tests in `tests/integration/test__receipt_letter.py`

**Common Patterns to Fix**:
- `"ReceiptLetter does not exist"` → `"Entity does not exist: ReceiptLetter"`
- `"Could not add receipt letter to the database"` → `"Could not add ReceiptLetter to DynamoDB"`
- Validation error messages

**Failing Tests**:
```bash
grep "test__receipt_letter.py" /tmp/all_failing_tests.txt
```

**Instructions**:
1. Check error_config.py for the correct error message patterns
2. Update all error assertions in the test file
3. Pay special attention to batch operation error messages

---

## File 3: test__receipt_line_item_analysis.py (28 failures)

**Task**: Fix 28 failing tests in `tests/integration/test__receipt_line_item_analysis.py`

**Common Patterns to Fix**:
- Entity not found/already exists patterns
- Database operation error messages
- Validation messages for required parameters

**Failing Tests**:
```bash
grep "test__receipt_line_item_analysis.py" /tmp/all_failing_tests.txt
```

---

## File 4: test__receipt_field.py (28 failures)

**Task**: Fix 28 failing tests in `tests/integration/test__receipt_field.py`

**Common Patterns to Fix**:
- `"ReceiptField already exists"` → `"Entity already exists: ReceiptField"`
- `"receipt_field cannot be None"` → Check error_config.py for exact message
- Database operation errors

**Failing Tests**:
```bash
grep "test__receipt_field.py" /tmp/all_failing_tests.txt
```

---

## File 5: test__receipt_chatgpt_validation.py (26 failures)

**Task**: Fix 26 failing tests in `tests/integration/test__receipt_chatgpt_validation.py`

**Common Patterns to Fix**:
- `"validation cannot be None"` → Check error_config.py
- `"Could not delete receipt ChatGPT validation from DynamoDB"` → Verify exact format
- Entity already exists patterns

**Failing Tests**:
```bash
grep "test__receipt_chatgpt_validation.py" /tmp/all_failing_tests.txt
```

---

## File 6: test__receipt_validation_result.py (24 failures)

**Task**: Fix 24 failing tests in `tests/integration/test__receipt_validation_result.py`

**Common Patterns to Fix**:
- Validation parameter error messages
- Entity not found/already exists patterns
- Database operation errors

**Failing Tests**:
```bash
grep "test__receipt_validation_result.py" /tmp/all_failing_tests.txt
```

---

## File 7: test__receipt_label_analysis.py (24 failures)

**Task**: Fix 24 failing tests in `tests/integration/test__receipt_label_analysis.py`

**Common Patterns to Fix**:
- Similar patterns to other receipt analysis tests
- Check for specific entity name in error messages

**Failing Tests**:
```bash
grep "test__receipt_label_analysis.py" /tmp/all_failing_tests.txt
```

---

## File 8: Other Files (Remaining failures)

For files with fewer failures, the same principles apply:
- test__receipt_validation_category.py (19 failures)
- test__receipt.py (18 failures)
- test__receipt_validation_summary.py (13 failures)
- test__batch_summary.py
- test__job.py
- test__job_metric.py
- test__job_dependency.py
- test__instance.py
- test__places_cache.py

---

## General Fix Strategy

1. **For each test file**:
   ```python
   # Old pattern
   with pytest.raises(ValueError, match="ReceiptWord does not exist"):
   
   # New pattern
   with pytest.raises(EntityNotFoundError, match=r"Entity does not exist: ReceiptWord"):
   ```

2. **For validation errors**, check the exact message in `error_config.py`:
   ```python
   # Example from error_config.py
   "validation": {
       "required": "{parameter} parameter is required and cannot be None.",
       "type_mismatch": "{parameter} must be an instance of the {expected_type} class.",
       # ...
   }
   ```

3. **For database operation errors**:
   - Changed from "database" to "DynamoDB" in most messages
   - Check error_config.py for exact wording

4. **Use regex patterns for flexibility**:
   ```python
   # When entity name might vary
   match=r"Entity already exists: \w+"
   
   # For specific entity
   match=r"Entity already exists: ReceiptWord"
   ```

---

## Verification

After fixing, run:
```bash
pytest <test_file> -v -n auto
```

To verify all tests pass.

## Detailed Failing Tests by File

### test__receipt_word_label.py specific failures:

:test_addReceiptWordLabel_client_errors[AccessDeniedException-Access denied-Access denied]
:test_addReceiptWordLabel_client_errors[ValidationException-One or more parameters were invalid-One or more parameters given were invalid]
:test_addReceiptWordLabels_invalid_parameters[None-receipt_word_labels parameter is required and cannot be None.]
:test_deleteReceiptWordLabel_client_errors[ConditionalCheckFailedException-Item does not exist-(does not exist|Entity does not exist: \\\\w+)]
:test_deleteReceiptWordLabel_client_errors[UnknownError-Unknown error-Something unexpected]
:test_deleteReceiptWordLabel_client_errors[ValidationException-One or more parameters were invalid-One or more parameters were invalid]
:test_deleteReceiptWordLabel_nonexistent_raises
:test_deleteReceiptWordLabels_client_errors[UnknownError-Unknown error-Something unexpected]
:test_deleteReceiptWordLabels_client_errors[ValidationException-One or more parameters were invalid-One or more parameters were invalid]
:test_deleteReceiptWordLabels_invalid_parameters[invalid_input2-All elements in receipt_word_labels must be instances of ReceiptWordLabel]
:test_deleteReceiptWordLabels_invalid_parameters[None-receipt_word_labels parameter is required and cannot be None.]
:test_getReceiptWordLabel_client_errors[UnknownError-Unknown error-Something unexpected]
:test_getReceiptWordLabel_invalid_parameters[invalid_params0-image_id is required and cannot be None]
:test_getReceiptWordLabel_invalid_parameters[invalid_params1-receipt_id is required and cannot be None]
:test_getReceiptWordLabel_invalid_parameters[invalid_params2-line_id is required and cannot be None]
:test_getReceiptWordLabel_invalid_parameters[invalid_params3-word_id is required and cannot be None]
:test_getReceiptWordLabel_invalid_parameters[invalid_params4-label is required and cannot be None]
:test_getReceiptWordLabelsByLabel_client_errors[UnknownError-Unknown error-Something unexpected]
:test_getReceiptWordLabelsByLabel_pagination_errors
:test_getReceiptWordLabelsByValidationStatus_pagination_midway_failure
:test_listReceiptWordLabels_client_errors[UnknownError-Unknown error-Something unexpected]
:test_listReceiptWordLabels_pagination_errors
:test_updateReceiptWordLabel_client_errors[ConditionalCheckFailedException-Item does not exist-(does not exist|Entity does not exist: \\\\w+)]
:test_updateReceiptWordLabel_client_errors[UnknownError-Unknown error-Something unexpected]
:test_updateReceiptWordLabel_client_errors[ValidationException-One or more parameters were invalid-One or more parameters were invalid]
:test_updateReceiptWordLabel_nonexistent_raises
:test_updateReceiptWordLabels_client_errors[UnknownError-Unknown error-Something unexpected-DynamoDBError]
:test_updateReceiptWordLabels_client_errors[ValidationException-One or more parameters were invalid-One or more parameters were invalid-DynamoDBValidationError]
:test_updateReceiptWordLabels_invalid_parameters[invalid_input2-All elements in receipt_word_labels must be instances of ReceiptWordLabel]
:test_updateReceiptWordLabels_invalid_parameters[None-receipt_word_labels parameter is required and cannot be None.]

### test__receipt_letter.py specific failures:

:test_addReceiptLetter_client_errors[UnknownError-Unknown error-Could not add receipt letter to DynamoDB]
:test_addReceiptLetter_duplicate_raises
:test_addReceiptLetter_invalid_parameters[None-letter cannot be None]
:test_addReceiptLetters_client_errors[UnknownError-Unknown error occurred-Could not add receipt letter to DynamoDB]
:test_addReceiptLetters_invalid_parameters[invalid_input2-All elements in letters must be instances of ReceiptLetter]
:test_addReceiptLetters_invalid_parameters[None-letters parameter is required and cannot be None.]
:test_addReceiptLetters_invalid_parameters[not-a-list-letters must be a list.]
:test_deleteReceiptLetter_client_errors[UnknownError-Unknown error occurred-Could not delete ReceiptLetter from the database]
:test_deleteReceiptLetter_invalid_parameters[None-letter cannot be None]
:test_deleteReceiptLetters_client_errors[UnknownError-Unknown error occurred-Could not delete ReceiptLetter from the database]
:test_deleteReceiptLetters_invalid_parameters[invalid_input2-All elements in letters must be instances of ReceiptLetter]
:test_deleteReceiptLetters_invalid_parameters[None-letters parameter is required and cannot be None.]
:test_deleteReceiptLetters_invalid_parameters[not a list-letters must be a list]
:test_getReceiptLetter_invalid_parameters[image_id-None-image_id cannot be None-sample_override1]
:test_getReceiptLetter_invalid_parameters[letter_id-None-letter_id cannot be None-sample_override4]
:test_getReceiptLetter_invalid_parameters[line_id-None-line_id cannot be None-sample_override2]
:test_getReceiptLetter_invalid_parameters[receipt_id-None-receipt_id cannot be None-sample_override0]
:test_getReceiptLetter_invalid_parameters[word_id-None-word_id cannot be None-sample_override3]
:test_listReceiptLettersFromWord_invalid_parameters[image_id-None-image_id cannot be None]
:test_listReceiptLettersFromWord_invalid_parameters[line_id-None-line_id cannot be None]
:test_listReceiptLettersFromWord_invalid_parameters[receipt_id-None-receipt_id cannot be None]
:test_listReceiptLettersFromWord_invalid_parameters[word_id-None-word_id cannot be None]
:test_updateReceiptLetter_client_errors[ConditionalCheckFailedException-Item does not exist-Entity does not exist]
:test_updateReceiptLetter_client_errors[UnknownError-Unknown error occurred-Could not update receipt letter in DynamoDB]
:test_updateReceiptLetter_invalid_parameters[None-letter cannot be None]
:test_updateReceiptLetters_client_errors[UnknownError-Unknown error occurred-Could not update receipt letter in DynamoDB-DynamoDBError-None]
:test_updateReceiptLetters_invalid_inputs[invalid_input2-All elements in letters must be instances of ReceiptLetter]
:test_updateReceiptLetters_invalid_inputs[None-letters parameter is required and cannot be None.]
:test_updateReceiptLetters_invalid_inputs[not-a-list-letters must be a list of ReceiptLetter instances.]

### test__receipt_field.py specific failures:

:test_addReceiptField_client_errors[UnknownError-Unknown error-Could not add receipt field to DynamoDB]
:test_addReceiptField_duplicate_raises
:test_addReceiptField_invalid_parameters[None-receipt_field cannot be None]
:test_addReceiptFields_client_errors[UnknownError-Unknown error-Could not add receipt field to DynamoDB]
:test_addReceiptFields_invalid_parameters[invalid_input2-All receipt_fields must be instances of the ReceiptField class.]
:test_addReceiptFields_invalid_parameters[None-receipt_fields cannot be None]
:test_addReceiptFields_invalid_parameters[not-a-list-receipt_fields must be a list of ReceiptField instances.]
:test_deleteReceiptField_client_errors[UnknownError-Unknown error-Could not delete receipt field from DynamoDB]
:test_deleteReceiptField_invalid_parameters[None-receipt_field cannot be None]
:test_deleteReceiptField_nonexistent_raises
:test_deleteReceiptField_success
:test_deleteReceiptFields_client_errors[UnknownError-Unknown error-Could not delete receipt field from DynamoDB]
:test_deleteReceiptFields_invalid_parameters[invalid_input2-All receipt_fields must be instances of the ReceiptField class.]
:test_deleteReceiptFields_invalid_parameters[None-receipt_fields cannot be None]
:test_deleteReceiptFields_invalid_parameters[not-a-list-receipt_fields must be a list of ReceiptField instances.]
:test_deleteReceiptFields_success
:test_getReceiptField_invalid_parameters[invalid_params0-field_type cannot be None]
:test_getReceiptField_invalid_parameters[invalid_params1-image_id cannot be None]
:test_getReceiptField_invalid_parameters[invalid_params2-receipt_id cannot be None]
:test_getReceiptField_nonexistent_raises
:test_updateReceiptField_client_errors[UnknownError-Unknown error-Could not update receipt field in DynamoDB]
:test_updateReceiptField_invalid_parameters[None-receipt_field cannot be None]
:test_updateReceiptField_nonexistent_raises
:test_updateReceiptFields_client_errors[ConditionalCheckFailedException-One or more items do not exist-Entity does not exist: list-EntityNotFoundError]
:test_updateReceiptFields_client_errors[UnknownError-Unknown error-Could not update receipt field in DynamoDB-DynamoDBError]
:test_updateReceiptFields_invalid_parameters[invalid_input2-All receipt_fields must be instances of the ReceiptField class.]
:test_updateReceiptFields_invalid_parameters[None-receipt_fields cannot be None]
:test_updateReceiptFields_invalid_parameters[not-a-list-receipt_fields must be a list of ReceiptField instances.]
