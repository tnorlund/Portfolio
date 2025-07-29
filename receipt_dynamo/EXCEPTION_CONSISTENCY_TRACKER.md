# Exception Consistency Tracker

This file tracks the progress of replacing inconsistent exceptions with the proper shared exceptions from `receipt_dynamo/data/shared_exceptions.py`.

## Exception Mapping Rules

1. **ValueError** for parameter validation → `EntityValidationError`
2. **ValueError** for entity not found → `EntityNotFoundError`
3. **RuntimeError** for throughput exceeded → `DynamoDBThroughputError`
4. **RuntimeError** for internal server error → `DynamoDBServerError`
5. **RuntimeError** for access denied → `DynamoDBAccessError`
6. **RuntimeError** for general errors → `OperationError`
7. **Exception** (generic) → Appropriate specific exception

## Files to Update

### Priority 1 - High ValueError Count (20+ occurrences)
- [ ] `_receipt_metadata.py` - 36 ValueError
- [ ] `_receipt_word_label.py` - 35 ValueError
- [ ] `_receipt_validation_result.py` - 29 ValueError
- [ ] `_receipt_word.py` - 26 ValueError
- [ ] `_receipt_line.py` - 24 ValueError
- [ ] `_receipt_label_analysis.py` - 24 ValueError

### Priority 2 - Medium ValueError Count (10-20 occurrences)
- [ ] `_ocr_routing_decision.py` - 20 ValueError
- [ ] `_job_resource.py` - 20 ValueError
- [ ] `_receipt_letter.py` - 19 ValueError
- [ ] `_receipt_field.py` - 19 ValueError
- [ ] `_receipt_line_item_analysis.py` - 18 ValueError
- [ ] `_receipt_structure_analysis.py` - 18 ValueError
- [ ] `_receipt_section.py` - 16 ValueError
- [ ] `_job_log.py` - 16 ValueError
- [ ] `_instance.py` - 15 ValueError
- [ ] `_letter.py` - 13 ValueError
- [ ] `_job_dependency.py` - 12 ValueError
- [ ] `_receipt_validation_category.py` - 12 ValueError
- [ ] `_embedding_batch_result.py` - 11 ValueError

### Priority 3 - Low ValueError Count (<10 occurrences)
- [ ] `_job_checkpoint.py` - 9 ValueError
- [ ] `_completion_batch_result.py` - 9 ValueError
- [ ] `_places_cache.py` - 9 ValueError
- [ ] `_image.py` - 9 ValueError
- [ ] `_job.py` - 8 ValueError
- [ ] `_label_count_cache.py` - 8 ValueError
- [ ] `_job_metric.py` - 7 ValueError
- [ ] `_word.py` - 7 ValueError
- [ ] `_line.py` - 6 ValueError
- [ ] `_queue.py` - 5 ValueError
- [ ] `_receipt.py` - 4 ValueError
- [ ] `_ai_usage_metric.py` - 3 ValueError
- [ ] `_job_status.py` - 2 ValueError
- [ ] `_receipt_chatgpt_validation.py` - 1 ValueError
- [ ] `_receipt_validation_summary.py` - 1 ValueError

### RuntimeError Files
- [ ] `_ocr_job.py` - 4 RuntimeError
- [ ] `_ocr_routing_decision.py` - 4 RuntimeError

### Completed Files
- [x] `_batch_summary.py` - 10 ValueError → EntityValidationError ✅

## Progress Summary
- **Total Files**: 36
- **Completed**: 1
- **Remaining**: 35
- **Total ValueError to fix**: ~491 (501 - 10 fixed)
- **Total RuntimeError to fix**: 8

## Common Patterns to Replace

### Pattern 1: Parameter Validation
```python
# Before
if not isinstance(param, expected_type):
    raise ValueError("param must be expected_type")

# After
if not isinstance(param, expected_type):
    raise EntityValidationError("param must be expected_type")
```

### Pattern 2: Entity Not Found
```python
# Before
if result is None:
    raise ValueError(f"Entity with id {id} does not exist")

# After
if result is None:
    raise EntityNotFoundError(f"Entity with id {id} does not exist")
```

### Pattern 3: DynamoDB Errors
```python
# Before
except ClientError as e:
    if error_code == "ProvisionedThroughputExceededException":
        raise RuntimeError(f"Provisioned throughput exceeded: {e}")

# After
except ClientError as e:
    if error_code == "ProvisionedThroughputExceededException":
        raise DynamoDBThroughputError(f"Provisioned throughput exceeded: {e}")
```

## Import Updates Required

Each file needs to import the appropriate exceptions:
```python
from receipt_dynamo.data.shared_exceptions import (
    EntityValidationError,  # For ValueError replacements
    EntityNotFoundError,    # For "not found" errors
    DynamoDBThroughputError,  # For throughput RuntimeError
    DynamoDBServerError,      # For server RuntimeError
    DynamoDBAccessError,      # For access RuntimeError
    OperationError,           # For general operation errors
)
```

## Notes
- Files using `@handle_dynamodb_errors` decorator shouldn't need manual ClientError handling
- Some files may need refactoring to use base operations instead of manual error handling
- Update docstrings to reflect the new exception types