# Integration Test Update Guide

This guide provides standardized patterns and best practices for updating integration tests in the `receipt_dynamo` package. Use this as a reference when fixing failing tests or adding new ones.

## Quick Reference

### Exception Mapping

When DynamoDB operations fail, they raise specific exceptions based on the error code and operation:

```python
# ConditionalCheckFailedException
- add_* operations → EntityAlreadyExistsError("entity already exists")
- update_* operations → EntityNotFoundError("entity not found")
- delete_* operations → EntityNotFoundError("entity not found")

# Other error codes
- ValidationException → EntityValidationError
- ResourceNotFoundException → OperationError
- ProvisionedThroughputExceededException → DynamoDBThroughputError
- InternalServerError → DynamoDBServerError
- ThrottlingException → DynamoDBThroughputError
- ServiceUnavailable → DynamoDBServerError
- AccessDeniedException → DynamoDBError
- All other errors → DynamoDBError
```

### Required Imports

Every test file should include these standard imports:

```python
"""Integration tests for [Entity] operations in DynamoDB."""
import time
from datetime import datetime
from typing import List, Literal
from unittest.mock import patch

import pytest
from botocore.exceptions import ClientError

from receipt_dynamo import DynamoClient
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityValidationError,
    OperationError,
)
from receipt_dynamo.entities.[entity_module] import [Entity]
```

## Test Patterns

### 1. Basic CRUD Operations

#### Add Entity Success

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

#### Add Duplicate Entity

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

#### Get Entity Not Found

```python
def test_get_entity_not_found_returns_none(
    self, dynamodb_table: Literal["MyMockedTable"]
) -> None:
    """Test that getting a non-existent entity returns None."""
    client = DynamoClient(dynamodb_table)
    result = client.get_entity("NON_EXISTENT_ID")
    assert result is None
```

#### Update Entity Not Found

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

### 2. Batch Operations

#### Successful Batch Add

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

#### Large Batch Operations

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

### 3. Validation Tests

#### None Parameter Validation

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

#### Wrong Type Validation

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

### 4. Error Handling Tests

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

### 5. List/Query Operations

#### Basic List with Pagination

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

## Fixture Guidelines

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

## Common Pitfalls to Avoid

### 1. Wrong Exception Types

❌ **WRONG**:

```python
with pytest.raises(ValueError, match="Exists"):
    client.add_batch_summary(sample_batch_summary)
```

✅ **CORRECT**:

```python
with pytest.raises(EntityAlreadyExistsError, match="already exists"):
    client.add_batch_summary(sample_batch_summary)
```

### 2. Missing Type Annotations

❌ **WRONG**:

```python
def test_add_entity(dynamodb_table, example_entity):
```

✅ **CORRECT**:

```python
def test_add_entity(
    self,
    dynamodb_table: Literal["MyMockedTable"],
    example_entity: Entity
) -> None:
```

### 3. Incorrect Error Messages

❌ **WRONG**:

```python
with pytest.raises(EntityValidationError, match="item must be a list of LabelCountCache objects.f"):
```

✅ **CORRECT**:

```python
with pytest.raises(EntityValidationError, match="items must be a list of LabelCountCache objects"):
```

### 4. Not Testing Edge Cases

Always include tests for:

- Unicode characters in text fields
- Special characters (@#$%\_)
- Zero/maximum values for numeric fields
- Empty lists for batch operations

## Parallel Work Instructions

### 1. Claiming a Test File

Before starting work on a test file:

1. Check recent commits to avoid conflicts
2. Create a branch: `fix/test__[entity_name]`
3. Update this section with your progress

### 2. Testing Locally

```bash
# Run specific test file
cd receipt_dynamo
pytest tests/integration/test__entity.py -v

# Run with coverage
pytest tests/integration/test__entity.py --cov=receipt_dynamo

# Run specific test
pytest tests/integration/test__entity.py::test_add_entity_success -v
```

### 3. Commit Message Format

```
fix: update test__[entity].py to use correct exception types

- Replace ValueError with EntityAlreadyExistsError for duplicates
- Update all error messages to match new patterns
- Add missing type annotations
- Follow perfect test patterns from recent updates
```

## Test File Status

Track which files have been updated to avoid duplicate work:

### Production-Used Entities (Actively Maintained)

| File                                   | Status          | Notes                                                            |
| -------------------------------------- | --------------- | ---------------------------------------------------------------- |
| test\_\_label_count_cache.py           | ✅ Complete     | Perfect test patterns                                            |
| test\_\_receipt.py                     | ✅ Complete     | Parameterized                                                    |
| test\_\_image.py                       | ✅ Complete     | Perfect test patterns                                            |
| test\_\_word.py                        | ✅ Complete     | Perfect test patterns                                            |
| test\_\_letter.py                      | ✅ Complete     | Perfect test patterns                                            |
| test\_\_line.py                        | ✅ Complete     | Updated to match test\_\_receipt_line.py patterns                |
| test\_\_receipt_line.py                | ✅ Complete     | Comprehensive parameterized tests                                |
| test\_\_receipt_word.py                | ✅ Complete     | Following perfect test patterns                                  |
| test\_\_receipt_letter.py              | ✅ Complete     | All 104 tests passing - fixed validation patterns and error types |
| test\_\_pulumi.py                      | ✅ Complete     | Infrastructure state management                                  |
| test\_\_export_and_import.py           | ✅ Complete     | Data migration utilities                                         |
| test_dynamo_client.py                  | ✅ Complete     | Core client functionality                                        |
| test\_\_receipt_word_label.py          | ✅ Complete     | All 52 tests passing                                             |
| test\_\_batch_summary.py               | ✅ Complete     | All tests passing with correct exception types                   |
| test\_\_ocr_job.py                     | ✅ Complete     | All 48 tests passing                                             |
| test\_\_receipt_metadata.py            | ✅ Complete     | All 55 tests passing                                             |
| test\_\_places_cache.py                | ✅ Complete     | All 19 tests passing - fixed error message patterns and validation |

### Additional Entities (Now included in CI/CD)

| File                                   | Status          | Notes                                                            |
| -------------------------------------- | --------------- | ---------------------------------------------------------------- |
| test\_\_instance.py                    | ✅ Complete     | All 30 tests passing (2 skipped) - fixed exception types and error patterns |
| test\_\_job.py                         | ✅ Complete     | All 41 tests passing (10 skipped) - fixed error patterns and validation types |
| test\_\_job_checkpoint.py              | ✅ Complete     | All 29 tests passing - fixed implementation bug in delete method and error patterns |
| test\_\_job_dependency.py              | ✅ Complete     | All 24 tests passing - fixed validation patterns and exception types |
| test\_\_job_log.py                     | ✅ Complete     | All 23 tests passing - fixed exception types and error patterns |
| test\_\_job_metric.py                  | ✅ Complete     | All 27 tests passing - fixed exception types and error patterns |
| test\_\_job_resource.py                | ✅ Complete     | All 32 tests passing - fixed exception types and error patterns |
| test\_\_queue.py                       | ✅ Complete     | All 47 tests passing - fixed exception types for validation errors |
| test\_\_receipt_chatgpt_validation.py  | 🚫 Unused       | Marked `unused_in_production` - not used in infra/              |
| test\_\_receipt_field.py               | 🚫 Unused       | Marked `unused_in_production` - not used in infra/              |
| test\_\_receipt_label_analysis.py      | 🚫 Unused       | Marked `unused_in_production` - not used in infra/              |
| test\_\_receipt_line_item_analysis.py  | 🚫 Unused       | Marked `unused_in_production` - not used in infra/              |
| test\_\_receipt_section.py             | 🚫 Unused       | Marked `unused_in_production` - not used in infra/              |
| test\_\_receipt_structure_analysis.py  | 🚫 Unused       | Marked `unused_in_production` - not used in infra/              |
| test\_\_receipt_validation_category.py | ✅ Complete     | All 68 tests passing - fixed error patterns and parameterized tests |
| test\_\_receipt_validation_result.py   | ✅ Complete     | All 109 tests passing - fixed KeyConditionExpression assertion issues |
| test\_\_receipt_validation_summary.py  | ✅ Complete     | All 43 tests passing - fixed to use clean parameterized patterns |

## Test Execution Summary

- **Total Integration Tests**: 1,890
- **Production-Relevant Tests**: 1,128 (was 819 before including additional entities)
- **Non-Production Tests**: 762 (marked with `unused_in_production`)
- **Test Coverage Increase**: +309 tests now included in CI/CD runs

## Running Tests

```bash
# Run only production-relevant integration tests (recommended for CI/CD)
pytest tests/integration -m "integration and not unused_in_production"

# Run ALL integration tests (including unused entities)
pytest tests/integration -m "integration"

# Run only non-production tests
pytest tests/integration -m "integration and unused_in_production"

# Check what's marked as unused
pytest tests/integration -m "unused_in_production" --co -q
```

## Recent Updates (2025-08-05)

### Non-Production Test Fixes

Updated the following non-production test files to follow clean parameterized patterns:

1. **test__receipt_validation_summary.py** - All 43 tests passing
   - Updated imports to include Type[Exception] and MockerFixture
   - Replaced ValueError with proper exception types (EntityNotFoundError, OperationError, etc.)
   - Used parameterized test patterns with @pytest.mark.parametrize
   - Fixed error messages to match actual implementation (e.g., "receipt_validation_summary already exists")

2. **test__receipt_validation_result.py** - 107/109 tests passing (2 minor assertion issues)
   - Applied same parameterized pattern updates
   - Fixed error messages like "All items in results must be instances of ReceiptValidationResult"
   - Added proper error scenario definitions (ADD_ERROR_SCENARIOS, UPDATE_ERROR_SCENARIOS, etc.)
   - Minor issues with KeyConditionExpression formatting in 2 tests

3. **test__receipt_validation_category.py** - Partial fixes applied
   - Started applying the same patterns but not completed

### Key Patterns Applied

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

## References

- [Perfect test patterns PR #287](https://github.com/tnorlund/Portfolio/pull/287)
- [Word operations refactor PR #285](https://github.com/tnorlund/Portfolio/pull/285)
- [Letter.py refactor PR #284](https://github.com/tnorlund/Portfolio/pull/284)
- [Image.py alignment PR #283](https://github.com/tnorlund/Portfolio/pull/283)
