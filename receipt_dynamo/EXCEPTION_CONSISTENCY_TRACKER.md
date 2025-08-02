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

## Completion Status

**✅ ALL FILES HAVE BEEN UPDATED!**

### Summary of Changes
- **Total Files Updated**: 38 (includes all data access files)
- **ValueError Fixed**: 489 → 0 ✅
- **RuntimeError Fixed**: 8 → 0 ✅
- **Total Issues Fixed**: 497

### Automated Fix Results
- Script: `fix_exceptions.py`
- Execution Time: < 5 seconds
- Success Rate: 99.4% (494/497 fixed automatically)
- Manual Fixes: 3 RuntimeError instances in _ocr_job.py and _ocr_routing_decision.py

## Files Updated

### Priority 1 - High ValueError Count (20+ occurrences)
- [x] `_receipt_metadata.py` - 36 ValueError ✅
- [x] `_receipt_word_label.py` - 35 ValueError ✅
- [x] `_receipt_validation_result.py` - 29 ValueError ✅
- [x] `_receipt_word.py` - 26 ValueError ✅
- [x] `_receipt_line.py` - 24 ValueError ✅
- [x] `_receipt_label_analysis.py` - 24 ValueError ✅

### Priority 2 - Medium ValueError Count (10-20 occurrences)
- [x] `_ocr_routing_decision.py` - 20 ValueError + 2 RuntimeError ✅
- [x] `_job_resource.py` - 20 ValueError ✅
- [x] `_receipt_letter.py` - 19 ValueError ✅
- [x] `_receipt_field.py` - 19 ValueError ✅
- [x] `_receipt_line_item_analysis.py` - 18 ValueError ✅
- [x] `_receipt_structure_analysis.py` - 18 ValueError ✅
- [x] `_receipt_section.py` - 16 ValueError ✅
- [x] `_job_log.py` - 16 ValueError ✅
- [x] `_instance.py` - 15 ValueError ✅
- [x] `_letter.py` - 13 ValueError ✅
- [x] `_job_dependency.py` - 12 ValueError ✅
- [x] `_receipt_validation_category.py` - 12 ValueError ✅
- [x] `_embedding_batch_result.py` - 11 ValueError ✅

### Priority 3 - Low ValueError Count (<10 occurrences)
- [x] `_job_checkpoint.py` - 9 ValueError ✅
- [x] `_completion_batch_result.py` - 9 ValueError ✅
- [x] `_places_cache.py` - 9 ValueError ✅
- [x] `_image.py` - 9 ValueError ✅
- [x] `_job.py` - 8 ValueError ✅
- [x] `_label_count_cache.py` - 8 ValueError ✅
- [x] `_job_metric.py` - 7 ValueError ✅
- [x] `_word.py` - 7 ValueError ✅
- [x] `_line.py` - 6 ValueError ✅
- [x] `_queue.py` - 5 ValueError ✅
- [x] `_receipt.py` - 4 ValueError ✅
- [x] `_ai_usage_metric.py` - 3 ValueError ✅
- [x] `_job_status.py` - 2 ValueError ✅
- [x] `_receipt_chatgpt_validation.py` - 1 ValueError ✅
- [x] `_receipt_validation_summary.py` - 1 ValueError ✅

### RuntimeError Files
- [x] `_ocr_job.py` - 4 RuntimeError → 3 DynamoDBThroughputError + 1 fixed in script ✅
- [x] `_ocr_routing_decision.py` - 4 RuntimeError → 1 DynamoDBThroughputError + 1 DynamoDBError + 2 fixed in script ✅

### Additional Files Fixed
- [x] `_batch_summary.py` - 10 ValueError → EntityValidationError ✅
- [x] `_embedding_batch.py` - 7 ValueError ✅
- [x] `_embedding_index.py` - 4 ValueError ✅
- [x] `_model_configuration.py` - 3 ValueError ✅

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