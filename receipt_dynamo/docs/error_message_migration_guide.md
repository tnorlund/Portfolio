# Error Message Migration Guide

This guide documents the error message changes made during the base_operations refactoring and provides guidance for updating tests and code that depend on specific error message formats.

## Overview

During Phase 6 of the base_operations refactoring, error messages were standardized to improve consistency while maintaining backward compatibility. This guide helps developers update their tests and code to work with the new error message patterns.

## Error Message Changes

### EntityAlreadyExistsError Messages

#### Previous Patterns
- `"Entity already exists: {EntityType}"`
- `"Entity already exists: {EntityType} with {field}={value}"`
- Various entity-specific patterns

#### New Standardized Pattern
Most entities now use a simplified pattern:
- `"already exists"`

Some entities retain specific patterns for backward compatibility:
- Queue: `"Queue {queue_name} already exists"`
- JobCheckpoint: `"Entity already exists: JobCheckpoint with job_id={job_id}"`
- ReceiptChatGPTValidation: `"Entity already exists: ReceiptChatGPTValidation with receipt_id={receipt_id}"`

### EntityNotFoundError Messages

#### Previous Patterns
- `"Entity does not exist: {EntityType}"`
- `"{EntityType} not found"`
- `"{EntityType} with {field} {value} not found"`

#### New Standardized Patterns
Default pattern:
- `"Entity does not exist: {EntityType}"`

Entity-specific patterns retained for backward compatibility:
- Job: `"Job with job id {job_id} does not exist"`
- Instance: `"Instance with instance id {instance_id} does not exist"`
- Queue: `"Queue {queue_name} not found"`
- Receipt: `"Receipt with receipt_id {receipt_id} does not exist"`
- Image: `"Image with image_id {image_id} does not exist"`
- ReceiptField: `"does not exist"`
- ReceiptWordLabel: `"Entity does not exist: ReceiptWordLabel"`
- QueueJob: `"Entity does not exist: QueueJob"`

### ValidationError Messages

#### Parameter Validation
Required parameter messages:
- Standard: `"{param} cannot be None"`
- Special cases:
  - JobCheckpoint: `"JobCheckpoint parameter is required and cannot be None."`
  - ReceiptLabelAnalysis: `"ReceiptLabelAnalysis parameter is required and cannot be None."`
  - ReceiptWordLabel: `"ReceiptWordLabel parameter is required and cannot be None."`

Type mismatch messages:
- Standard: `"{param} must be an instance of the {ClassName} class."`
- Special cases use camelCase for some parameters (e.g., `receiptField`)

List validation messages:
- List required: `"{param} must be a list"`
- List type mismatch: Various patterns for different entity types

## Migration Strategy

### For Test Files

1. **Reduce Specificity**: Consider making tests less dependent on exact error message text:
   ```python
   # Before
   with pytest.raises(EntityAlreadyExistsError, match="Entity already exists: Queue"):
       client.add_queue(queue)
   
   # After - just verify the exception type
   with pytest.raises(EntityAlreadyExistsError):
       client.add_queue(queue)
   ```

2. **Use Partial Matching**: For tests that must check messages, use partial matching:
   ```python
   # Before
   with pytest.raises(EntityNotFoundError, match="Queue TestQueue not found"):
       client.get_queue("TestQueue")
   
   # After - match key parts
   with pytest.raises(EntityNotFoundError, match="Queue.*not found"):
       client.get_queue("TestQueue")
   ```

3. **Update Expected Messages**: For tests that require exact matching, update to new patterns:
   ```python
   # Before
   assert str(exc_info.value) == "Entity already exists: ReceiptField"
   
   # After
   assert str(exc_info.value) == "already exists"
   ```

### For Production Code

1. **Avoid Parsing Error Messages**: Code should not parse error messages for logic:
   ```python
   # Bad - parsing error message
   try:
       client.add_entity(entity)
   except EntityAlreadyExistsError as e:
       if "Queue" in str(e):
           # Queue-specific handling
   
   # Good - use exception type or additional context
   try:
       client.add_entity(entity)
   except EntityAlreadyExistsError:
       # Handle based on context, not message
   ```

2. **Use Exception Types**: Rely on exception types rather than messages:
   ```python
   # Rely on exception type hierarchy
   try:
       result = client.get_entity(entity_id)
   except EntityNotFoundError:
       # Handle not found case
   except DynamoDBValidationError:
       # Handle validation error
   ```

## Automated Migration Tool

A utility script is available to help update test files automatically. See `scripts/migrate_error_messages.py` for details.

## Backward Compatibility

The refactoring maintains backward compatibility for most common error patterns. Tests that check for specific error messages should continue to work, but updating them to be less message-dependent will make them more maintainable.

## Future Considerations

Going forward, consider:
1. Making tests less dependent on exact error message text
2. Using structured error information (error codes, error types) instead of parsing messages
3. Documenting any error message patterns that external code depends on