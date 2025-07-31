"""
Flattened standard mixin implementation.

This module contains the FlattenedStandardMixin class that provides all
standard DynamoDB operations in a single class without deep inheritance chains.
"""

from typing import (
    TYPE_CHECKING,
    Any,
    Callable,
    Dict,
    List,
    Optional,
    Tuple,
    Type,
    TypeVar,
)

from botocore.exceptions import ClientError

from receipt_dynamo.data.shared_exceptions import (
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityValidationError,
)
from receipt_dynamo.entities.util import assert_valid_uuid

from .error_handling import ErrorMessageConfig, handle_dynamodb_errors
from .shared_utils import (
    build_get_item_key,
    build_query_params,
    validate_pagination_params,
)
from .types import (
    QueryInputTypeDef,
    TransactWriteItemTypeDef,
    WriteRequestTypeDef,
)
from .validators import EntityValidator

T = TypeVar("T")

if TYPE_CHECKING:
    from mypy_boto3_dynamodb import DynamoDBClient


class FlattenedStandardMixin:
    """
    A flattened mixin that provides all standard DynamoDB operations.

    This approach avoids deep inheritance chains while providing the same
    functionality as StandardAccessorMixin. By implementing all methods
    directly, we respect pylint's max-ancestors limit while maintaining
    clean, reusable code.

    Expects the implementing class to provide:
    - table_name: str
    - _client: DynamoDBClient

    This is the RECOMMENDED approach for all new accessor classes as it:
    - Respects pylint's max-ancestors=7 limit (only 2 ancestors total)
    - Provides all functionality in one place
    - Easier to understand and maintain
    - No deep inheritance chains to debug
    """

    # Declare expected attributes for type checking
    if TYPE_CHECKING:
        table_name: str
        _client: "DynamoDBClient"
        _validator: EntityValidator
        _error_config: ErrorMessageConfig

    def _ensure_validator_initialized(self) -> None:
        """Ensure the validator is initialized."""
        if not hasattr(self, "_validator"):
            self._error_config = ErrorMessageConfig()
            self._validator = EntityValidator(self._error_config)

    # ========== Validation Methods ==========

    def _validate_entity(
        self, entity: Any, expected_type: Type[T], param_name: str
    ) -> None:
        """Validate a single entity."""
        self._ensure_validator_initialized()
        self._validator.validate_entity(entity, expected_type, param_name)

    def _validate_entity_list(
        self, entities: List[Any], expected_type: Type[T], param_name: str
    ) -> None:
        """Validate a list of entities."""
        self._ensure_validator_initialized()
        self._validator.validate_entity_list(
            entities, expected_type, param_name
        )

    def _validate_image_id(self, image_id: str) -> None:
        """Validate an image ID."""
        if image_id is None:
            raise EntityValidationError("image_id cannot be None")
        assert_valid_uuid(image_id)

    def _validate_receipt_id(
        self, receipt_id: int, param_name: str = "receipt_id"
    ) -> None:
        """Validate receipt_id is not None and is a positive integer."""
        if receipt_id is None:
            raise EntityValidationError(f"{param_name} cannot be None")
        if not isinstance(receipt_id, int) or receipt_id <= 0:
            raise EntityValidationError(
                f"{param_name} must be a positive integer"
            )

    def _validate_pagination_params(
        self,
        limit: Optional[int],
        last_evaluated_key: Optional[Dict[str, Any]],
    ) -> None:
        """Validate pagination parameters."""
        validate_pagination_params(limit, last_evaluated_key)

    # ========== Single Entity CRUD Operations ==========

    @handle_dynamodb_errors("add_entity")
    def _add_entity(
        self, entity: T, condition_expression: Optional[str] = None
    ) -> None:
        """Add a single entity to DynamoDB."""
        put_params = {
            "TableName": self.table_name,
            "Item": entity.to_item(),
        }
        if condition_expression:
            put_params["ConditionExpression"] = condition_expression

        try:
            self._client.put_item(**put_params)
        except ClientError as e:
            if (
                e.response["Error"]["Code"]
                == "ConditionalCheckFailedException"
            ):
                entity_name = entity.__class__.__name__
                entity_id = getattr(entity, "id", "unknown")
                raise EntityAlreadyExistsError(
                    f"{entity_name} with ID {entity_id} already exists"
                ) from e
            raise

    def _add_entities(
        self, entities: List[T], entity_type: Type[T], param_name: str
    ) -> None:
        """Add multiple entities using transactional writes with existence check."""
        self._validate_entity_list(entities, entity_type, param_name)

        # Use transactional writes to ensure entities don't already exist
        transact_items = []
        for entity in entities:
            transact_items.append(
                TransactWriteItemTypeDef(
                    Put={
                        "TableName": self.table_name,
                        "Item": entity.to_item(),
                        "ConditionExpression": "attribute_not_exists(PK)",
                    }
                )
            )

        # Process in chunks (transact_write_items has a limit of 25)
        self._transact_write_with_chunking(transact_items)

    @handle_dynamodb_errors("update_entity")
    def _update_entity(
        self, entity: T, condition_expression: Optional[str] = None
    ) -> None:
        """Update a single entity in DynamoDB."""
        put_params = {
            "TableName": self.table_name,
            "Item": entity.to_item(),
        }
        if condition_expression:
            put_params["ConditionExpression"] = condition_expression

        try:
            self._client.put_item(**put_params)
        except ClientError as e:
            if (
                e.response["Error"]["Code"]
                == "ConditionalCheckFailedException"
            ):
                entity_name = entity.__class__.__name__
                entity_id = getattr(entity, "id", "unknown")
                raise EntityNotFoundError(
                    f"{entity_name} with ID {entity_id} does not exist"
                ) from e
            raise

    @handle_dynamodb_errors("delete_entity")
    def _delete_entity(
        self, entity: T, condition_expression: Optional[str] = None
    ) -> None:
        """Delete a single entity from DynamoDB."""
        delete_params = {
            "TableName": self.table_name,
            "Key": entity.key,
        }
        if condition_expression:
            delete_params["ConditionExpression"] = condition_expression

        try:
            self._client.delete_item(**delete_params)
        except ClientError as e:
            if (
                e.response["Error"]["Code"]
                == "ConditionalCheckFailedException"
            ):
                entity_name = entity.__class__.__name__
                entity_id = getattr(entity, "id", "unknown")
                raise EntityNotFoundError(
                    f"{entity_name} with ID {entity_id} does not exist"
                ) from e
            raise

    @handle_dynamodb_errors("get_entity")
    def _get_entity(
        self,
        primary_key: str,
        sort_key: str,
        entity_class: Type[T],  # pylint: disable=unused-argument
        converter_func: Callable[[Dict[str, Any]], T],
    ) -> Optional[T]:
        """Get a single entity from DynamoDB."""
        response = self._client.get_item(
            TableName=self.table_name,
            Key=build_get_item_key(primary_key, sort_key),
        )

        if "Item" not in response:
            return None

        return converter_func(response["Item"])

    # ========== Batch Operations ==========

    def _batch_write_with_retry(
        self, request_items: List[WriteRequestTypeDef]
    ) -> None:
        """Execute batch write operations with retry logic."""
        remaining_items = request_items

        while remaining_items:
            # Split into chunks of 25 (DynamoDB limit)
            batch = remaining_items[:25]
            remaining_items = remaining_items[25:]

            response = self._client.batch_write_item(
                RequestItems={self.table_name: batch}
            )

            # Handle unprocessed items
            if "UnprocessedItems" in response:
                unprocessed = response["UnprocessedItems"].get(
                    self.table_name, []
                )
                if unprocessed:
                    remaining_items.extend(unprocessed)

    # ========== Transactional Operations ==========

    def _transact_write_with_chunking(
        self, transact_items: List[TransactWriteItemTypeDef]
    ) -> None:
        """Execute transactional writes with chunking for large batches."""
        # Process in chunks of 25 (DynamoDB transact limit)
        for i in range(0, len(transact_items), 25):
            chunk = transact_items[i : i + 25]
            self._client.transact_write_items(TransactItems=chunk)

    @handle_dynamodb_errors("update_entities")
    def _update_entities(
        self, entities: List[T], entity_type: Type[T], param_name: str
    ) -> None:
        """Update multiple entities using transactions."""
        self._validate_entity_list(entities, entity_type, param_name)

        transact_items = []
        for entity in entities:
            transact_items.append(
                TransactWriteItemTypeDef(
                    Put={
                        "TableName": self.table_name,
                        "Item": entity.to_item(),
                        "ConditionExpression": "attribute_exists(PK)",
                    }
                )
            )

        self._transact_write_with_chunking(transact_items)

    def _delete_entities(
        self,
        entities: List[T],
        condition_expression: str = "attribute_exists(PK)",
    ) -> None:
        """Delete multiple entities using transactional writes."""
        if not entities:
            return

        # DynamoDB transact_write_items has a limit of 25 items
        chunk_size = 25

        for i in range(0, len(entities), chunk_size):
            chunk = entities[i : i + chunk_size]
            transact_items = []

            for entity in chunk:
                transact_items.append(
                    TransactWriteItemTypeDef(
                        Delete={
                            "TableName": self.table_name,
                            "Key": entity.key,
                            "ConditionExpression": condition_expression,
                        }
                    )
                )

            self._transact_write_with_chunking(transact_items)

    # ========== Query Operations ==========

    def _query_entities(
        self,
        index_name: Optional[str],
        key_condition_expression: str,
        expression_attribute_names: Optional[Dict[str, str]],
        expression_attribute_values: Dict[str, Any],
        converter_func: Callable[[Dict[str, Any]], T],
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
        filter_expression: Optional[str] = None,
    ) -> Tuple[List[T], Optional[Dict[str, Any]]]:
        """Query entities with pagination support."""
        entities = []
        current_last_key = last_evaluated_key

        while True:
            # Build query parameters
            query_params: QueryInputTypeDef = build_query_params(
                self.table_name,
                key_condition_expression,
                expression_attribute_values,
                index_name,
                expression_attribute_names,
                filter_expression,
                current_last_key,
            )

            # Set query limit
            if limit:
                remaining = limit - len(entities)
                query_params["Limit"] = min(remaining * 2, 1000)

            # Execute query
            response = self._client.query(**query_params)

            # Process items
            if "Items" in response:
                for item in response["Items"]:
                    entity = converter_func(item)
                    if entity is not None:
                        entities.append(entity)
                        if limit and len(entities) >= limit:
                            return entities[:limit], response.get(
                                "LastEvaluatedKey"
                            )

            # Check for more pages
            current_last_key = response.get("LastEvaluatedKey")
            if not current_last_key:
                break

        return entities, None

    @handle_dynamodb_errors("query_by_type")
    def _query_by_type(
        self,
        entity_type: str,
        converter_func: Callable[[Dict[str, Any]], T],
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[T], Optional[Dict[str, Any]]]:
        """Query entities by TYPE using GSITYPE index."""
        return self._query_entities(
            index_name="GSITYPE",
            key_condition_expression="GSITYPE = :type_value",
            expression_attribute_names=None,
            expression_attribute_values={":type_value": {"S": entity_type}},
            converter_func=converter_func,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("query_by_parent")
    def _query_by_parent(
        self,
        parent_key_prefix: str,
        child_key_prefix: str,
        converter_func: Callable[[Dict[str, Any]], T],
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[T], Optional[Dict[str, Any]]]:
        """Query child entities by parent ID."""
        return self._query_entities(
            index_name=None,
            key_condition_expression="PK = :pk AND begins_with(SK, :sk)",
            expression_attribute_names=None,
            expression_attribute_values={
                ":pk": {"S": parent_key_prefix},
                ":sk": {"S": child_key_prefix},
            },
            converter_func=converter_func,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )
