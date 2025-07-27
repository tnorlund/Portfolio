"""
Mixin classes for common DynamoDB operations.

This module provides reusable mixins that can be composed to create
data access classes with common CRUD functionality.
"""

import time
from typing import TYPE_CHECKING, Any, Dict, List, Type

from .error_config import ErrorMessageConfig
from .error_handlers import handle_dynamodb_errors
from .validators import EntityValidator

if TYPE_CHECKING:
    # Use the type from mypy_boto3_dynamodb for better type safety
    from mypy_boto3_dynamodb import DynamoDBClient


class SingleEntityCRUDMixin:
    """
    Mixin providing single entity CRUD operations.

    Requires the using class to implement DynamoOperationsProtocol:
    - table_name: str
    - _client: DynamoDBClient

    This mixin adds add, update, and delete functionality for single entities
    with consistent validation and error handling.
    """

    # Declare expected attributes for type checker
    if TYPE_CHECKING:
        table_name: str
        _client: "DynamoDBClient"

    def _ensure_validator_initialized(self) -> None:
        """Ensure validator is initialized."""
        if not hasattr(self, "_validator") or self._validator is None:
            config: ErrorMessageConfig = getattr(
                self, "_error_config", ErrorMessageConfig()
            )
            self._validator: EntityValidator = EntityValidator(config)

    @handle_dynamodb_errors("add_entity")
    def _add_entity(
        self,
        entity: Any,
        entity_class: Type[Any],
        param_name: str,
        condition_expression: str = "attribute_not_exists(PK)",
        **kwargs: Any,
    ) -> None:
        """
        Add a single entity to DynamoDB with validation.

        Args:
            entity: The entity to add
            entity_class: Expected class of the entity
            param_name: Parameter name for error messages
            condition_expression: Condition to prevent duplicates
            **kwargs: Additional arguments for put_item
        """
        self._ensure_validator_initialized()
        self._validator.validate_entity(entity, entity_class, param_name)

        item = entity.to_item()

        # Build put_item parameters
        put_params = {
            "TableName": self.table_name,
            "Item": item,
            "ConditionExpression": condition_expression,
            **kwargs,
        }

        self._client.put_item(**put_params)

    @handle_dynamodb_errors("update_entity")
    def _update_entity(
        self,
        entity: Any,
        entity_class: Type[Any],
        param_name: str,
        condition_expression: str = "attribute_exists(PK)",
        **kwargs: Any,
    ) -> None:
        """
        Update a single entity in DynamoDB with validation.

        Args:
            entity: The entity to update
            entity_class: Expected class of the entity
            param_name: Parameter name for error messages
            condition_expression: Condition to ensure entity exists
            **kwargs: Additional arguments for put_item
        """
        self._ensure_validator_initialized()
        self._validator.validate_entity(entity, entity_class, param_name)

        item = entity.to_item()

        # Build put_item parameters
        put_params = {
            "TableName": self.table_name,
            "Item": item,
            "ConditionExpression": condition_expression,
            **kwargs,
        }

        self._client.put_item(**put_params)

    @handle_dynamodb_errors("delete_entity")
    def _delete_entity(
        self,
        entity: Any,
        entity_class: Type[Any],
        param_name: str,
        condition_expression: str = "attribute_exists(PK)",
        **kwargs: Any,
    ) -> None:
        """
        Delete a single entity from DynamoDB with validation.

        Args:
            entity: The entity to delete
            entity_class: Expected class of the entity
            param_name: Parameter name for error messages
            condition_expression: Condition to ensure entity exists
            **kwargs: Additional arguments for delete_item
        """
        self._ensure_validator_initialized()
        self._validator.validate_entity(entity, entity_class, param_name)

        # Build delete_item parameters
        delete_params = {
            "TableName": self.table_name,
            "Key": entity.key,
            "ConditionExpression": condition_expression,
            **kwargs,
        }

        self._client.delete_item(**delete_params)


class BatchOperationsMixin:
    """
    Mixin providing batch operation functionality.

    Requires the using class to implement DynamoOperationsProtocol:
    - table_name: str
    - _client: DynamoDBClient

    This mixin adds batch write operations with automatic retry logic
    and chunking for large datasets.
    """

    # Declare expected attributes for type checker
    if TYPE_CHECKING:
        table_name: str
        _client: "DynamoDBClient"

    def _ensure_validator_initialized(self) -> None:
        """Ensure validator is initialized."""
        if not hasattr(self, "_validator") or self._validator is None:
            config: ErrorMessageConfig = getattr(
                self, "_error_config", ErrorMessageConfig()
            )
            self._validator: EntityValidator = EntityValidator(config)

    @handle_dynamodb_errors("batch_write")
    def _batch_write_with_retry_dict(
        self,
        request_items: Dict[str, List[Dict[str, Any]]],
        max_retries: int = 3,
        initial_backoff: float = 0.1,
    ) -> None:
        """
        Perform batch write with automatic retry for unprocessed items.

        Args:
            request_items: DynamoDB batch write request items
            max_retries: Maximum number of retries for unprocessed items
            initial_backoff: Initial backoff time in seconds
        """
        backoff = initial_backoff

        for attempt in range(max_retries + 1):
            response = self._client.batch_write_item(
                RequestItems=request_items
            )

            unprocessed_items = response.get("UnprocessedItems", {})
            if not unprocessed_items:
                break

            if attempt < max_retries:
                time.sleep(backoff)
                backoff *= 2  # Exponential backoff
                request_items = unprocessed_items
            else:
                # Final attempt failed, log unprocessed items
                raise RuntimeError(
                    f"Failed to process all items after {max_retries} retries"
                )

    def _prepare_batch_request(
        self, entities: List[Any], operation: str = "PutRequest"
    ) -> Dict[str, List[Dict[str, Any]]]:
        """
        Prepare batch request items from entities.

        Args:
            entities: List of entities to process
            operation: Type of operation ("PutRequest" or "DeleteRequest")

        Returns:
            Formatted request items for batch operation
        """
        items = []

        for entity in entities:
            if operation == "PutRequest":
                items.append({operation: {"Item": entity.to_item()}})
            elif operation == "DeleteRequest":
                items.append({operation: {"Key": entity.key}})

        return {self.table_name: items}

    def _split_into_batches(
        self, entities: List[Any], batch_size: int = 25
    ) -> List[List[Any]]:
        """
        Split entities into batches for processing.

        Args:
            entities: List of entities to split
            batch_size: Maximum size of each batch (DynamoDB limit is 25)

        Returns:
            List of entity batches
        """
        return [
            entities[i : i + batch_size]
            for i in range(0, len(entities), batch_size)
        ]

    @handle_dynamodb_errors("add_entities")
    def _add_entities_batch(
        self, entities: List[Any], entity_class: Type[Any], param_name: str
    ) -> None:
        """
        Add multiple entities using batch operations.

        Args:
            entities: List of entities to add
            entity_class: Expected class of entities
            param_name: Parameter name for error messages
        """
        self._ensure_validator_initialized()
        self._validator.validate_entity_list(
            entities, entity_class, param_name
        )

        # Split into batches and process
        batches = self._split_into_batches(entities)

        for batch in batches:
            request_items = self._prepare_batch_request(batch, "PutRequest")
            self._batch_write_with_retry_dict(request_items)


class TransactionalOperationsMixin:
    """
    Mixin providing transactional operation functionality.

    Requires the using class to implement DynamoOperationsProtocol:
    - table_name: str
    - _client: DynamoDBClient

    This mixin adds transactional write operations with automatic chunking
    for operations that exceed DynamoDB's 25-item transaction limit.
    """

    # Declare expected attributes for type checker
    if TYPE_CHECKING:
        table_name: str
        _client: "DynamoDBClient"

    def _transact_write_items(
        self, transact_items: List[Dict[str, Any]]
    ) -> None:
        """
        Perform transactional write operation.

        Args:
            transact_items: List of transactional write items
        """
        self._client.transact_write_items(TransactItems=transact_items)

    def _prepare_transact_update_item(
        self, entity: Any, condition_expression: str = "attribute_exists(PK)"
    ) -> Dict[str, Any]:
        """
        Prepare a transactional update item.

        Args:
            entity: Entity to update
            condition_expression: Condition for the update

        Returns:
            Formatted transactional update item
        """
        return {
            "Update": {
                "TableName": self.table_name,
                "Key": entity.key,
                "UpdateExpression": entity.update_expression,
                "ExpressionAttributeValues": entity.expression_attribute_values,
                "ConditionExpression": condition_expression,
            }
        }

    def _prepare_transact_put_item(
        self,
        entity: Any,
        condition_expression: str = "attribute_not_exists(PK)",
    ) -> Dict[str, Any]:
        """
        Prepare a transactional put item.

        Args:
            entity: Entity to put
            condition_expression: Condition for the put

        Returns:
            Formatted transactional put item
        """
        return {
            "Put": {
                "TableName": self.table_name,
                "Item": entity.to_item(),
                "ConditionExpression": condition_expression,
            }
        }

    def _transact_write_with_chunking(
        self, transact_items: List[Dict[str, Any]]
    ) -> None:
        """
        Perform transactional write with automatic chunking for large batches.

        Since DynamoDB's transact_write_items supports a maximum of 25
        operations per call, this method splits large lists into chunks
        and processes each chunk in a separate transaction.

        Args:
            transact_items: List of transactional write items

        Raises:
            ValueError: When given invalid parameters
            Exception: For underlying DynamoDB errors
        """
        # DynamoDB transact_write_items has a limit of 25 items per transaction
        chunk_size = 25

        for i in range(0, len(transact_items), chunk_size):
            chunk = transact_items[i : i + chunk_size]
            self._transact_write_items(chunk)

    def _update_entities(
        self,
        entities: List[Any],
        entity_type: Type[Any],
        entity_name: str,
        operation_name: str = None,
    ) -> None:
        """
        Update multiple entities in the database using transactions.

        This is a generic method that handles the common pattern of updating
        a list of entities with transactional writes. Each update is conditional
        upon the entity already existing (attribute_exists(PK)).

        Args:
            entities: List of entities to update
            entity_type: The type of entity for validation
            entity_name: Name of the entity for error messages
            operation_name: Name of the operation for error messages (e.g., "update_words")

        Example:
            # In a data access class
            def update_images(self, images: List[Image]) -> None:
                self._update_entities(images, Image, "images", "update_images")
        """
        if not hasattr(self, "_validator") or self._validator is None:
            if not hasattr(self, "_error_config"):
                self._error_config = ErrorMessageConfig()
            self._validator = EntityValidator(self._error_config)

        self._validator.validate_entity_list(entities, entity_type, entity_name)

        # Build transactional items
        transact_items = []
        for entity in entities:
            # Support different transaction item formats
            if hasattr(entity, "to_item"):
                # Standard format using Put
                transact_items.append(
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": entity.to_item(),
                            "ConditionExpression": "attribute_exists(PK)",
                        }
                    }
                )
            else:
                # For entities that use Update operations
                transact_items.append(
                    self._prepare_transact_update_item(
                        entity, "attribute_exists(PK)"
                    )
                )

        # Use existing chunking method
        self._transact_write_with_chunking(transact_items)
