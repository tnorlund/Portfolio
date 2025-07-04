"""
Unit tests for base operations framework.

These tests validate the core functionality that will be shared across
all entity data access classes.
"""

from unittest.mock import MagicMock, Mock

import pytest
from botocore.exceptions import ClientError

from receipt_dynamo.data.base_operations import (
    BatchOperationsMixin,
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    TransactionalOperationsMixin,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBAccessError,
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    DynamoDBValidationError,
)


class MockEntity:
    """Mock entity for testing"""

    def __init__(self, entity_id="test_id"):
        self.entity_id = entity_id
        self.receipt_id = 12345
        self.image_id = "test-image-123"

    def to_item(self):
        return {"PK": {"S": f"TEST#{self.entity_id}"}, "SK": {"S": "ENTITY"}}

    def key(self):
        return {"PK": {"S": f"TEST#{self.entity_id}"}, "SK": {"S": "ENTITY"}}


class TestEntityOperations(
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    BatchOperationsMixin,
    TransactionalOperationsMixin,
):
    """Test implementation of base operations"""

    def __init__(self):
        super().__init__()
        self._client = Mock()
        self.table_name = "test-table"


class TestDynamoDBBaseOperations:
    """Test the base operations class"""

    def setup_method(self):
        self.ops = TestEntityOperations()
        self.entity = MockEntity()

    def test_validate_entity_success(self):
        """Test successful entity validation"""
        # Should not raise any exception
        self.ops._validate_entity(self.entity, MockEntity, "test_entity")

    def test_validate_entity_none(self):
        """Test validation fails for None entity"""
        with pytest.raises(
            ValueError, match="test_entity parameter is required"
        ):
            self.ops._validate_entity(None, MockEntity, "test_entity")

    def test_validate_entity_wrong_type(self):
        """Test validation fails for wrong entity type"""
        with pytest.raises(
            ValueError, match="must be an instance of the MockEntity class"
        ):
            self.ops._validate_entity(
                "not_an_entity", MockEntity, "test_entity"
            )

    def test_validate_entity_list_success(self):
        """Test successful entity list validation"""
        entities = [MockEntity("1"), MockEntity("2")]
        self.ops._validate_entity_list(entities, MockEntity, "test_entities")

    def test_validate_entity_list_none(self):
        """Test validation fails for None entity list"""
        with pytest.raises(
            ValueError, match="test_entities parameter is required"
        ):
            self.ops._validate_entity_list(None, MockEntity, "test_entities")

    def test_validate_entity_list_not_list(self):
        """Test validation fails for non-list"""
        with pytest.raises(
            ValueError, match="must be a list of MockEntity instances"
        ):
            self.ops._validate_entity_list(
                "not_a_list", MockEntity, "test_entities"
            )

    def test_validate_entity_list_wrong_types(self):
        """Test validation fails for list with wrong types"""
        entities = [MockEntity(), "not_an_entity"]
        with pytest.raises(
            ValueError, match="All test_entities must be instances"
        ):
            self.ops._validate_entity_list(
                entities, MockEntity, "test_entities"
            )

    def test_validate_entity_type_bypass_prevention(self):
        """Test that using type(entity) does NOT bypass validation (bug prevention)"""
        # This test ensures we don't accidentally use type(entity) which bypasses validation
        wrong_entity = "not_an_entity"

        # Using type(wrong_entity) would make isinstance(wrong_entity, str) always True
        # This should NOT be done - we should use specific entity classes
        with pytest.raises(
            ValueError, match="must be an instance of the MockEntity class"
        ):
            self.ops._validate_entity(wrong_entity, MockEntity, "test_entity")

        # Demonstrate the bug: type(entity) would make validation useless
        # isinstance("not_an_entity", type("not_an_entity")) would be True
        # But isinstance("not_an_entity", MockEntity) is correctly False

    def test_extract_entity_context_with_entity(self):
        """Test extracting entity context from args"""
        context = {"args": [self.entity]}
        result = self.ops._extract_entity_context(context)
        # The method prioritizes receipt_id over entity_id in the order list
        assert "MockEntity with receipt_id=12345" == result

    def test_extract_entity_context_no_context(self):
        """Test extracting entity context with no context"""
        result = self.ops._extract_entity_context({})
        assert result == "unknown entity"

    def test_extract_entity_context_no_args(self):
        """Test extracting entity context with no args"""
        context = {"args": []}
        result = self.ops._extract_entity_context(context)
        assert result == "unknown entity"


class TestErrorHandling:
    """Test centralized error handling"""

    def setup_method(self):
        self.ops = TestEntityOperations()
        self.entity = MockEntity()

    def test_handle_conditional_check_failed_add_operation(self):
        """Test conditional check failure for add operation"""
        error = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem"
        )
        context = {"args": [self.entity]}

        with pytest.raises(ValueError, match="Entity already exists"):
            self.ops._handle_conditional_check_failed(
                error, "add_entity", context
            )

    def test_handle_conditional_check_failed_update_operation(self):
        """Test conditional check failure for update operation"""
        error = ClientError(
            {"Error": {"Code": "ConditionalCheckFailedException"}}, "PutItem"
        )
        context = {"args": [self.entity]}

        with pytest.raises(ValueError, match="Entity does not exist"):
            self.ops._handle_conditional_check_failed(
                error, "update_entity", context
            )

    def test_handle_resource_not_found(self):
        """Test resource not found error handling"""
        error = ClientError(
            {"Error": {"Code": "ResourceNotFoundException"}}, "PutItem"
        )

        with pytest.raises(DynamoDBError, match="Table not found"):
            self.ops._handle_resource_not_found(error, "test_operation", {})

    def test_handle_throughput_exceeded(self):
        """Test throughput exceeded error handling"""
        error = ClientError(
            {"Error": {"Code": "ProvisionedThroughputExceededException"}},
            "PutItem",
        )

        with pytest.raises(
            DynamoDBThroughputError, match="Provisioned throughput exceeded"
        ):
            self.ops._handle_throughput_exceeded(error, "test_operation", {})

    def test_handle_internal_server_error(self):
        """Test internal server error handling"""
        error = ClientError(
            {"Error": {"Code": "InternalServerError"}}, "PutItem"
        )

        with pytest.raises(DynamoDBServerError, match="Internal server error"):
            self.ops._handle_internal_server_error(error, "test_operation", {})

    def test_handle_validation_exception(self):
        """Test validation exception handling"""
        error = ClientError(
            {"Error": {"Code": "ValidationException"}}, "PutItem"
        )

        with pytest.raises(DynamoDBValidationError, match="Validation error"):
            self.ops._handle_validation_exception(error, "test_operation", {})

    def test_handle_access_denied(self):
        """Test access denied error handling"""
        error = ClientError(
            {"Error": {"Code": "AccessDeniedException"}}, "PutItem"
        )

        with pytest.raises(DynamoDBAccessError, match="Access denied"):
            self.ops._handle_access_denied(error, "test_operation", {})

    def test_handle_transaction_cancelled_conditional_check(self):
        """Test transaction cancelled with conditional check failure"""
        error = ClientError(
            {
                "Error": {
                    "Code": "TransactionCanceledException",
                    "Message": "ConditionalCheckFailed",
                }
            },
            "TransactWriteItems",
        )

        with pytest.raises(
            ValueError, match="One or more entities do not exist"
        ):
            self.ops._handle_transaction_cancelled(error, "test_operation", {})

    def test_handle_transaction_cancelled_other_reason(self):
        """Test transaction cancelled for other reasons"""
        error = ClientError(
            {
                "Error": {
                    "Code": "TransactionCanceledException",
                    "Message": "Other reason",
                }
            },
            "TransactWriteItems",
        )

        with pytest.raises(DynamoDBError, match="Transaction canceled"):
            self.ops._handle_transaction_cancelled(error, "test_operation", {})

    def test_handle_unknown_error(self):
        """Test unknown error handling"""
        error = ClientError({"Error": {"Code": "UnknownErrorCode"}}, "PutItem")

        with pytest.raises(DynamoDBError, match="Unknown error"):
            self.ops._handle_unknown_error(error, "test_operation", {})


class TestSingleEntityCRUDMixin:
    """Test the CRUD mixin functionality"""

    def setup_method(self):
        self.ops = TestEntityOperations()
        self.entity = MockEntity()

    def test_add_entity(self):
        """Test adding a single entity"""
        self.ops._add_entity(self.entity)

        self.ops._client.put_item.assert_called_once_with(
            TableName="test-table",
            Item=self.entity.to_item(),
            ConditionExpression="attribute_not_exists(PK)",
        )

    def test_add_entity_custom_condition(self):
        """Test adding entity with custom condition"""
        custom_condition = "custom_condition"
        self.ops._add_entity(self.entity, custom_condition)

        self.ops._client.put_item.assert_called_once_with(
            TableName="test-table",
            Item=self.entity.to_item(),
            ConditionExpression=custom_condition,
        )

    def test_update_entity(self):
        """Test updating a single entity"""
        self.ops._update_entity(self.entity)

        self.ops._client.put_item.assert_called_once_with(
            TableName="test-table",
            Item=self.entity.to_item(),
            ConditionExpression="attribute_exists(PK)",
        )

    def test_delete_entity(self):
        """Test deleting a single entity"""
        self.ops._delete_entity(self.entity)

        self.ops._client.delete_item.assert_called_once_with(
            TableName="test-table",
            Key=self.entity.key(),
            ConditionExpression="attribute_exists(PK)",
        )


class TestBatchOperationsMixin:
    """Test the batch operations mixin"""

    def setup_method(self):
        self.ops = TestEntityOperations()

    def test_batch_write_with_retry_success(self):
        """Test successful batch write"""
        request_items = [
            {"PutRequest": {"Item": {"PK": {"S": "TEST#1"}}}},
            {"PutRequest": {"Item": {"PK": {"S": "TEST#2"}}}},
        ]

        # Mock successful response
        self.ops._client.batch_write_item.return_value = {
            "UnprocessedItems": {}
        }

        self.ops._batch_write_with_retry(request_items)

        self.ops._client.batch_write_item.assert_called_once_with(
            RequestItems={"test-table": request_items}
        )

    def test_batch_write_with_retry_unprocessed_items(self):
        """Test batch write with unprocessed items that succeed on retry"""
        request_items = [{"PutRequest": {"Item": {"PK": {"S": "TEST#1"}}}}]

        # First call has unprocessed items, second call succeeds
        self.ops._client.batch_write_item.side_effect = [
            {
                "UnprocessedItems": {
                    "test-table": [
                        {"PutRequest": {"Item": {"PK": {"S": "TEST#1"}}}}
                    ]
                }
            },
            {"UnprocessedItems": {}},
        ]

        self.ops._batch_write_with_retry(request_items)

        assert self.ops._client.batch_write_item.call_count == 2

    def test_batch_write_with_retry_max_retries_exceeded(self):
        """Test batch write fails after max retries"""
        request_items = [{"PutRequest": {"Item": {"PK": {"S": "TEST#1"}}}}]

        # Always return unprocessed items
        self.ops._client.batch_write_item.return_value = {
            "UnprocessedItems": {
                "test-table": [
                    {"PutRequest": {"Item": {"PK": {"S": "TEST#1"}}}}
                ]
            }
        }

        with pytest.raises(
            DynamoDBError, match="Failed to process all items after 3 retries"
        ):
            self.ops._batch_write_with_retry(request_items, max_retries=3)

        assert (
            self.ops._client.batch_write_item.call_count == 4
        )  # Initial + 3 retries

    def test_batch_write_chunking(self):
        """Test that large batches are properly chunked"""
        # Create 30 items to test chunking (should be split into 25 + 5)
        request_items = [
            {"PutRequest": {"Item": {"PK": {"S": f"TEST#{i}"}}}}
            for i in range(30)
        ]

        self.ops._client.batch_write_item.return_value = {
            "UnprocessedItems": {}
        }

        self.ops._batch_write_with_retry(request_items)

        # Should be called twice: once for first 25 items, once for remaining 5
        assert self.ops._client.batch_write_item.call_count == 2

        # Check first call had 25 items
        first_call_items = self.ops._client.batch_write_item.call_args_list[0][
            1
        ]["RequestItems"]["test-table"]
        assert len(first_call_items) == 25

        # Check second call had 5 items
        second_call_items = self.ops._client.batch_write_item.call_args_list[
            1
        ][1]["RequestItems"]["test-table"]
        assert len(second_call_items) == 5


class TestTransactionalOperationsMixin:
    """Test the transactional operations mixin"""

    def setup_method(self):
        self.ops = TestEntityOperations()

    def test_transact_write_with_chunking_small_batch(self):
        """Test transactional write with small batch"""
        transact_items = [
            {
                "Put": {
                    "TableName": "test-table",
                    "Item": {"PK": {"S": "TEST#1"}},
                }
            },
            {
                "Put": {
                    "TableName": "test-table",
                    "Item": {"PK": {"S": "TEST#2"}},
                }
            },
        ]

        self.ops._transact_write_with_chunking(transact_items)

        self.ops._client.transact_write_items.assert_called_once_with(
            TransactItems=transact_items
        )

    def test_transact_write_with_chunking_large_batch(self):
        """Test transactional write with large batch gets chunked"""
        # Create 30 items to test chunking
        transact_items = [
            {
                "Put": {
                    "TableName": "test-table",
                    "Item": {"PK": {"S": f"TEST#{i}"}},
                }
            }
            for i in range(30)
        ]

        self.ops._transact_write_with_chunking(transact_items)

        # Should be called twice: once for first 25 items, once for remaining 5
        assert self.ops._client.transact_write_items.call_count == 2

        # Check first call had 25 items
        first_call_items = (
            self.ops._client.transact_write_items.call_args_list[0][1][
                "TransactItems"
            ]
        )
        assert len(first_call_items) == 25

        # Check second call had 5 items
        second_call_items = (
            self.ops._client.transact_write_items.call_args_list[1][1][
                "TransactItems"
            ]
        )
        assert len(second_call_items) == 5


class TestErrorHandlingDecorator:
    """Test the error handling decorator"""

    def setup_method(self):
        self.ops = TestEntityOperations()

    def test_decorator_handles_client_error(self):
        """Test that decorator properly handles ClientError"""

        @handle_dynamodb_errors("test_operation")
        def test_method(self):
            raise ClientError(
                {"Error": {"Code": "ValidationException"}}, "PutItem"
            )

        # Bind the method to our test instance
        bound_method = test_method.__get__(self.ops, TestEntityOperations)

        with pytest.raises(DynamoDBValidationError):
            bound_method()

    def test_decorator_passes_through_success(self):
        """Test that decorator doesn't interfere with successful operations"""

        @handle_dynamodb_errors("test_operation")
        def test_method(self):
            return "success"

        # Bind the method to our test instance
        bound_method = test_method.__get__(self.ops, TestEntityOperations)

        result = bound_method()
        assert result == "success"

    def test_decorator_passes_through_non_client_errors(self):
        """Test that decorator doesn't handle non-ClientError exceptions"""

        @handle_dynamodb_errors("test_operation")
        def test_method(self):
            raise ValueError("Regular ValueError")

        # Bind the method to our test instance
        bound_method = test_method.__get__(self.ops, TestEntityOperations)

        with pytest.raises(ValueError, match="Regular ValueError"):
            bound_method()
