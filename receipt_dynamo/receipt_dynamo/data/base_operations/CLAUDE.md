# Claude AI Assistant Guide - Base Operations Module

This guide helps Claude understand and work with the base_operations module effectively.

## Quick Context

The base_operations module provides reusable mixins for DynamoDB operations using composition-over-inheritance. All mixins follow the same pattern: they expect `table_name` and `_client` attributes from the implementing class.

## Current State (2025-01-27)

- **Pylint Score**: 10.00/10 (perfect)
- **Type Safety**: Full mypy strict mode compatibility
- **Test Coverage**: Comprehensive with all tests passing
- **Recent Changes**: 
  - Added `__init__` method to DynamoDBBaseOperations for proper initialization
  - Removed unused `operation_name` parameter from `_update_entities`
  - Fixed all pylint warnings while maintaining functionality

## Common AI Tasks

### 1. Adding a New Operation to a Mixin

```python
# In mixins.py - follow existing patterns
@handle_dynamodb_errors("operation_name")
def _new_operation(self, entities: List[Entity]) -> None:
    """Document what this does."""
    self._validate_entity_list(entities, Entity, "entities")
    # Implementation...
```

### 2. Fixing Error Messages

When tests fail due to error message mismatches:
1. Check `error_config.py` for the expected pattern
2. Update `ENTITY_NOT_FOUND_PATTERNS` or `OPERATION_MESSAGES`
3. Never change test expectations - fix the config instead

### 3. Handling Pylint Warnings

- **Unused arguments**: Prefix with underscore (e.g., `_context`)
- **Import issues**: Move imports to module level
- **Protected access**: Use `# pylint: disable=protected-access` sparingly

## Key Patterns to Maintain

### TYPE_CHECKING Pattern
```python
class SomeMixin:
    if TYPE_CHECKING:
        table_name: str
        _client: "DynamoDBClient"
```
This provides type safety without runtime overhead.

### Error Decorator Usage
Always use `@handle_dynamodb_errors("operation_name")` on methods that call DynamoDB.

### Validation Pattern
```python
self._validate_entity(entity, EntityClass, "param_name")
self._validate_entity_list(entities, EntityClass, "param_name")
```

## Common Pitfalls

1. **Don't add runtime type checks** - Use TYPE_CHECKING pattern
2. **Don't catch ClientError directly** - Use the decorator
3. **Don't skip validation** - Always validate inputs
4. **Don't break existing tests** - Update error configs instead

## Testing Guidance

When modifying this module:
1. Run integration tests: `pytest tests/integration/test__*.py -k update`
2. Check pylint: `python -m pylint receipt_dynamo/data/base_operations`
3. Verify type safety: `mypy receipt_dynamo/data/base_operations --strict`

## Migration Checklist

When updating a data access class to use these mixins:
- [ ] Inherit from appropriate mixins
- [ ] Remove duplicate error handling code
- [ ] Update method calls to use mixin methods
- [ ] Preserve public API signatures
- [ ] Add integration tests for error scenarios

## Quick Reference

- **Error handling**: `error_handlers.py` - centralized error processing
- **Validation**: `validators.py` - input validation and messages  
- **Mixins**: `mixins.py` - reusable operation patterns
- **Config**: `error_config.py` - error message patterns
- **Base**: `base.py` - core functionality and initialization