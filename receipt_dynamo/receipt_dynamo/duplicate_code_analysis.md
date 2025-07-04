# Duplicate Code Pattern Analysis for receipt_dynamo

## Executive Summary

After analyzing the data layer files in the receipt_dynamo package, I've identified significant code duplication across the CRUD operations. The duplication is primarily in exception handling, batch operations, validation logic, and method structures. This analysis provides a foundation for developing a refactoring strategy to reduce code duplication and improve maintainability.

## Key Duplicate Patterns Identified

### 1. Exception Handling Patterns

Every CRUD operation follows the same exception handling pattern:

```python
try:
    # DynamoDB operation
except ClientError as e:
    error_code = e.response.get("Error", {}).get("Code", "")
    if error_code == "ConditionalCheckFailedException":
        # Entity-specific message
    elif error_code == "ProvisionedThroughputExceededException":
        raise DynamoDBThroughputError(f"Provisioned throughput exceeded: {e}") from e
    elif error_code == "InternalServerError":
        raise DynamoDBServerError(f"Internal server error: {e}") from e
    elif error_code == "ValidationException":
        raise DynamoDBValidationError(f"One or more parameters given were invalid: {e}") from e
    elif error_code == "AccessDeniedException":
        raise DynamoDBAccessError(f"Access denied: {e}") from e
    # ... more conditions
```

This pattern is repeated in:
- `add_*` methods
- `update_*` methods
- `delete_*` methods
- `get_*` methods
- `list_*` methods

### 2. Batch Operation Patterns

All batch operations follow the same chunking and retry pattern:

```python
CHUNK_SIZE = 25  # Defined in each file

for i in range(0, len(items), CHUNK_SIZE):
    chunk = items[i : i + CHUNK_SIZE]
    request_items = [
        WriteRequestTypeDef(
            PutRequest=PutRequestTypeDef(Item=item.to_item())
        )
        for item in chunk
    ]
    response = self._client.batch_write_item(
        RequestItems={self.table_name: request_items}
    )
    # Handle unprocessed items
    unprocessed = response.get("UnprocessedItems", {})
    while unprocessed.get(self.table_name):
        response = self._client.batch_write_item(
            RequestItems=unprocessed
        )
        unprocessed = response.get("UnprocessedItems", {})
```

This pattern appears in:
- `add_receipts`, `add_images`, `add_words`, `add_lines`
- `delete_receipts`, `delete_images`, `delete_words`, `delete_lines`

### 3. Transaction Operation Patterns

Transaction operations for updates follow a consistent pattern:

```python
for i in range(0, len(items), CHUNK_SIZE):
    chunk = items[i : i + CHUNK_SIZE]
    transact_items = []
    for item in chunk:
        transact_items.append(
            TransactWriteItemTypeDef(
                Put=PutTypeDef(
                    TableName=self.table_name,
                    Item=item.to_item(),
                    ConditionExpression="attribute_exists(PK)",
                )
            )
        )
    try:
        self._client.transact_write_items(TransactItems=transact_items)
    except ClientError as e:
        # Standard exception handling
```

This pattern appears in:
- `update_receipts`, `update_images`, `update_words`, `update_receipt_metadatas`
- `delete_receipts` (using Delete instead of Put)

### 4. Validation Logic Patterns

Parameter validation is repeated across methods:

```python
if param is None:
    raise ValueError("Param is required and cannot be None.")
if not isinstance(param, ExpectedType):
    raise ValueError("param must be an instance of the ExpectedType class.")

# For lists:
if not isinstance(params, list):
    raise ValueError("params must be a list of ExpectedType instances.")
if not all(isinstance(item, ExpectedType) for item in params):
    raise ValueError("All items must be instances of the ExpectedType class.")
```

### 5. Query and List Patterns

List operations follow a similar pagination pattern:

```python
query_params: QueryInputTypeDef = {
    "TableName": self.table_name,
    "IndexName": "GSITYPE",
    "KeyConditionExpression": "#t = :val",
    "ExpressionAttributeNames": {"#t": "TYPE"},
    "ExpressionAttributeValues": {":val": {"S": "ENTITY_TYPE"}},
}

if lastEvaluatedKey is not None:
    query_params["ExclusiveStartKey"] = lastEvaluatedKey
if limit is not None:
    query_params["Limit"] = limit

response = self._client.query(**query_params)
items.extend([item_to_entity(item) for item in response["Items"]])

# Pagination handling
if limit is None:
    while "LastEvaluatedKey" in response:
        query_params["ExclusiveStartKey"] = response["LastEvaluatedKey"]
        response = self._client.query(**query_params)
        items.extend([item_to_entity(item) for item in response["Items"]])
```

## Refactoring Opportunities

### 1. Base Class for Common Operations

Create a `DynamoDBBaseOperations` class that includes:

```python
class DynamoDBBaseOperations(DynamoClientProtocol):
    CHUNK_SIZE = 25

    def _handle_client_error(self, error: ClientError, operation: str, entity_type: str):
        """Centralized error handling for DynamoDB operations"""

    def _batch_write_with_retry(self, request_items: list, operation_type: str):
        """Generic batch write with retry logic"""

    def _validate_entity(self, entity, expected_type: type):
        """Common validation logic"""

    def _validate_entity_list(self, entities: list, expected_type: type):
        """Common list validation logic"""

    def _paginated_query(self, query_params: dict, item_converter):
        """Generic paginated query logic"""
```

### 2. Mixins for Specific Operations

Create mixins for common patterns:

```python
class CRUDMixin:
    """Mixin providing generic CRUD operations"""

    def _generic_add(self, entity, entity_type: str):
        """Generic add operation with standard error handling"""

    def _generic_batch_add(self, entities: list, entity_type: str):
        """Generic batch add with chunking and retry"""

    def _generic_update(self, entity, entity_type: str):
        """Generic update operation"""

    def _generic_delete(self, key: dict, entity_type: str):
        """Generic delete operation"""

class TransactionMixin:
    """Mixin for transaction operations"""

    def _transact_write_chunked(self, items: list, operation_builder):
        """Generic transactional write with chunking"""
```

### 3. Error Handler Decorator

Create a decorator for consistent error handling:

```python
def handle_dynamodb_errors(entity_type: str):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            try:
                return func(*args, **kwargs)
            except ClientError as e:
                # Centralized error handling logic
        return wrapper
    return decorator
```

### 4. Entity-Specific Classes

Refactor entity classes to inherit from base classes:

```python
class _Receipt(DynamoDBBaseOperations, CRUDMixin):
    entity_type = "Receipt"

    def add_receipt(self, receipt: Receipt):
        self._validate_entity(receipt, Receipt)
        return self._generic_add(receipt, self.entity_type)

    def add_receipts(self, receipts: list[Receipt]):
        self._validate_entity_list(receipts, Receipt)
        return self._generic_batch_add(receipts, self.entity_type)
```

## Benefits of Refactoring

1. **Code Reduction**: Approximately 60-70% reduction in duplicated code
2. **Maintainability**: Changes to error handling or batch logic only need to be made in one place
3. **Consistency**: Ensures all operations follow the same patterns
4. **Testing**: Easier to test common functionality once
5. **Extensibility**: Adding new entity types becomes trivial

## Implementation Priority

1. **High Priority**: Exception handling centralization (affects all operations)
2. **High Priority**: Batch operation abstraction (high duplication, clear pattern)
3. **Medium Priority**: Transaction operation abstraction
4. **Medium Priority**: Validation logic centralization
5. **Low Priority**: Query/pagination patterns (more variation between entities)

## Next Steps

1. Create the base classes and mixins in a new module (e.g., `receipt_dynamo/data/_base_operations.py`)
2. Implement error handling decorator
3. Refactor one entity class as a proof of concept
4. Write comprehensive tests for the base functionality
5. Gradually migrate other entity classes
6. Update documentation to reflect new architecture
