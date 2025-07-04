# DynamoDB Duplicate Code Reduction Strategy

## Overview

The receipt_dynamo package currently has 922 instances of duplicate/similar code (R0801), primarily in the data access layer. This document outlines a comprehensive strategy to reduce code duplication while maintaining functionality and improving maintainability.

## Current State Analysis

### Main Areas of Duplication

1. **Exception Handling (386+ instances)**
   - Every DynamoDB operation has nearly identical ClientError handling
   - Same error codes checked in same order across all files
   - Similar error messages with only entity names changing

2. **Batch Operations (100+ instances)**
   - Identical chunking logic (25 items per batch)
   - Same retry pattern for unprocessed items
   - Repeated across add/delete operations for all entities

3. **Transaction Operations (80+ instances)**
   - Same transaction chunking pattern
   - Identical error handling for transaction failures
   - Repeated in update/delete methods

4. **Validation Logic (150+ instances)**
   - Null checks and type validation repeated in every method
   - Same parameter validation patterns
   - UUID validation repeated across files

5. **Query/Pagination Logic (60+ instances)**
   - LastEvaluatedKey handling duplicated
   - Similar query construction patterns
   - Repeated pagination loops

## Proposed Solution Architecture

### 1. Base Class Hierarchy

```python
# base.py
class DynamoDBBaseOperations(DynamoClientProtocol):
    """Base class for all DynamoDB operations with common functionality"""

    def _handle_client_error(self, error: ClientError, operation: str, context: dict = None):
        """Centralized error handling for all DynamoDB operations"""

    def _batch_write_with_retry(self, items: list, operation_type: str):
        """Generic batch write with automatic retry for unprocessed items"""

    def _validate_entity(self, entity: Any, entity_class: type, param_name: str):
        """Common entity validation logic"""

    def _paginated_query(self, query_params: dict) -> tuple[list, dict]:
        """Generic paginated query handler"""
```

### 2. Mixin Classes

```python
# mixins.py
class SingleEntityCRUDMixin:
    """Mixin for single entity CRUD operations"""

    def _add_entity(self, entity, condition_expression="attribute_not_exists(PK)"):
        """Generic add operation with error handling"""

    def _update_entity(self, entity, condition_expression="attribute_exists(PK)"):
        """Generic update operation with error handling"""

    def _delete_entity(self, entity, condition_expression="attribute_exists(PK)"):
        """Generic delete operation with error handling"""

class BatchOperationsMixin:
    """Mixin for batch operations"""

    def _add_entities_batch(self, entities, entity_class):
        """Generic batch add with chunking and retry"""

    def _delete_entities_batch(self, entities):
        """Generic batch delete with chunking and retry"""

class TransactionalOperationsMixin:
    """Mixin for transactional operations"""

    def _update_entities_transactional(self, entities, condition_expression):
        """Generic transactional update"""

    def _delete_entities_transactional(self, entities, condition_expression):
        """Generic transactional delete"""
```

### 3. Error Handling Decorator

```python
# decorators.py
def handle_dynamodb_errors(operation_name: str):
    """Decorator to handle DynamoDB errors consistently"""
    def decorator(func):
        @wraps(func)
        def wrapper(self, *args, **kwargs):
            try:
                return func(self, *args, **kwargs)
            except ClientError as e:
                return self._handle_client_error(e, operation_name,
                                               context={'args': args, 'kwargs': kwargs})
        return wrapper
    return decorator
```

## Implementation Plan

### Phase 1: Create Base Infrastructure (Week 1)
1. Create `base.py` with `DynamoDBBaseOperations` class
2. Implement common error handling method
3. Create comprehensive test suite for base functionality
4. Document the new base class patterns

### Phase 2: Implement Mixins (Week 2)
1. Create mixin classes for different operation types
2. Add comprehensive tests for each mixin
3. Create usage examples and documentation

### Phase 3: Refactor One Entity as Proof of Concept (Week 3)
1. Choose `_receipt.py` as the pilot entity
2. Refactor to use base class and mixins
3. Ensure all tests pass
4. Measure code reduction and complexity improvement

### Phase 4: Systematic Refactoring (Weeks 4-6)
1. Refactor remaining entities in groups:
   - Core entities: Image, Word, Line, Letter
   - Receipt entities: Receipt, ReceiptMetadata, ReceiptField
   - Analysis entities: ReceiptLabelAnalysis, ReceiptValidation*
   - Other entities: GPT, Queue, Instance, etc.
2. Update tests for each group
3. Ensure backward compatibility

### Phase 5: Optimization and Cleanup (Week 7)
1. Remove any remaining duplication
2. Optimize base class methods based on usage patterns
3. Update documentation
4. Performance testing

## Example: Before and After

### Before (Current Implementation)
```python
# _receipt.py (200+ lines per CRUD operation set)
def add_receipt(self, receipt: Receipt):
    if receipt is None:
        raise ValueError("receipt parameter is required and cannot be None.")
    if not isinstance(receipt, Receipt):
        raise ValueError("receipt must be an instance of the Receipt class.")

    try:
        self._client.put_item(
            TableName=self.table_name,
            Item=receipt.to_item(),
            ConditionExpression="attribute_not_exists(PK)"
        )
    except ClientError as e:
        error_code = e.response.get("Error", {}).get("Code", "")
        if error_code == "ConditionalCheckFailedException":
            raise ValueError(f"Receipt already exists") from e
        elif error_code == "ResourceNotFoundException":
            raise DynamoDBError(f"Table not found") from e
        # ... 10+ more error conditions ...
```

### After (With Base Class)
```python
# _receipt.py (20-30 lines per CRUD operation set)
class _Receipt(DynamoDBBaseOperations, SingleEntityCRUDMixin):

    @handle_dynamodb_errors("add_receipt")
    def add_receipt(self, receipt: Receipt):
        self._validate_entity(receipt, Receipt, "receipt")
        return self._add_entity(receipt)

    @handle_dynamodb_errors("add_receipts")
    def add_receipts(self, receipts: list[Receipt]):
        self._validate_entity_list(receipts, Receipt, "receipts")
        return self._add_entities_batch(receipts, Receipt)
```

## Success Metrics

1. **Code Reduction**: Target 60-70% reduction in data layer code
2. **Test Coverage**: Maintain or improve current coverage
3. **Performance**: No regression in operation performance
4. **Maintainability**: Reduce time to add new entity types by 80%
5. **Bug Reduction**: Centralized error handling should reduce error-related bugs

## Risks and Mitigation

1. **Risk**: Breaking existing functionality
   - **Mitigation**: Comprehensive test coverage before refactoring
   - **Mitigation**: Phased rollout with careful testing

2. **Risk**: Performance degradation from abstraction
   - **Mitigation**: Performance benchmarks before/after
   - **Mitigation**: Profile critical paths

3. **Risk**: Over-abstraction making code harder to understand
   - **Mitigation**: Clear documentation and examples
   - **Mitigation**: Regular code reviews during implementation

## Alternative Approaches Considered

1. **Code Generation**: Generate data access code from entity definitions
   - Rejected due to complexity and maintenance overhead

2. **Functional Approach**: Use higher-order functions instead of classes
   - Rejected as less intuitive for team familiar with OOP

3. **External ORM**: Use existing DynamoDB ORM library
   - Rejected to maintain full control over DynamoDB interactions

## Conclusion

This strategy will significantly reduce code duplication while improving maintainability and consistency. The phased approach allows for validation at each step and minimizes risk. The investment in refactoring will pay dividends in reduced bugs, easier maintenance, and faster feature development.
