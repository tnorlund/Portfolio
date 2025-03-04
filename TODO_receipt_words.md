# TODO List for `_receipt_word.py` Improvements

## 1. Error Handling Improvements
- [ ] Add comprehensive error handling in `addReceiptWord()` for:
  - ResourceNotFoundException
  - ProvisionedThroughputExceededException
  - InternalServerError
  - ValidationException
  - AccessDeniedException
- [ ] Update error handling in `deleteReceiptWord()` to match the pattern
- [ ] Enhance error handling in `updateReceiptWord()` with specific error types
- [ ] Add detailed error handling to batch operations (`addReceiptWords()`, `deleteReceiptWords()`)
- [ ] Use `from e` syntax consistently for error propagation

## 2. Input Validation
- [ ] Add parameter validation in `addReceiptWord()` for word object
- [ ] Add validation for `receipt_id`, `image_id`, `line_id`, `word_id` in all relevant methods
- [ ] Add limit and lastEvaluatedKey validation in `listReceiptWords()`
- [ ] Add type checking for all parameters

## 3. Return Type Consistency
- [ ] Update `listReceiptWords()` to return tuple[list[ReceiptWord], dict | None]
- [ ] Add explicit return type hints to all methods
- [ ] Ensure consistent return patterns across similar methods

## 4. Documentation
- [ ] Update class docstring with complete method list and descriptions
- [ ] Add detailed parameter descriptions to all method docstrings
- [ ] Document all possible exceptions in method docstrings
- [ ] Add return type documentation to all methods
- [ ] Add examples where appropriate

## 5. Method Consistency
- [ ] Add missing error conditions to `updateReceiptWords()`
- [ ] Make batch operation behavior consistent with `_receipt_letter.py`
- [ ] Standardize pagination handling across list methods

## 6. Code Organization
- [ ] Move constant definitions to top of file
- [ ] Add type hints for all method parameters
- [ ] Ensure consistent method ordering matching `_receipt_letter.py`

## 7. Additional Features
- [ ] Add `updateReceiptWordsFromLine()` method to match pattern
- [ ] Add pagination support to `listReceiptWordsFromLine()`
- [ ] Add batch get/update operations with retries
