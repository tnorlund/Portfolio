"""
Mixin classes for common DynamoDB operations.

.. deprecated::
    These mixins are deprecated. Use FlattenedStandardMixin instead,
    which provides all functionality in a single class without deep
    inheritance chains or MRO conflicts.

This module provides reusable mixins that can be composed to create
data access classes with common CRUD functionality.
"""

import warnings
from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Type,
)

from .error_handling import ErrorMessageConfig, handle_dynamodb_errors
from .shared_utils import batch_write_with_retry_dict
from .validators import EntityValidator

if TYPE_CHECKING:
    # Use the type from mypy_boto3_dynamodb for better type safety
    from mypy_boto3_dynamodb import DynamoDBClient


def _emit_deprecation_warning(mixin_name: str) -> None:
    """Emit a deprecation warning for old mixins."""
    warnings.warn(
        f"{mixin_name} is deprecated. Use FlattenedStandardMixin instead, "
        "which provides all functionality without MRO conflicts.",
        DeprecationWarning,
        stacklevel=3,
    )


class SingleEntityCRUDMixin:
    """
    Mixin providing single entity CRUD operations.

    .. deprecated::
        Use FlattenedStandardMixin instead.

    Requires the using class to implement DynamoOperationsProtocol:
    - table_name: str
    - _client: DynamoDBClient

    This mixin adds add, update, and delete functionality for single entities
    with consistent validation and error handling.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        _emit_deprecation_warning("SingleEntityCRUDMixin")

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

        # Use shared implementation
        self._execute_put_item(entity, condition_expression, **kwargs)

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

        # Use shared implementation
        self._execute_put_item(entity, condition_expression, **kwargs)

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

        # Use shared implementation
        self._execute_delete_item(entity, condition_expression, **kwargs)


class BatchOperationsMixin:
    """
    Mixin providing batch operation functionality.

    .. deprecated::
        Use FlattenedStandardMixin instead.

    Requires the using class to implement DynamoOperationsProtocol:
    - table_name: str
    - _client: DynamoDBClient

    This mixin adds batch write operations with automatic retry logic
    and chunking for large datasets.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        _emit_deprecation_warning("BatchOperationsMixin")

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
        request_items: dict[str, list[dict[str, Any]]],
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
        batch_write_with_retry_dict(
            self._client, request_items, max_retries, initial_backoff
        )

    def _prepare_batch_request(
        self, entities: list[Any], operation: str = "PutRequest"
    ) -> dict[str, list[dict[str, Any]]]:
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
        self, entities: list[Any], batch_size: int = 25
    ) -> list[list[Any]]:
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
        self, entities: list[Any], entity_class: Type[Any], param_name: str
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

    .. deprecated::
        Use FlattenedStandardMixin instead.

    Requires the using class to implement DynamoOperationsProtocol:
    - table_name: str
    - _client: DynamoDBClient

    This mixin adds transactional write operations with automatic chunking
    for operations that exceed DynamoDB's 25-item transaction limit.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        _emit_deprecation_warning("TransactionalOperationsMixin")

    # Declare expected attributes for type checker
    if TYPE_CHECKING:
        table_name: str
        _client: "DynamoDBClient"

    def _transact_write_items(
        self, transact_items: list[dict[str, Any]]
    ) -> None:
        """
        Perform transactional write operation.

        Args:
            transact_items: List of transactional write items
        """
        self._client.transact_write_items(TransactItems=transact_items)

    def _prepare_transact_update_item(
        self, entity: Any, condition_expression: str = "attribute_exists(PK)"
    ) -> dict[str, Any]:
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
                "ExpressionAttributeValues": (
                    entity.expression_attribute_values
                ),
                "ConditionExpression": condition_expression,
            }
        }

    def _prepare_transact_put_item(
        self,
        entity: Any,
        condition_expression: str = "attribute_not_exists(PK)",
    ) -> dict[str, Any]:
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
        self, transact_items: list[dict[str, Any]]
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
        entities: list[Any],
        entity_type: Type[Any],
        entity_name: str,
    ) -> None:
        """
        Update multiple entities in the database using transactions.

        This is a generic method that handles the common pattern of
        updating a list of entities with transactional writes. Each update
        is conditional
        upon the entity already existing (attribute_exists(PK)).

        Args:
            entities: List of entities to update
            entity_type: The type of entity for validation
            entity_name: Name of the entity for error messages

        Example:
            # In a data access class
            def update_images(self, images: list[Image]) -> None:
                self._update_entities(images, Image, "images", "update_images")
        """
        if not hasattr(self, "_validator") or self._validator is None:
            if not hasattr(self, "_error_config"):
                self._error_config = ErrorMessageConfig()
            self._validator = EntityValidator(self._error_config)

        self._validator.validate_entity_list(
            entities, entity_type, entity_name
        )

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


class QueryByTypeMixin:
    """
    Mixin for querying entities by TYPE using GSITYPE index.

    .. deprecated::
        Use FlattenedStandardMixin instead.

    This mixin provides a standardized way to query all entities of a specific
    type using the GSITYPE global secondary index, reducing code duplication
    across data access classes.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        _emit_deprecation_warning("QueryByTypeMixin")

    # Declare expected attributes for type checker
    if TYPE_CHECKING:
        table_name: str
        _client: "DynamoDBClient"
        _query_entities: Callable[
            ..., tuple[list[Any], dict[str, Any] | None]
        ]
        _validate_entity: Callable[..., None]

    @handle_dynamodb_errors("query_by_type")
    def _query_by_type(
        self,
        entity_type: str,
        converter_func: "Callable[[dict[str, Any]], Any]",
        limit: int | None = None,
        last_evaluated_key: dict[str, Any] | None = None,
    ) -> tuple[list[Any], dict[str, Any] | None]:
        """
        Query all entities of a specific type using GSITYPE index.

        Args:
            entity_type: The TYPE value to query for
            converter_func: Function to convert DynamoDB items to entity
                objects
            limit: Maximum number of items to return
            last_evaluated_key: Key to continue pagination from previous query

        Returns:
            Tuple of (list of entities, last_evaluated_key for pagination)
        """
        # Validate inputs
        if not entity_type:
            raise ValueError("entity_type cannot be empty")

        if limit is not None:
            if not isinstance(limit, int) or limit <= 0:
                raise ValueError("Limit must be a positive integer")

        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise ValueError("last_evaluated_key must be a dictionary")

        return self._query_entities(
            index_name="GSITYPE",
            key_condition_expression="#t = :val",
            expression_attribute_names={"#t": "TYPE"},
            expression_attribute_values={":val": {"S": entity_type}},
            converter_func=converter_func,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )


class QueryByParentMixin:
    """
    Mixin for querying child entities by parent ID.

    .. deprecated::
        Use FlattenedStandardMixin instead.

    This mixin provides a standardized way to query child entities that belong
    to a parent entity using PK/SK prefix matching, reducing code duplication
    for hierarchical queries.
    """

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        _emit_deprecation_warning("QueryByParentMixin")

    # Declare expected attributes for type checker
    if TYPE_CHECKING:
        table_name: str
        _client: "DynamoDBClient"
        _query_entities: Callable[
            ..., tuple[list[Any], dict[str, Any] | None]
        ]

    @handle_dynamodb_errors("query_by_parent")
    def _query_by_parent(
        self,
        parent_pk: str,
        child_sk_prefix: str,
        converter_func: "Callable[[dict[str, Any]], Any]",
        *,
        limit: int | None = None,
        last_evaluated_key: dict[str, Any] | None = None,
        filter_expression: str | None = None,
        expression_attribute_names: dict[str, str] | None = None,
        expression_attribute_values: dict[str, Any] | None = None,
    ) -> tuple[list[Any], dict[str, Any] | None]:
        """
        Query child entities by parent using PK and SK prefix.

        Args:
            parent_pk: The parent entity's primary key (e.g., "IMAGE#uuid")
            child_sk_prefix: The prefix for child sort keys (e.g., "RECEIPT#")
            converter_func: Function to convert DynamoDB items to entity
                objects
            limit: Maximum number of items to return
            last_evaluated_key: Key to continue pagination from previous query

        Returns:
            Tuple of (list of entities, last_evaluated_key for pagination)
        """
        # Validate inputs
        if not parent_pk:
            raise ValueError("parent_pk cannot be empty")

        if not child_sk_prefix:
            raise ValueError("child_sk_prefix cannot be empty")

        if limit is not None:
            if not isinstance(limit, int) or limit <= 0:
                raise ValueError("Limit must be a positive integer")

        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise ValueError("last_evaluated_key must be a dictionary")

        # Build expression attribute names and values
        expr_names = {"#pk": "PK", "#sk": "SK"}
        expr_values = {
            ":pk": {"S": parent_pk},
            ":sk_prefix": {"S": child_sk_prefix},
        }

        # Merge additional expression attributes if provided
        if expression_attribute_names:
            expr_names.update(expression_attribute_names)
        if expression_attribute_values:
            expr_values.update(expression_attribute_values)

        return self._query_entities(
            index_name=None,  # Query main table
            key_condition_expression=(
                "#pk = :pk AND begins_with(#sk, :sk_prefix)"
            ),
            expression_attribute_names=expr_names,
            expression_attribute_values=expr_values,
            converter_func=converter_func,
            filter_expression=filter_expression,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )
