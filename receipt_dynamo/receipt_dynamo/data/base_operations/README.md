# Base Operations Module

A modular, reusable framework for building DynamoDB data access classes with consistent error handling, validation, and operation patterns.

## Overview

This module provides a collection of composable components that can be mixed and matched to create robust DynamoDB data access layers. The architecture follows Python best practices with proper type safety, comprehensive error handling, and clear separation of concerns.

## Architecture

### Core Components

```
base_operations/
├── base.py              # Base class with core functionality
├── mixins.py           # Reusable operation mixins
├── error_handlers.py   # Centralized error handling
├── error_config.py     # Error message configuration
├── error_context.py    # Context extraction utilities
└── validators.py       # Entity validation logic
```

### Design Principles

1. **Composition over Inheritance** - Use mixins to add specific functionality
2. **Type Safety** - Full mypy compatibility with proper type hints
3. **Duck Typing** - Pythonic protocols without forced inheritance
4. **Centralized Configuration** - Single source of truth for error messages
5. **Modular Design** - Pick and choose components as needed

## Quick Start

### Basic Usage

```python
from receipt_dynamo.data.base_operations import (
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    BatchOperationsMixin
)

class MyEntityClient(DynamoDBBaseOperations, SingleEntityCRUDMixin, BatchOperationsMixin):
    def __init__(self, table_name: str, client):
        self.table_name = table_name
        self._client = client
    
    def add_entity(self, entity: MyEntity) -> None:
        """Add a single entity with validation and error handling."""
        self._add_entity(entity, MyEntity, "entity")
    
    def add_entities(self, entities: List[MyEntity]) -> None:
        """Add multiple entities using batch operations."""
        self._add_entities_batch(entities, MyEntity, "entities")
```

### Required Interface

All classes using these mixins must implement the `DynamoOperationsProtocol`:

```python
# Required attributes (automatically satisfied by DynamoDBBaseOperations)
table_name: str           # DynamoDB table name
_client: DynamoDBClient   # boto3 DynamoDB client
```

## Available Mixins

### SingleEntityCRUDMixin

Provides individual entity operations:

- `_add_entity()` - Add single entity with validation
- `_update_entity()` - Update single entity with validation  
- `_delete_entity()` - Delete single entity with validation

### BatchOperationsMixin

Provides batch operations with automatic retry:

- `_add_entities_batch()` - Batch add with chunking
- `_batch_write_with_retry()` - Retry logic for unprocessed items
- `_prepare_batch_request()` - Format requests for DynamoDB
- `_split_into_batches()` - Handle DynamoDB's 25-item limit

### TransactionalOperationsMixin

Provides transactional operations:

- `_transact_write_items()` - Execute transaction
- `_prepare_transact_update_item()` - Format update transactions
- `_prepare_transact_put_item()` - Format put transactions
- `_transact_write_with_chunking()` - Handle large transactions

## Error Handling

### Centralized Error Management

The module provides comprehensive error handling with:

- **Consistent Error Messages** - Standardized across all entity types
- **Automatic Error Classification** - ClientErrors mapped to appropriate exceptions
- **Context-Aware Messages** - Error details include operation context
- **Configurable Patterns** - Easy to customize for new entity types

### Error Types

```python
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,      # Entity doesn't exist
    EntityAlreadyExistsError, # Duplicate entity
    DynamoDBValidationError,  # Invalid parameters
    DynamoDBAccessError,      # Permission denied
    DynamoDBThroughputError,  # Rate limiting
    DynamoDBServerError,      # AWS service errors
)
```

### Custom Error Configuration

```python
from receipt_dynamo.data.base_operations import ErrorMessageConfig

# Customize error messages for your entity
config = ErrorMessageConfig()
config.ENTITY_NOT_FOUND_PATTERNS["my_entity"] = "MyEntity {entity_id} not found"
config.OPERATION_MESSAGES["process"] = "Could not process {entity_type}"
```

## Validation

### Entity Validation

Built-in validation with consistent error messages:

```python
# Single entity validation
self._validate_entity(entity, MyEntity, "entity")

# List validation  
self._validate_entity_list(entities, MyEntity, "entities")
```

### Custom Validation Messages

```python
# Configure parameter-specific messages
config = ErrorMessageConfig()
config.REQUIRED_PARAM_MESSAGES["my_param"] = "my_param cannot be None"
config.TYPE_MISMATCH_MESSAGES["MyEntity"] = "Must be MyEntity instance"
```

## Type Safety

### Protocol-Based Design

The module uses Python protocols for type safety without forcing inheritance:

```python
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mypy_boto3_dynamodb import DynamoDBClient

class MyMixin:
    # Declare expected attributes for type checker
    if TYPE_CHECKING:
        table_name: str
        _client: "DynamoDBClient"
```

### MyPy Compatibility

- **Strict mode compatible** - No type: ignore needed
- **Full type coverage** - All public APIs properly typed
- **Protocol compliance** - Clear contracts for mixin users

## Best Practices

### 1. Composition Strategy

```python
# ✅ Good: Compose mixins based on needs
class ReadOnlyClient(DynamoDBBaseOperations):
    # Only read operations, no mixins needed
    pass

class CRUDClient(DynamoDBBaseOperations, SingleEntityCRUDMixin):
    # Add CRUD operations
    pass

class BatchClient(DynamoDBBaseOperations, BatchOperationsMixin):
    # Add batch operations
    pass

class FullClient(DynamoDBBaseOperations, SingleEntityCRUDMixin, 
                 BatchOperationsMixin, TransactionalOperationsMixin):
    # All functionality
    pass
```

### 2. Error Handling

```python
# ✅ Good: Let base operations handle errors
@handle_dynamodb_errors("add_my_entity")
def add_my_entity(self, entity: MyEntity) -> None:
    self._add_entity(entity, MyEntity, "entity")

# ❌ Avoid: Manual error handling
def add_my_entity(self, entity: MyEntity) -> None:
    try:
        # Manual DynamoDB operations
    except ClientError as e:
        # Manual error handling
```

### 3. Validation Patterns

```python
# ✅ Good: Use built-in validation
def add_entities(self, entities: List[MyEntity]) -> None:
    self._add_entities_batch(entities, MyEntity, "entities")

# ❌ Avoid: Manual validation
def add_entities(self, entities: List[MyEntity]) -> None:
    if not entities:
        raise ValueError("entities cannot be empty")
    if not isinstance(entities, list):
        raise ValueError("entities must be a list")
    # etc...
```

## Testing

### Mocking Strategy

```python
import pytest
from unittest.mock import MagicMock
from mypy_boto3_dynamodb import DynamoDBClient

@pytest.fixture
def mock_client():
    return MagicMock(spec=DynamoDBClient)

@pytest.fixture  
def my_client(mock_client):
    return MyEntityClient("test-table", mock_client)

def test_add_entity(my_client, mock_client):
    entity = MyEntity(id="test")
    my_client.add_entity(entity)
    mock_client.put_item.assert_called_once()
```

### Error Testing

```python
def test_entity_not_found(my_client, mock_client):
    # Configure mock to raise ClientError
    error_response = {"Error": {"Code": "ConditionalCheckFailedException"}}
    mock_client.put_item.side_effect = ClientError(error_response, "PutItem")
    
    with pytest.raises(EntityNotFoundError):
        my_client.update_entity(entity)
```

## Migration Guide

### From Legacy Patterns

```python
# Before: Manual error handling
class OldClient:
    def add_entity(self, entity):
        try:
            self._client.put_item(...)
        except ClientError as e:
            if e.response["Error"]["Code"] == "ConditionalCheckFailedException":
                raise ValueError("Entity already exists")
            # etc...

# After: Using base operations
class NewClient(DynamoDBBaseOperations, SingleEntityCRUDMixin):
    @handle_dynamodb_errors("add_entity")
    def add_entity(self, entity: MyEntity) -> None:
        self._add_entity(entity, MyEntity, "entity")
```

## Performance Considerations

### Batch Operations

- **Automatic Chunking** - Handles DynamoDB's 25-item limit
- **Retry Logic** - Exponential backoff for unprocessed items
- **Memory Efficient** - Processes items in batches

### Error Handling

- **Lazy Initialization** - Error handlers created on first use
- **Cached Components** - Reuse validators and configurations
- **Minimal Overhead** - TYPE_CHECKING blocks have no runtime cost

## Contributing

### Adding New Mixins

1. **Follow the Protocol** - Declare expected attributes in TYPE_CHECKING
2. **Document Requirements** - Clear docstrings about protocol needs
3. **Add Error Handling** - Use @handle_dynamodb_errors decorator
4. **Include Validation** - Use built-in validation patterns
5. **Test Thoroughly** - Unit tests with proper mocking

### Extending Error Handling

1. **Update ErrorMessageConfig** - Add new patterns and messages
2. **Extend Context Extraction** - Support new entity types
3. **Add Test Coverage** - Verify error scenarios work correctly

## Examples

See the existing data access classes for real-world usage examples:

- `receipt_dynamo/data/_receipt.py` - Basic CRUD operations
- `receipt_dynamo/data/_image.py` - Batch operations  
- `receipt_dynamo/data/_receipt_field.py` - Transactional operations

## License

This module is part of the receipt_dynamo package and follows the same licensing terms.