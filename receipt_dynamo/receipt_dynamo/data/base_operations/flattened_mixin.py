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

# DynamoDB BatchGetItem can handle up to 100 keys per request
BATCH_GET_CHUNK_SIZE = 100

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
        self._validate_positive_int_id(receipt_id, param_name)

    def _validate_positive_int_id(
        self, value: int, param_name: str
    ) -> None:
        """Validate that value is a positive integer.

        This is the generic validator for ID fields like line_id, word_id,
        letter_id, receipt_id, etc.

        Args:
            value: The value to validate
            param_name: Name of the parameter for error messages

        Raises:
            EntityValidationError: If value is None, not an int, or <= 0
        """
        if value is None:
            raise EntityValidationError(f"{param_name} cannot be None")
        if not isinstance(value, int) or value <= 0:
            raise EntityValidationError(
                f"{param_name} must be a positive integer"
            )

    def _validate_pagination_params(
        self,
        limit: Optional[int],
        last_evaluated_key: Optional[Dict[str, Any]],
        validate_attribute_format: bool = False,
    ) -> None:
        """Validate pagination parameters.

        Args:
            limit: Maximum number of items to return
            last_evaluated_key: Key to start from for pagination
            validate_attribute_format: If True, validates that LEK values
                have proper DynamoDB attribute format (e.g., {"S": "value"})
        """
        validate_pagination_params(
            limit, last_evaluated_key, validate_attribute_format
        )

    def _validate_job_id(self, job_id: str) -> None:
        """Validate a job ID.

        Args:
            job_id: The job ID to validate

        Raises:
            EntityValidationError: If job_id is None
            ValueError: If job_id is not a valid UUID
        """
        if job_id is None:
            raise EntityValidationError("job_id cannot be None")
        assert_valid_uuid(job_id)

    def _validate_image_receipt_indices(
        self, indices: List[Tuple[str, int]], param_name: str = "indices"
    ) -> None:
        """Validate a list of (image_id, receipt_id) tuples.

        This is the standard validation for get_*_by_indices methods that
        accept a list of (image_id, receipt_id) tuples.

        Args:
            indices: List of (image_id, receipt_id) tuples
            param_name: Name of the parameter for error messages

        Raises:
            EntityValidationError: If validation fails
        """
        if indices is None:
            raise EntityValidationError(f"{param_name} cannot be None")
        if not isinstance(indices, list):
            raise EntityValidationError(f"{param_name} must be a list")
        if not all(isinstance(index, tuple) for index in indices):
            raise EntityValidationError(
                f"{param_name} must be a list of tuples"
            )
        if not all(
            isinstance(index[0], str) and isinstance(index[1], int)
            for index in indices
        ):
            raise EntityValidationError(
                f"{param_name} must be a list of tuples of "
                "(image_id, receipt_id)"
            )
        if not all(index[1] > 0 for index in indices):
            raise EntityValidationError("receipt_id must be positive")

    def _validate_batch_receipt_keys(
        self, keys: List[Dict[str, Any]], expected_sk_suffix: str
    ) -> None:
        """Validate batch keys for receipt-related entities.

        Args:
            keys: List of DynamoDB key dictionaries with PK and SK
            expected_sk_suffix: Expected suffix of the SK (e.g., "METADATA",
                "PLACE")

        Raises:
            EntityValidationError: If keys are invalid
        """
        if keys is None:
            raise EntityValidationError("keys cannot be None")
        if not isinstance(keys, list):
            raise EntityValidationError("keys must be a list")
        if not all(isinstance(key, dict) for key in keys):
            raise EntityValidationError("keys must be a list of dictionaries")
        for key in keys:
            if not {"PK", "SK"}.issubset(key.keys()):
                raise EntityValidationError("keys must contain 'PK' and 'SK'")
            if not key["PK"]["S"].startswith("IMAGE#"):
                raise EntityValidationError("PK must start with 'IMAGE#'")
            if not key["SK"]["S"].startswith("RECEIPT#"):
                raise EntityValidationError("SK must start with 'RECEIPT#'")
            if not key["SK"]["S"].split("#")[-1] == expected_sk_suffix:
                raise EntityValidationError(
                    f"SK must end with '{expected_sk_suffix}'"
                )

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
        """Add multiple entities using transactional writes with existence
        check."""
        self._validate_entity_list(entities, entity_type, param_name)

        # Use transactional writes to ensure entities don't already exist
        transact_items = []
        for entity in entities:
            transact_items.append(
                TransactWriteItemTypeDef(
                    Put={
                        "TableName": self.table_name,
                        "Item": entity.to_item(),
                        # Guard on full composite key for uniqueness
                        "ConditionExpression": (
                            "attribute_not_exists(PK) and "
                            "attribute_not_exists(SK)"
                        ),
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

    def _batch_get_items(
        self, keys: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Execute batch get operations with chunking and retry logic.

        Args:
            keys: List of DynamoDB key dictionaries, each with PK and SK.

        Returns:
            List of raw DynamoDB items (not converted to entities).
            Callers should apply their own converter function.

        Note:
            DynamoDB BatchGetItem can handle up to 100 keys per request.
            This method handles chunking and retries for unprocessed keys.
        """
        if not keys:
            return []

        results: List[Dict[str, Any]] = []

        # Process keys in chunks of BATCH_GET_CHUNK_SIZE
        for i in range(0, len(keys), BATCH_GET_CHUNK_SIZE):
            chunk = keys[i : i + BATCH_GET_CHUNK_SIZE]

            # Perform BatchGetItem
            response = self._client.batch_get_item(
                RequestItems={self.table_name: {"Keys": chunk}}
            )

            # Collect results
            batch_items = response.get("Responses", {}).get(
                self.table_name, []
            )
            results.extend(batch_items)

            # Retry unprocessed keys
            unprocessed = response.get("UnprocessedKeys", {})
            while unprocessed.get(self.table_name, {}).get("Keys"):
                response = self._client.batch_get_item(
                    RequestItems=unprocessed
                )
                batch_items = response.get("Responses", {}).get(
                    self.table_name, []
                )
                results.extend(batch_items)
                unprocessed = response.get("UnprocessedKeys", {})

        return results

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
                        # Require existing full composite key
                        "ConditionExpression": (
                            "attribute_exists(PK) and attribute_exists(SK)"
                        ),
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

    def _build_last_evaluated_key(
        self, item: Dict[str, Any], index_name: Optional[str]
    ) -> Dict[str, Any]:
        """Build a LastEvaluatedKey from a DynamoDB item.

        Args:
            item: The raw DynamoDB item
            index_name: The GSI name if querying an index, None for main table

        Returns:
            A LastEvaluatedKey dict suitable for pagination
        """
        # Always include the table's primary key
        key: Dict[str, Any] = {
            "PK": item["PK"],
            "SK": item["SK"],
        }

        # Add GSI key attributes if querying an index
        if index_name:
            # Special case: GSITYPE index uses "TYPE" as partition key
            if index_name == "GSITYPE" and "TYPE" in item:
                key["TYPE"] = item["TYPE"]
            else:
                # Standard pattern: GSI{N}PK and GSI{N}SK
                pk_attr = f"{index_name}PK"
                sk_attr = f"{index_name}SK"
                if pk_attr in item:
                    key[pk_attr] = item[pk_attr]
                if sk_attr in item:
                    key[sk_attr] = item[sk_attr]

        return key

    # pylint: disable=too-many-positional-arguments
    def _query_entities(
        self,
        index_name: Optional[str],
        key_condition_expression: str,
        expression_attribute_names: Optional[Dict[str, str]],
        expression_attribute_values: Dict[str, Any],
        converter_func: Callable[[Dict[str, Any]], T],
        *,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
        filter_expression: Optional[str] = None,
        scan_index_forward: Optional[bool] = None,
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
                index_name=index_name,
                expression_attribute_names=expression_attribute_names,
                filter_expression=filter_expression,
                exclusive_start_key=current_last_key,
                limit=None,  # Will be set separately below
                scan_index_forward=scan_index_forward,
            )

            # Set query limit
            if limit:
                remaining = limit - len(entities)
                query_params["Limit"] = min(remaining * 2, 1000)

            # Execute query
            response = self._client.query(**query_params)

            # Process items
            items = response.get("Items", [])
            last_processed_item = None
            items_processed_count = 0

            for item in items:
                entity = converter_func(item)
                if entity is not None:
                    entities.append(entity)
                    last_processed_item = item
                    items_processed_count += 1

                    if limit and len(entities) >= limit:
                        # Check if there are more items available
                        has_more_in_response = items_processed_count < len(
                            items
                        )
                        has_more_pages = (
                            response.get("LastEvaluatedKey") is not None
                        )

                        if has_more_in_response or has_more_pages:
                            # Construct key from last processed item
                            next_key = self._build_last_evaluated_key(
                                last_processed_item, index_name
                            )
                            return entities[:limit], next_key
                        # No more items anywhere
                        return entities[:limit], None

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
        """Query entities by TYPE using GSITYPE index.

        Note: The index is named GSITYPE but uses TYPE as its partition key.
        """
        return self._query_entities(
            index_name="GSITYPE",
            key_condition_expression="#type_attr = :type_value",
            expression_attribute_names={"#type_attr": "TYPE"},
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
        *,
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

    def _query_entities_by_receipt_gsi3(
        self,
        image_id: str,
        receipt_id: int,
        entity_type: str,
        converter_func: Callable[[Dict[str, Any]], T],
    ) -> List[T]:
        """Query entities by image_id and receipt_id using GSI3.

        This is a common pattern for listing receipt-level entities
        (lines, words) that belong to a specific receipt.

        Args:
            image_id: The image ID (UUID)
            receipt_id: The receipt ID (positive integer)
            entity_type: The entity type for GSI3SK (e.g., "LINE", "WORD")
            converter_func: Function to convert DynamoDB items to entities

        Returns:
            List of entities matching the query
        """
        self._validate_image_id(image_id)
        self._validate_positive_int_id(receipt_id, "receipt_id")

        results, _ = self._query_entities(
            index_name="GSI3",
            key_condition_expression="GSI3PK = :pk AND GSI3SK = :sk",
            expression_attribute_names=None,
            expression_attribute_values={
                ":pk": {"S": f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}"},
                ":sk": {"S": entity_type},
            },
            converter_func=converter_func,
        )
        return results

    def _query_by_image_receipt_sk_prefix(
        self,
        image_id: str,
        receipt_id: int,
        sk_suffix: str,
        converter_func: Callable[[Dict[str, Any]], T],
        *,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[T], Optional[Dict[str, Any]]]:
        """Query entities by image_id with SK prefix on main table.

        This is a common pattern for listing receipt analysis entities
        (validation categories, validation results) that use a SK prefix
        pattern starting with RECEIPT#{receipt_id}#ANALYSIS#...

        Args:
            image_id: The image ID (UUID)
            receipt_id: The receipt ID (positive integer)
            sk_suffix: The suffix after RECEIPT#{receipt_id}# (e.g.,
                "ANALYSIS#VALIDATION#CATEGORY#")
            converter_func: Function to convert DynamoDB items to entities
            limit: Maximum number of items to return
            last_evaluated_key: Key for pagination

        Returns:
            Tuple of (list of entities, last_evaluated_key for pagination)

        Raises:
            EntityValidationError: If image_id format is invalid
        """
        try:
            self._validate_image_id(image_id)
        except ValueError as e:
            raise EntityValidationError(f"Invalid image_id format: {e}") from e

        return self._query_entities(
            index_name=None,
            key_condition_expression=(
                "#pk = :pk AND begins_with(#sk, :sk_prefix)"
            ),
            expression_attribute_names={
                "#pk": "PK",
                "#sk": "SK",
            },
            expression_attribute_values={
                ":pk": {"S": f"IMAGE#{image_id}"},
                ":sk_prefix": {"S": f"RECEIPT#{receipt_id:05d}#{sk_suffix}"},
            },
            converter_func=converter_func,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    def _query_by_image_sk_prefix(
        self,
        image_id: str,
        sk_prefix: str,
        converter_func: Callable[[Dict[str, Any]], T],
    ) -> List[T]:
        """Query entities by image_id with SK prefix on main table.

        This helper returns results without pagination.

        This is a common pattern for listing entities that belong to a
        specific image using a SK prefix pattern.

        Args:
            image_id: The image ID (UUID)
            sk_prefix: The full SK prefix to match
            converter_func: Function to convert DynamoDB items to entities

        Returns:
            List of entities matching the query
        """
        self._validate_image_id(image_id)

        results, _ = self._query_entities(
            index_name=None,
            key_condition_expression=(
                "PK = :pkVal AND begins_with(SK, :skPrefix)"
            ),
            expression_attribute_names=None,
            expression_attribute_values={
                ":pkVal": {"S": f"IMAGE#{image_id}"},
                ":skPrefix": {"S": sk_prefix},
            },
            converter_func=converter_func,
        )
        return results

    def _query_by_job_sk_prefix(
        self,
        job_id: str,
        sk_prefix: str,
        converter_func: Callable[[Dict[str, Any]], T],
        *,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
        scan_index_forward: Optional[bool] = None,
    ) -> Tuple[List[T], Optional[Dict[str, Any]]]:
        """Query entities by job_id with SK prefix on main table.

        This is a common pattern for listing job-related entities
        (checkpoints, resources) that belong to a specific job.

        Args:
            job_id: The job ID (UUID)
            sk_prefix: The SK prefix to match
                (e.g., "CHECKPOINT#", "RESOURCE#")
            converter_func: Function to convert DynamoDB items to entities
            limit: Maximum number of items to return
            last_evaluated_key: Key for pagination
            scan_index_forward: Sort order (True=ascending, False=descending)

        Returns:
            Tuple of (list of entities, last_evaluated_key for pagination)
        """
        self._validate_job_id(job_id)
        self._validate_pagination_params(
            limit, last_evaluated_key, validate_attribute_format=True
        )

        return self._query_entities(
            index_name=None,
            key_condition_expression="PK = :pk AND begins_with(SK, :sk)",
            expression_attribute_names=None,
            expression_attribute_values={
                ":pk": {"S": f"JOB#{job_id}"},
                ":sk": {"S": sk_prefix},
            },
            converter_func=converter_func,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
            scan_index_forward=scan_index_forward,
        )
