# receipt_dynamo integration test patterns

Worked examples for the rules in `SKILL.md`. Replace `Entity` with the real entity.

## 1. Basic CRUD Operations

### Add Entity Success

```python
def test_add_entity_success(
    self,
    dynamodb_table: Literal["MyMockedTable"],
    example_entity: Entity
) -> None:
    """Test successful addition of an entity."""
    client = DynamoClient(dynamodb_table)
    client.add_entity(example_entity)
    result = client.get_entity(example_entity.id)
    assert result == example_entity
```

### Add Duplicate Entity

```python
def test_add_duplicate_entity_raises_error(
    self,
    dynamodb_table: Literal["MyMockedTable"],
    example_entity: Entity
) -> None:
    """Test that adding a duplicate entity raises error."""
    client = DynamoClient(dynamodb_table)
    client.add_entity(example_entity)
    # Create duplicate with same ID
    duplicate = Entity(
        id=example_entity.id,
        # ... other fields
    )
    with pytest.raises(EntityAlreadyExistsError, match="already exists"):
        client.add_entity(duplicate)
```

### Get Entity Not Found

```python
def test_get_entity_not_found_returns_none(
    self, dynamodb_table: Literal["MyMockedTable"]
) -> None:
    """Test that getting a non-existent entity returns None."""
    client = DynamoClient(dynamodb_table)
    result = client.get_entity("NON_EXISTENT_ID")
    assert result is None
```

### Update Entity Not Found

```python
def test_update_entity_not_found_raises_error(
    self,
    dynamodb_table: Literal["MyMockedTable"],
    example_entity: Entity
) -> None:
    """Test that updating a non-existent entity raises error."""
    client = DynamoClient(dynamodb_table)
    with pytest.raises(EntityNotFoundError, match="not found"):
        client.update_entity(example_entity)
```

## 2. Batch Operations

### Successful Batch Add

```python
def test_add_entities_success(
    self,
    dynamodb_table: Literal["MyMockedTable"],
    example_entities: List[Entity]
) -> None:
    """Test successful batch addition of entities."""
    client = DynamoClient(dynamodb_table)
    client.add_entities(example_entities)

    for entity in example_entities:
        result = client.get_entity(entity.id)
        assert result == entity
```

### Large Batch Operations

```python
def test_add_large_batch_entities_success(
    self, dynamodb_table: Literal["MyMockedTable"]
) -> None:
    """Test successful batch addition of 100 entities."""
    client = DynamoClient(dynamodb_table)
    large_batch = [
        Entity(
            id=f"ENTITY_{i:03d}",
            # ... other fields
        )
        for i in range(100)
    ]

    client.add_entities(large_batch)

    # Verify a sample of the added entities
    for i in [0, 25, 50, 75, 99]:
        result = client.get_entity(f"ENTITY_{i:03d}")
        assert result == large_batch[i]
```

## 3. Validation Tests

### None Parameter Validation

```python
def test_add_entity_none_raises_error(
    self, dynamodb_table: Literal["MyMockedTable"]
) -> None:
    """Test that adding None raises EntityValidationError."""
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        EntityValidationError, match="item cannot be None"
    ):
        client.add_entity(None)  # type: ignore
```

### Wrong Type Validation

```python
def test_add_entity_wrong_type_raises_error(
    self, dynamodb_table: Literal["MyMockedTable"]
) -> None:
    """Test that adding wrong type raises EntityValidationError."""
    client = DynamoClient(dynamodb_table)
    with pytest.raises(
        EntityValidationError,
        match="item must be an instance of the Entity class",
    ):
        client.add_entity("not-an-entity")  # type: ignore
```

## 4. Error Handling Tests

Use parametrized tests for comprehensive error coverage:

```python
@pytest.mark.parametrize(
    "error_code,expected_exception",
    [
        ("ConditionalCheckFailedException", EntityAlreadyExistsError),
        ("ValidationException", EntityValidationError),
        ("ResourceNotFoundException", OperationError),
        ("ItemCollectionSizeLimitExceededException", DynamoDBError),
        ("TransactionConflictException", DynamoDBError),
        ("RequestLimitExceeded", DynamoDBError),
        ("ProvisionedThroughputExceededException", DynamoDBThroughputError),
        ("InternalServerError", DynamoDBServerError),
        ("ServiceUnavailable", DynamoDBServerError),
        ("UnknownError", DynamoDBError),
    ],
)
class TestEntityErrorHandling:
    """Test error handling for Entity operations."""

    def test_add_entity_dynamodb_error(
        self,
        dynamodb_table: Literal["MyMockedTable"],
        example_entity: Entity,
        error_code: str,
        expected_exception: type,
    ) -> None:
        """Test that DynamoDB errors are properly handled in add operations."""
        client = DynamoClient(dynamodb_table)
        with patch.object(
            client._client, "put_item", side_effect=ClientError(
                {"Error": {"Code": error_code, "Message": "Test error"}},
                "PutItem"
            )
        ):
            with pytest.raises(expected_exception):
                client.add_entity(example_entity)
```

## 5. List/Query Operations

### Basic List with Pagination

```python
def test_list_entities_pagination(
    self,
    dynamodb_table: Literal["MyMockedTable"],
    example_entities: List[Entity]
) -> None:
    """Test pagination through entities."""
    client = DynamoClient(dynamodb_table)
    client.add_entities(example_entities)

    # Get first page
    first_results, first_key = client.list_entities(limit=2)
    assert len(first_results) == 2
    assert first_key is not None

    # Get second page
    second_results, second_key = client.list_entities(
        limit=2, last_evaluated_key=first_key
    )
    assert len(second_results) == 1
    assert second_key is None

    # Verify no overlap
    first_ids = {entity.id for entity in first_results}
    second_ids = {entity.id for entity in second_results}
    assert first_ids.isdisjoint(second_ids)
```

## 6. Fixtures

### Basic Entity Fixture

```python
@pytest.fixture
def example_entity() -> Entity:
    """Create a sample Entity for testing."""
    return Entity(
        id="TEST_ID",
        # Include all required fields
        # Use datetime.now().isoformat() for timestamps
        timestamp=datetime.now().isoformat(),
    )
```

### Entity with TTL

```python
@pytest.fixture
def example_entity_with_ttl() -> Entity:
    """Create a sample Entity with TTL for testing."""
    future_ttl = int(time.time()) + 3600  # 1 hour from now
    return Entity(
        id="TTL_ID",
        # ... other fields
        time_to_live=future_ttl,
    )
```

### List of Entities

```python
@pytest.fixture
def example_entities() -> List[Entity]:
    """Create a list of Entities for batch testing."""
    now = datetime.now().isoformat()
    return [
        Entity(id="ENTITY_1", timestamp=now),
        Entity(id="ENTITY_2", timestamp=now),
        Entity(id="ENTITY_3", timestamp=now),
    ]
```

## 7. Shared error scenarios

```python
# Error scenarios for parameterized tests
ERROR_SCENARIOS = [
    ("ProvisionedThroughputExceededException", DynamoDBThroughputError, "Throughput exceeded"),
    ("InternalServerError", DynamoDBServerError, "DynamoDB server error"),
    ("ValidationException", EntityValidationError, "Validation error"),
    ("AccessDeniedException", DynamoDBError, "DynamoDB error during"),
    ("ResourceNotFoundException", OperationError, "DynamoDB resource not found"),
]

# Parameterized test example
@pytest.mark.parametrize("error_code,expected_exception,error_match", ERROR_SCENARIOS)
def test_operation_client_errors(
    dynamodb_table: Literal["MyMockedTable"],
    sample_entity: Entity,
    mocker: MockerFixture,
    error_code: str,
    expected_exception: Type[Exception],
    error_match: str,
) -> None:
    # Test implementation
```
