from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from botocore.exceptions import ClientError

from receipt_dynamo.data.base_operations import (
    BatchOperationsMixin,
    DynamoDBBaseOperations,
    PutRequestTypeDef,
    SingleEntityCRUDMixin,
    TransactionalOperationsMixin,
    WriteRequestTypeDef,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBError,
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityValidationError,
    OperationError,
)
from receipt_dynamo.entities.label_count_cache import (
    LabelCountCache,
    item_to_label_count_cache,
)

if TYPE_CHECKING:
    from receipt_dynamo.data.base_operations import QueryInputTypeDef


class _LabelCountCache(
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    BatchOperationsMixin,
    TransactionalOperationsMixin,
):
    """Accessor methods for LabelCountCache items in DynamoDB."""

    @handle_dynamodb_errors("add_label_count_cache")
    def add_label_count_cache(self, item: LabelCountCache) -> None:
        if item is None:
            raise EntityValidationError("item cannot be None")
        if not isinstance(item, LabelCountCache):
            raise EntityValidationError(
                "item must be an instance of the LabelCountCache class."
            )
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=item.to_item(),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "ConditionalCheckFailedException":
                raise EntityAlreadyExistsError(
                    f"LabelCountCache for label {item.label} already exists"
                ) from e
            raise DynamoDBError(
                f"Could not add label count cache to DynamoDB: {e}"
            ) from e

    @handle_dynamodb_errors("add_label_count_caches")
    def add_label_count_caches(self, items: list[LabelCountCache]) -> None:
        if items is None:
            raise EntityValidationError("items cannot be None")
        if not isinstance(items, list) or not all(
            isinstance(item, LabelCountCache) for item in items
        ):
            raise EntityValidationError(
                "items must be a list of LabelCountCache objects.f"
            )
        try:
            for i in range(0, len(items), 25):
                chunk = items[i : i + 25]
                request_items = [
                    WriteRequestTypeDef(
                        PutRequest=PutRequestTypeDef(Item=item.to_item())
                    )
                    for item in chunk
                ]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                # Handle unprocessed items if they exist
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    # If there are unprocessed items, retry them
                    response = self._client.batch_write_item(
                        RequestItems=unprocessed
                    )
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "ConditionalCheckFailedException":
                # Note: 'item' is not defined here, using generic message
                raise EntityAlreadyExistsError(
                    "LabelCountCache already exists for one or more labels"
                ) from e
            raise DynamoDBError(
                f"Could not add label count caches to DynamoDB: {e}"
            ) from e

    @handle_dynamodb_errors("update_label_count_cache")
    def update_label_count_cache(self, item: LabelCountCache) -> None:
        if item is None:
            raise EntityValidationError("item cannot be None")
        if not isinstance(item, LabelCountCache):
            raise EntityValidationError(
                "item must be an instance of the LabelCountCache class."
            )
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=item.to_item(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "ConditionalCheckFailedException":
                raise EntityNotFoundError(
                    f"LabelCountCache for label {item.label} does not exist"
            ) from e
            raise DynamoDBError(
                f"Could not update label count cache in DynamoDB: {e}"
            ) from e

    @handle_dynamodb_errors("get_label_count_cache")
    def get_label_count_cache(self, label: str) -> Optional[LabelCountCache]:
        return self._get_entity(
            primary_key="LABEL_CACHE",
            sort_key=f"LABEL#{label}",
            entity_class=LabelCountCache,
            converter_func=item_to_label_count_cache
        )

    @handle_dynamodb_errors("list_label_count_caches")
    def list_label_count_caches(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict] = None,
    ) -> Tuple[List[LabelCountCache], Optional[Dict[str, Any]]]:
        return self._query_entities(
            index_name="GSITYPE",
            key_condition_expression="#t = :val",
            expression_attribute_names={"#t": "TYPE"},
            expression_attribute_values={":val": {"S": "LABEL_COUNT_CACHE"}},
            converter_func=item_to_label_count_cache,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
            scan_index_forward=True
        )
