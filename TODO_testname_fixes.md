## Complete Test Function List

### Add Receipt Letter Tests
- test_addReceiptLetter_success
- test_addReceiptLetter_duplicate_raises
- test_addReceiptLetter_invalid_parameters
- test_addReceiptLetter_client_errors
- test_addReceiptLetter_raises_resource_not_found_with_invalid_table
- test_addReceiptLetter_raises_validation_error
- test_addReceiptLetter_raises_access_denied

### Add Receipt Letters Tests
- test_addReceiptLetters_success
- test_addReceiptLetters_with_large_batch
- test_addReceiptLetters_with_unprocessed_items_retries
- test_addReceiptLetters_invalid_parameters
- test_addReceiptLetters_raises_resource_not_found_with_invalid_table
- test_addReceiptLetters_client_errors

### Update Receipt Letter Tests
- test_updateReceiptLetter_success
- test_updateReceiptLetter_invalid_parameters
- test_updateReceiptLetter_client_errors

### Update Receipt Letters Tests
- test_updateReceiptLetters_success
- test_updateReceiptLetters_with_large_batch
- test_updateReceiptLetters_invalid_inputs
- test_updateReceiptLetters_client_errors

### Delete Receipt Letter Tests
- test_deleteReceiptLetter_success
- test_deleteReceiptLetter_invalid_parameters
- test_deleteReceiptLetter_client_errors

### Delete Receipt Letters Tests
- test_deleteReceiptLetters_success
- test_deleteReceiptLetters_with_large_batch
- test_deleteReceiptLetters_with_unprocessed_items
- test_deleteReceiptLetters_invalid_parameters
- test_deleteReceiptLetters_client_errors

### Get Receipt Letter Tests
- test_getReceiptLetter_success
- test_getReceiptLetter_not_found
- test_getReceiptLetter_invalid_parameters
- test_getReceiptLetter_client_errors

### List Receipt Letters Tests
#### Basic Operations
- test_listReceiptLetters_success
- test_listReceiptLetters_with_limit
- test_listReceiptLetters_with_pagination
- test_listReceiptLetters_multiple_pages

#### Parameter Validation
- test_listReceiptLetters_invalid_parameters

#### Error Handling
- test_listReceiptLetters_client_errors

#### Pagination Error Handling
- test_listReceiptLetters_pagination_errors

### List Receipt Letters From Word Tests
#### Basic Operations
- test_listReceiptLettersFromWord_success
- test_listReceiptLettersFromWord_returns_empty_list_when_not_found
- test_listReceiptLettersFromWord_with_pagination

#### Parameter Validation
- test_listReceiptLettersFromWord_invalid_parameters
  - None value tests for required parameters (receipt_id, image_id, line_id, word_id)
  - Invalid type tests (non-integer IDs, invalid UUID)

#### Error Handling
- test_listReceiptLettersFromWord_client_errors
  - ResourceNotFoundException
  - ProvisionedThroughputExceededException
  - ValidationException
  - InternalServerError
  - AccessDeniedException
  - UnknownError

#### Pagination Error Handling
- test_listReceiptLettersFromWord_pagination_errors
  - ResourceNotFoundException during pagination
  - ProvisionedThroughputExceededException during pagination
  - ValidationException during pagination
  - InternalServerError during pagination
  - AccessDeniedException during pagination
  - UnknownError during pagination
