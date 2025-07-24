# Alternative Code Duplication Reduction Strategy

## Overview

Since migrating existing files to `base_operations` proved problematic due to integration test dependencies, this document outlines alternative strategies to reduce code duplication while maintaining backward compatibility.

## Strategy 1: Shared Validation Utilities

### Current Duplication
Many files duplicate validation logic:
```python
# Repeated in multiple files
if not isinstance(limit, int):
    raise ValueError("Limit must be an integer")
if limit <= 0:
    raise ValueError("Limit must be greater than 0")
```

### Solution
Create `receipt_dynamo/data/validation.py`:
```python
def validate_pagination_params(limit=None, last_evaluated_key=None):
    """Shared validation for pagination parameters."""
    if limit is not None:
        if not isinstance(limit, int):
            raise ValueError("Limit must be an integer")
        if limit <= 0:
            raise ValueError("Limit must be greater than 0")
    
    if last_evaluated_key is not None:
        if not isinstance(last_evaluated_key, dict):
            raise ValueError("LastEvaluatedKey must be a dictionary")
        validate_last_evaluated_key(last_evaluated_key)
```

## Strategy 2: Error Handling Helper Functions

### Current Duplication
ClientError handling is repeated with slight variations:
```python
except ClientError as e:
    error_code = e.response.get("Error", {}).get("Code", "")
    if error_code == "ResourceNotFoundException":
        raise DynamoDBError(f"Could not perform operation: {e}") from e
    # ... more conditions
```

### Solution
Create error handling helpers that preserve specific messages:
```python
def handle_client_error(e: ClientError, operation: str, entity_details: str = ""):
    """Handle ClientError with entity-specific messages."""
    error_code = e.response.get("Error", {}).get("Code", "")
    
    if error_code == "ConditionalCheckFailedException":
        # Preserve entity-specific error messages
        raise ValueError(f"{entity_details} already exists") from e
    elif error_code == "ResourceNotFoundException":
        raise DynamoDBError(f"Could not {operation}: {e}") from e
    # ... handle other cases
```

## Strategy 3: Query Builder Pattern

### Current Duplication
Query parameter construction is repeated:
```python
query_params = {
    "TableName": self.table_name,
    "KeyConditionExpression": "PK = :pk",
    "ExpressionAttributeValues": {":pk": {"S": f"ENTITY#{id}"}},
}
if limit is not None:
    query_params["Limit"] = limit
```

### Solution
Create a query builder:
```python
class DynamoQueryBuilder:
    def __init__(self, table_name: str):
        self.params = {"TableName": table_name}
    
    def with_key_condition(self, expression: str, values: dict):
        self.params["KeyConditionExpression"] = expression
        self.params["ExpressionAttributeValues"] = values
        return self
    
    def with_limit(self, limit: Optional[int]):
        if limit is not None:
            self.params["Limit"] = limit
        return self
    
    def build(self) -> dict:
        return self.params
```

## Strategy 4: Entity-Specific Mixins

### Current Challenge
Base operations can't preserve entity-specific error messages.

### Solution
Create entity-aware mixins that maintain backward compatibility:
```python
class QueueOperationsMixin:
    """Queue-specific operations that preserve error message format."""
    
    def _add_queue_with_handling(self, queue: Queue):
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=queue.to_item(),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                # Preserve exact error message format
                raise ValueError(f"Queue {queue.queue_name} already exists") from e
            raise
```

## Strategy 5: Batch Operation Utilities

### Current Duplication
Batch operations with retry logic are repeated:
```python
while unprocessed_items:
    time.sleep(0.5)
    response = self._client.batch_write_item(RequestItems=unprocessed_items)
    unprocessed_items = response.get("UnprocessedItems", {})
```

### Solution
Create reusable batch utilities:
```python
class BatchOperationHelper:
    @staticmethod
    def batch_write_with_retry(client, table_name: str, items: list, 
                              transform_func=None, max_retries: int = 3):
        """Handle batch writes with automatic retry logic."""
        # Implementation here
```

## Implementation Plan

### Phase 1: Create Shared Utilities (Week 1)
1. Create `validation.py` with common validation functions
2. Create `error_helpers.py` with error handling utilities
3. Create `query_builders.py` with query construction helpers

### Phase 2: Gradual Adoption (Weeks 2-4)
1. Update files to use shared utilities where possible
2. Maintain backward compatibility for error messages
3. Add unit tests for all shared utilities

### Phase 3: Documentation (Week 5)
1. Document all shared utilities
2. Create migration guide for developers
3. Update code review checklist

### Phase 4: Enforce Standards (Ongoing)
1. Require use of shared utilities in new code
2. Gradually refactor old code during feature work
3. Track reduction in code duplication metrics

## Benefits

1. **Reduces duplication** without breaking tests
2. **Maintains backward compatibility** for error messages
3. **Improves maintainability** through centralized logic
4. **Enables gradual migration** without big-bang changes
5. **Preserves specific error messages** that tests depend on

## Metrics for Success

- [ ] 50% reduction in validation code duplication
- [ ] 40% reduction in error handling duplication
- [ ] 30% reduction in query construction duplication
- [ ] Zero integration test failures due to changes
- [ ] Improved code review velocity due to standardization

## Example Migration

### Before (in `_queue.py`):
```python
def add_queue(self, queue: Queue) -> None:
    if queue is None:
        raise ValueError("queue cannot be None")
    if not isinstance(queue, Queue):
        raise ValueError("queue must be an instance of Queue")
    
    try:
        self._client.put_item(...)
    except ClientError as e:
        if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
            raise ValueError(f"Queue {queue.queue_name} already exists") from e
        raise
```

### After:
```python
def add_queue(self, queue: Queue) -> None:
    validate_entity(queue, Queue, "queue")  # Shared validation
    
    try:
        self._client.put_item(...)
    except ClientError as e:
        handle_queue_error(e, "add", queue)  # Preserves specific messages
```

## Conclusion

This alternative strategy provides a pragmatic path to reduce code duplication while maintaining backward compatibility. It acknowledges the reality of existing integration tests while still improving code quality and maintainability.