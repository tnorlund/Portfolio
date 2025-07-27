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
    Type,
    Union,
)

from botocore.exceptions import ClientError

from receipt_dynamo.data._base import DynamoClientProtocol

from .error_config import ErrorMessageConfig
from .error_handlers import ErrorHandler
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

    def _ensure_initialized(self) -> None:
        """Lazily initialize error handling components."""
        if not hasattr(self, "_error_config") or self._error_config is None:
            self._error_config: ErrorMessageConfig = ErrorMessageConfig()

        if not hasattr(self, "_error_handler") or self._error_handler is None:
            self._error_handler: ErrorHandler = ErrorHandler(
                self._error_config
            )

        if not hasattr(self, "_validator") or self._validator is None:
            self._validator: EntityValidator = EntityValidator(
                self._error_config
            )

    def _handle_client_error(
        self,
        error: ClientError,
        operation: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> NoReturn:
        """
        Handle DynamoDB ClientError with appropriate exception types and messages.

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
        self._error_handler.handle_client_error(error, operation, context)

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
        self._validator.validate_entity_list(
            entities, entity_class, param_name
        )

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
        item = entity.to_item()

        # Build put_item parameters
        put_params = {
            "TableName": self.table_name,
            "Item": item,
            "ConditionExpression": condition_expression,
            **kwargs,
        }

        self._client.put_item(**put_params)

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
        item = entity.to_item()

        # Build put_item parameters
        put_params = {
            "TableName": self.table_name,
            "Item": item,
            "ConditionExpression": condition_expression,
            **kwargs,
        }

        self._client.put_item(**put_params)

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
        # Build delete_item parameters
        delete_params = {
            "TableName": self.table_name,
            "Key": entity.key,
            "ConditionExpression": condition_expression,
            **kwargs,
        }

        self._client.delete_item(**delete_params)

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
            max_retries: Maximum number of retries for unprocessed items
            initial_backoff: Initial backoff time in seconds
        """
        import time

        # Format request items for DynamoDB
        formatted_items = {self.table_name: request_items}
        backoff = initial_backoff

        for attempt in range(max_retries + 1):
            response = self._client.batch_write_item(
                RequestItems=formatted_items
            )

            unprocessed_items = response.get("UnprocessedItems", {})
            if not unprocessed_items:
                break

            if attempt < max_retries:
                time.sleep(backoff)
                backoff *= 2  # Exponential backoff
                formatted_items = unprocessed_items
            else:
                # Final attempt failed, log unprocessed items
                raise RuntimeError(
                    f"Failed to process all items after {max_retries} retries"
                )
