"""
Base class for DynamoDB operations.

This module provides the core base class that all DynamoDB data access
classes should inherit from, providing common functionality and error handling.
"""

from typing import (
    TYPE_CHECKING,
    Any,
    Dict,
    List,
    NoReturn,
    Optional,
    Tuple,
    Type,
)

from botocore.exceptions import ClientError

from .error_handling import ErrorHandler, ErrorMessageConfig
from .shared_utils import (
    batch_write_with_retry,
    build_get_item_key,
    validate_pagination_params,
)
from .types import DynamoClientProtocol
from .validators import EntityValidator

if TYPE_CHECKING:
    # Use the type from mypy_boto3_dynamodb for better type safety
    from mypy_boto3_dynamodb import DynamoDBClient


class DynamoDBBaseOperations(DynamoClientProtocol):
    """
    Base class for all DynamoDB operations with common functionality.

    This class provides centralized error handling, validation, and common
    operation patterns that are shared across all entity data access classes.

    Attributes that must be provided by concrete implementations:
    - table_name: str - The DynamoDB table name
    - _client: DynamoDBClient - The boto3 DynamoDB client instance
    """

    # Declare protocol-required attributes for type checker
    if TYPE_CHECKING:
        table_name: str
        _client: "DynamoDBClient"

    def __init__(self) -> None:
        """Initialize the base operations with lazy-loaded components."""
        self._error_config: Optional[ErrorMessageConfig] = None
        self._error_handler: Optional[ErrorHandler] = None
        self._validator: Optional[EntityValidator] = None

    def _ensure_initialized(self) -> None:
        """Lazily initialize error handling components."""
        if not hasattr(self, "_error_config") or self._error_config is None:
            self._error_config: ErrorMessageConfig = ErrorMessageConfig()

        if not hasattr(self, "_error_handler") or self._error_handler is None:
            self._error_handler: ErrorHandler = ErrorHandler(self._error_config)

        if not hasattr(self, "_validator") or self._validator is None:
            self._validator: EntityValidator = EntityValidator(self._error_config)

    def _handle_client_error(
        self,
        error: ClientError,
        operation: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> NoReturn:
        """
        Handle DynamoDB ClientError with appropriate exception types and
        messages.

        This method delegates to the centralized error handler for consistent
        error handling across all operations.

        Args:
            error: The original ClientError from boto3
            operation: Name of the operation that failed
            context: Additional context (args, kwargs) from the operation

        Raises:
            Appropriate exception based on the error type
        """
        self._ensure_initialized()
        assert self._error_handler is not None  # For type checker
        self._error_handler.handle_client_error(error, operation, **context)

    def _validate_entity(
        self, entity: Any, entity_class: Type[Any], param_name: str
    ) -> None:
        """
        Common entity validation logic with consistent error messages.

        Args:
            entity: The entity to validate
            entity_class: The expected class of the entity
            param_name: Name of parameter for error messages

        Raises:
            ValueError: If validation fails
        """
        self._ensure_initialized()
        assert self._validator is not None  # For type checker
        self._validator.validate_entity(entity, entity_class, param_name)

    def _validate_entity_list(
        self, entities: List[Any], entity_class: Type[Any], param_name: str
    ) -> None:
        """
        Validate a list of entities with consistent error messages.

        Args:
            entities: List of entities to validate
            entity_class: Expected class of entities
            param_name: Name of parameter for error messages

        Raises:
            ValueError: If validation fails
        """
        self._ensure_initialized()
        assert self._validator is not None  # For type checker
        self._validator.validate_entity_list(entities, entity_class, param_name)

    def _execute_put_item(
        self,
        entity: Any,
        condition_expression: str | None,
        **kwargs: Any,
    ) -> None:
        """
        Execute a put_item operation with the given entity.

        Args:
            entity: The entity to put
            condition_expression: Condition expression for the operation (optional)
            **kwargs: Additional arguments for put_item
        """
        item = entity.to_item()

        # Build put_item parameters
        put_params: dict[str, Any] = {
            "TableName": self.table_name,
            "Item": item,
            **kwargs,
        }

        # Only include ConditionExpression if provided
        if condition_expression is not None:
            put_params["ConditionExpression"] = condition_expression

        self._client.put_item(**put_params)

    def _execute_delete_item(
        self,
        entity: Any,
        condition_expression: str,
        **kwargs: Any,
    ) -> None:
        """
        Execute a delete_item operation with the given entity.

        Args:
            entity: The entity to delete
            condition_expression: Condition expression for the operation
            **kwargs: Additional arguments for delete_item
        """
        # Build delete_item parameters
        delete_params = {
            "TableName": self.table_name,
            "Key": entity.key,
            **kwargs,
        }

        # Only add ConditionExpression if provided
        if condition_expression is not None:
            delete_params["ConditionExpression"] = condition_expression

        self._client.delete_item(**delete_params)

    def _add_entity(
        self,
        entity: Any,
        condition_expression: str = "attribute_not_exists(PK)",
        **kwargs: Any,
    ) -> None:
        """
        Add a single entity to DynamoDB.

        Args:
            entity: The entity to add
            condition_expression: Condition to prevent duplicates
            **kwargs: Additional arguments for put_item
        """
        self._execute_put_item(entity, condition_expression, **kwargs)

    def _update_entity(
        self,
        entity: Any,
        condition_expression: str = "attribute_exists(PK)",
        **kwargs: Any,
    ) -> None:
        """
        Update a single entity in DynamoDB.

        Args:
            entity: The entity to update
            condition_expression: Condition to ensure entity exists
            **kwargs: Additional arguments for put_item
        """
        self._execute_put_item(entity, condition_expression, **kwargs)

    def _delete_entity(
        self,
        entity: Any,
        condition_expression: str = "attribute_exists(PK)",
        **kwargs: Any,
    ) -> None:
        """
        Delete a single entity from DynamoDB (backward compatibility method).

        Args:
            entity: The entity to delete
            condition_expression: Condition to ensure entity exists
            **kwargs: Additional arguments for delete_item
        """
        self._execute_delete_item(entity, condition_expression, **kwargs)

    def _delete_entities(
        self,
        entities: List[Any],
        condition_expression: str = "attribute_exists(PK)",
        **kwargs: Any,
    ) -> None:
        """Write multiple entities to DynamoDB using transactional write."""
        # DynamoDB transact_write_items has a limit of 25 items per transaction
        chunk_size = 25

        for i in range(0, len(entities), chunk_size):
            chunk = entities[i : i + chunk_size]
            transact_items = []
            for entity in chunk:
                item = {
                    "Delete": {
                        "TableName": self.table_name,
                        "Key": entity.key,
                        "ConditionExpression": condition_expression,
                    }
                }
                # Add any extra kwargs to the transaction item
                if kwargs:
                    item["Delete"].update(kwargs)
                transact_items.append(item)

            if transact_items:
                self._client.transact_write_items(TransactItems=transact_items)

    def _get_entity(
        self,
        primary_key: str,
        sort_key: str,
        entity_class: Type[Any],
        converter_func: Optional[Any] = None,
        consistent_read: bool = False,
    ) -> Optional[Any]:
        """
        Get a single entity from DynamoDB.

        Args:
            primary_key: The primary key value
            sort_key: The sort key value
            entity_class: The class to instantiate from the item
            converter_func: Optional function to convert item to entity
            consistent_read: Whether to use consistent read

        Returns:
            An instance of entity_class or None if not found

        Raises:
            ClientError: If the DynamoDB operation fails
        """
        response = self._client.get_item(
            TableName=self.table_name,
            Key=build_get_item_key(primary_key, sort_key),
            ConsistentRead=consistent_read,
        )

        item = response.get("Item")
        if not item:
            return None

        # Use converter function if provided
        if converter_func:
            return converter_func(item)
        # Check if entity_class has a from_item method
        if hasattr(entity_class, "from_item"):
            return entity_class.from_item(item)
        # Fallback - return raw item
        return item

    def _batch_write_with_retry(
        self,
        request_items: List[Any],
        max_retries: int = 3,
        initial_backoff: float = 0.1,
    ) -> None:
        """
        Perform batch write with automatic retry for unprocessed items.

        Args:
            request_items: List of write request items
            max_retries: Maximum number of retries for unprocessed
                items
            initial_backoff: Initial backoff time in seconds
        """
        batch_write_with_retry(
            self._client,
            self.table_name,
            request_items,
            max_retries,
            initial_backoff,
        )

    def _query_entities(
        self,
        index_name: Optional[str],
        key_condition_expression: str,
        expression_attribute_names: Optional[Dict[str, str]],
        expression_attribute_values: Dict[str, Any],
        converter_func: Any,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict] = None,
        scan_index_forward: bool = True,
        filter_expression: Optional[str] = None,
    ) -> Tuple[List[Any], Optional[Dict]]:
        """
        Query entities from DynamoDB with pagination support.

        Args:
            index_name: The name of the GSI to query (None for main table)
            key_condition_expression: The key condition expression
            expression_attribute_names: Optional attribute name mappings
            expression_attribute_values: The expression attribute values
            converter_func: Function to convert items to entities
            limit: Maximum number of items to return
            last_evaluated_key: Key to start from for pagination
            scan_index_forward: Whether to sort in ascending order
            filter_expression: Optional filter expression to apply after key
                condition

        Returns:
            Tuple of entities list and last evaluated key for pagination

        Raises:
            ClientError: If the DynamoDB operation fails
        """
        entities = []
        query_params: Dict[str, Any] = {
            "TableName": self.table_name,
            "KeyConditionExpression": key_condition_expression,
            "ExpressionAttributeValues": expression_attribute_values,
        }

        # Only add ScanIndexForward if it's not the default value
        if not scan_index_forward:
            query_params["ScanIndexForward"] = scan_index_forward

        if index_name:
            query_params["IndexName"] = index_name

        if expression_attribute_names:
            query_params["ExpressionAttributeNames"] = expression_attribute_names

        if filter_expression:
            query_params["FilterExpression"] = filter_expression

        if last_evaluated_key is not None:
            query_params["ExclusiveStartKey"] = last_evaluated_key

        # Handle pagination based on whether limit is provided
        if limit is None:
            # If no limit, retrieve all items
            response = self._client.query(**query_params)
            entities.extend([converter_func(item) for item in response["Items"]])

            while "LastEvaluatedKey" in response and response["LastEvaluatedKey"]:
                query_params["ExclusiveStartKey"] = response["LastEvaluatedKey"]
                response = self._client.query(**query_params)
                entities.extend([converter_func(item) for item in response["Items"]])
            last_evaluated_key = None
        else:
            # If limit is provided, accumulate items until we reach the limit
            remaining = limit
            last_evaluated_key = None

            while remaining > 0:
                # Query for at most 'remaining' items
                query_params["Limit"] = remaining

                response = self._client.query(**query_params)
                items = response.get("Items", [])

                # Convert and add items
                for item in items:
                    entity = converter_func(item)
                    if entity is not None:  # Skip None results from converter
                        entities.append(entity)
                        remaining -= 1
                        if remaining == 0:
                            break

                # Check if there are more pages
                last_evaluated_key = response.get("LastEvaluatedKey")
                if not last_evaluated_key or remaining == 0:
                    break

                query_params["ExclusiveStartKey"] = last_evaluated_key

            # If we've collected all requested items but there's still a LEK,
            # we need to return None to indicate no more pages for the user
            if remaining == 0 and last_evaluated_key and len(items) > 0:
                # We might have more items in the current page that we didn't
                # process
                # In this case, keep the LEK to indicate there are more items
                pass
            elif remaining > 0:
                # We ran out of items before reaching the limit
                last_evaluated_key = None

        return entities, last_evaluated_key

    def _validate_pagination_params(
        self,
        limit: Optional[int],
        last_evaluated_key: Optional[Dict[str, Any]],
    ) -> None:
        """Validate pagination parameters."""
        validate_pagination_params(
            limit, last_evaluated_key, validate_attribute_format=True
        )
