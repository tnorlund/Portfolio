# Error Handler Fixes Summary

## Overview
Added missing error handlers to update/delete methods that use `transact_write_items` in the receipt_dynamo package.

## Files Modified

### 1. `/Users/tnorlund/GitHub/issue-121/receipt_dynamo/receipt_dynamo/data/_receipt.py`
- **Method**: `delete_receipts`
- **Added error handlers for**:
  - `TransactionCanceledException` (with ConditionalCheckFailed check)
  - `ResourceNotFoundException`
  - Changed else clause from `ValueError` to `DynamoDBError`

### 2. `/Users/tnorlund/GitHub/issue-121/receipt_dynamo/receipt_dynamo/data/_receipt_metadata.py`
- **Added imports**: DynamoDB exception classes from `shared_exceptions.py`
- **Method**: `update_receipt_metadatas`
- **Added error handlers for**:
  - `TransactionCanceledException` (with ConditionalCheckFailed check)
  - `AccessDeniedException`
  - Changed all `ValueError` exceptions to appropriate DynamoDB exceptions
  - Changed else clause to use `DynamoDBError`
- **Method**: `delete_receipt_metadatas`
- **Added error handlers for**:
  - `TransactionCanceledException` (with ConditionalCheckFailed check)
  - `AccessDeniedException`
  - Changed all `ValueError` exceptions to appropriate DynamoDB exceptions
  - Changed else clause to use `DynamoDBError`

### 3. `/Users/tnorlund/GitHub/issue-121/receipt_dynamo/receipt_dynamo/data/_receipt_word_label.py`
- **Method**: `delete_receipt_word_labels`
- **Added error handlers for**:
  - `TransactionCanceledException` (with ConditionalCheckFailed check)
  - `ResourceNotFoundException`
  - Changed else clause from `ValueError` to `DynamoDBError`

### 4. `/Users/tnorlund/GitHub/issue-121/receipt_dynamo/receipt_dynamo/data/_batch_summary.py`
- **Added imports**: DynamoDB exception classes from `shared_exceptions.py`
- **Method**: `update_batch_summaries`
- **Added error handlers for**:
  - `TransactionCanceledException` (with ConditionalCheckFailed check)
  - `AccessDeniedException`
  - Changed all `ValueError` exceptions to appropriate DynamoDB exceptions
  - Changed else clause to use `DynamoDBError`
- **Method**: `delete_batch_summaries`
- **Added error handlers for**:
  - `TransactionCanceledException` (with ConditionalCheckFailed check)
  - `AccessDeniedException`
  - Changed all `ValueError` exceptions to appropriate DynamoDB exceptions
  - Changed else clause to use `DynamoDBError`

### 5. `/Users/tnorlund/GitHub/issue-121/receipt_dynamo/receipt_dynamo/data/_image.py`
- **Method**: `update_images`
- **Added error handlers for**:
  - `TransactionCanceledException` (with ConditionalCheckFailed check)
  - Changed `ResourceNotFoundException` handler from `ValueError` to `DynamoDBError`

## Complete Error Handling Pattern Applied

All modified methods now follow this complete error handling pattern:

```python
except ClientError as e:
    error_code = e.response.get("Error", {}).get("Code", "")
    if error_code == "ConditionalCheckFailedException":
        raise ValueError("One or more items do not exist") from e
    elif error_code == "TransactionCanceledException":
        if "ConditionalCheckFailed" in str(e):
            raise ValueError("One or more items do not exist") from e
        else:
            raise DynamoDBError(f"Transaction canceled: {e}") from e
    elif error_code == "ProvisionedThroughputExceededException":
        raise DynamoDBThroughputError(f"Provisioned throughput exceeded: {e}") from e
    elif error_code == "InternalServerError":
        raise DynamoDBServerError(f"Internal server error: {e}") from e
    elif error_code == "ValidationException":
        raise DynamoDBValidationError(f"One or more parameters given were invalid: {e}") from e
    elif error_code == "AccessDeniedException":
        raise DynamoDBAccessError(f"Access denied: {e}") from e
    elif error_code == "ResourceNotFoundException":
        raise DynamoDBError(f"Resource not found: {e}") from e
    else:
        raise DynamoDBError(f"Error message: {e}") from e
```

## Key Improvements

1. **Consistent error handling**: All update/delete methods using `transact_write_items` now handle the same set of common DynamoDB errors.

2. **Proper exception types**: Replaced generic `ValueError` exceptions with appropriate DynamoDB-specific exceptions from `shared_exceptions.py`.

3. **Transaction cancellation handling**: Added specific handling for `TransactionCanceledException` with a check for `ConditionalCheckFailed` to properly distinguish between conditional check failures and other transaction cancellations.

4. **Complete error coverage**: All methods now handle:
   - ConditionalCheckFailedException
   - TransactionCanceledException
   - ProvisionedThroughputExceededException
   - InternalServerError
   - ValidationException
   - AccessDeniedException
   - ResourceNotFoundException
   - Generic catch-all with DynamoDBError

5. **Consistent error messages**: Error messages now consistently use the proper exception types and include the original error for debugging.
