# Mixin Consolidation Guide

## Overview

To address pylint's "too-many-ancestors" warning, we've created consolidated mixins that group related functionality. This reduces inheritance depth from 6+ ancestors to just 2-3.

## Migration Strategy

### Before (6 ancestors):
```python
class _Receipt(
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    BatchOperationsMixin,
    TransactionalOperationsMixin,
    QueryByTypeMixin,
    CommonValidationMixin,
):
    pass
```

### After (2 ancestors):
```python
from receipt_dynamo.data.base_operations import (
    DynamoDBBaseOperations,
    FullDynamoEntityMixin,  # Includes all the above mixins
)

class _Receipt(
    DynamoDBBaseOperations,
    FullDynamoEntityMixin,
):
    pass
```

## Consolidated Mixin Patterns

### 1. **FullDynamoEntityMixin** (Most Common - 80% of entities)
Use for entities that need complete CRUD, query, and validation capabilities.

**Includes:**
- SingleEntityCRUDMixin
- BatchOperationsMixin
- TransactionalOperationsMixin
- QueryByTypeMixin
- QueryByParentMixin
- CommonValidationMixin

**Examples:** `_Receipt`, `_Job`, `_Image`, `_ReceiptLine`, `_ReceiptField`

### 2. **WriteOperationsMixin**
For entities that need all write operations but custom queries.

**Includes:**
- SingleEntityCRUDMixin
- BatchOperationsMixin
- TransactionalOperationsMixin

**Use when:** You need custom query patterns but standard write operations.

### 3. **CacheDynamoEntityMixin**
For cache and metrics entities that primarily write in batches.

**Includes:**
- BatchOperationsMixin
- QueryByTypeMixin

**Examples:** `_AIUsageMetric`, `_BatchSummary`, `_LabelCountCache`

### 4. **SimpleDynamoEntityMixin**
For basic entities with minimal operations.

**Includes:**
- SingleEntityCRUDMixin
- CommonValidationMixin

**Use when:** You only need basic CRUD, no batch or complex queries.

### 5. **ReadOptimizedDynamoEntityMixin**
For read-heavy entities with minimal writes.

**Includes:**
- QueryByTypeMixin
- QueryByParentMixin
- CommonValidationMixin

**Use when:** Entity is mostly read-only or rarely updated.

## Migration Examples

### Example 1: Standard Entity
```python
# Before
class _ReceiptLine(
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    BatchOperationsMixin,
    TransactionalOperationsMixin,
    QueryByTypeMixin,
    CommonValidationMixin,
):
    pass

# After
class _ReceiptLine(
    DynamoDBBaseOperations,
    FullDynamoEntityMixin,
):
    pass
```

### Example 2: Cache Entity
```python
# Before
class _AIUsageMetric(
    DynamoDBBaseOperations,
    BatchOperationsMixin,
):
    pass

# After
class _AIUsageMetric(
    DynamoDBBaseOperations,
    CacheDynamoEntityMixin,
):
    pass
```

### Example 3: Custom Pattern
```python
# If you need a custom combination
class _SpecialEntity(
    DynamoDBBaseOperations,
    WriteOperationsMixin,    # All write operations
    CommonValidationMixin,   # Validation only
):
    # Add custom query methods here
    pass
```

## Benefits

1. **Reduces ancestors** from 6 to 2 on average
2. **Eliminates pylint warnings** about too-many-ancestors
3. **Improves readability** - clearer what capabilities each class has
4. **Maintains all functionality** - no behavior changes
5. **Easier to understand** - fewer inheritance levels to trace

## Migration Checklist

- [ ] Identify which consolidated mixin fits your entity's needs
- [ ] Update imports to include the consolidated mixin
- [ ] Replace individual mixins with the consolidated version
- [ ] Verify all tests still pass
- [ ] Check that pylint warnings are resolved

## Quick Decision Tree

```
Does the entity need full CRUD + queries + validation?
  → Yes: Use FullDynamoEntityMixin
  → No: Continue...

Is it primarily for caching/metrics?
  → Yes: Use CacheDynamoEntityMixin
  → No: Continue...

Is it read-heavy with minimal writes?
  → Yes: Use ReadOptimizedDynamoEntityMixin
  → No: Continue...

Does it only need basic CRUD?
  → Yes: Use SimpleDynamoEntityMixin
  → No: Use WriteOperationsMixin + custom queries
```