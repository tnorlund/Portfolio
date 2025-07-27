from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from botocore.exceptions import ClientError

from receipt_dynamo.data._base import PutRequestTypeDef, WriteRequestTypeDef
from receipt_dynamo.data.base_operations import (
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    BatchOperationsMixin,
    TransactionalOperationsMixin,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import DynamoDBError, OperationError
from receipt_dynamo.entities.label_count_cache import (
    LabelCountCache,
    item_to_label_count_cache,
)

if TYPE_CHECKING:
    from receipt_dynamo.data._base import QueryInputTypeDef


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
            raise ValueError("item cannot be None")
        if not isinstance(item, LabelCountCache):
            raise ValueError(
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
                raise ValueError(
                    f"LabelCountCache for label {item.label} already exists"
                ) from e
            raise DynamoDBError(
                f"Could not add label count cache to DynamoDB: {e}"
            ) from e

    @handle_dynamodb_errors("add_label_count_caches")
    def add_label_count_caches(self, items: list[LabelCountCache]) -> None:
        if items is None:
            raise ValueError("items cannot be None")
        if not isinstance(items, list) or not all(
            isinstance(item, LabelCountCache) for item in items
        ):
            raise ValueError(
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
                raise ValueError(
                    "LabelCountCache already exists for one or more labels"
                ) from e
            raise DynamoDBError(
                f"Could not add label count caches to DynamoDB: {e}"
            ) from e

    @handle_dynamodb_errors("update_label_count_cache")
    def update_label_count_cache(self, item: LabelCountCache) -> None:
        if item is None:
            raise ValueError("item cannot be None")
        if not isinstance(item, LabelCountCache):
            raise ValueError(
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
                raise ValueError(
                    f"LabelCountCache for label {item.label} does not exist"
                ) from e
            raise DynamoDBError(
                f"Could not update label count cache in DynamoDB: {e}"
            ) from e

    @handle_dynamodb_errors("get_label_count_cache")
    def get_label_count_cache(self, label: str) -> Optional[LabelCountCache]:
        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": "LABEL_CACHE"},
                    "SK": {"S": f"LABEL#{label}"},
                },
            )
            if "Item" not in response:
                return None
            return item_to_label_count_cache(response["Item"])
        except ClientError as e:
            raise OperationError(f"Error getting LabelCountCache: {e}f") from e

    @handle_dynamodb_errors("list_label_count_caches")
    def list_label_count_caches(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict] = None,
    ) -> Tuple[List[LabelCountCache], Optional[Dict[str, Any]]]:
        counts: list[LabelCountCache] = []
        try:
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "IndexName": "GSITYPE",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "TYPE"},
                "ExpressionAttributeValues": {
                    ":val": {"S": "LABEL_COUNT_CACHE"}
                },
                "ScanIndexForward": True,
            }
            if last_evaluated_key is not None:
                query_params["ExclusiveStartKey"] = last_evaluated_key
            if limit is not None:
                query_params["Limit"] = limit
            response = self._client.query(**query_params)
            counts.extend(
                [item_to_label_count_cache(item) for item in response["Items"]]
            )
            if limit is None:
                while "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                    response = self._client.query(**query_params)
                    counts.extend(
                        [
                            item_to_label_count_cache(item)
                            for item in response["Items"]
                        ]
                    )
                last_evaluated_key = None
            else:
                last_evaluated_key = response.get("LastEvaluatedKey", None)
            return counts, last_evaluated_key
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "ResourceNotFoundException":
                raise ValueError("LabelCountCache table does not exist") from e
            raise OperationError(f"Error listing LabelCountCaches: {e}") from e
