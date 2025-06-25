from typing import Dict, List, Optional

from botocore.exceptions import ClientError

from receipt_dynamo.data._base import DynamoClientProtocol
from receipt_dynamo.entities.label_count_cache import (LabelCountCache,
                                                       itemToLabelCountCache)


class _LabelCountCache(DynamoClientProtocol):
    """Accessor methods for LabelCountCache items in DynamoDB."""

    def addLabelCountCache(self, item: LabelCountCache) -> None:
        if item is None:
            raise ValueError("item parameter is required and cannot be None.")
        if not isinstance(item, LabelCountCache):
            raise ValueError("item must be an instance of the LabelCountCache class.")
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
            else:
                raise Exception(
                    f"Could not add label count cache to DynamoDB: {e}"
                ) from e

    def addLabelCountCaches(self, items: list[LabelCountCache]) -> None:
        if items is None:
            raise ValueError("items parameter is required and cannot be None.")
        if not isinstance(items, list) or not all(
            isinstance(item, LabelCountCache) for item in items
        ):
            raise ValueError("items must be a list of LabelCountCache objects.")
        try:
            for i in range(0, len(items), 25):
                chunk = items[i : i + 25]
                request_items = [
                    {"PutRequest": {"Item": item.to_item()}} for item in chunk
                ]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                # Handle unprocessed items if they exist
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    # If there are unprocessed items, retry them
                    response = self._client.batch_write_item(RequestItems=unprocessed)
        except ClientError:
            raise ValueError("Could not add label count caches to the database")

            self._client.batch_write_item(
                RequestItems={
                    self.table_name: [
                        {"PutRequest": {"Item": item.to_item()}} for item in items
                    ],
                },
            )
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"LabelCountCache for label {item.label} already exists"
                ) from e
            else:
                raise Exception(
                    f"Could not add label count caches to DynamoDB: {e}"
                ) from e

    def updateLabelCountCache(self, item: LabelCountCache) -> None:
        if item is None:
            raise ValueError("item parameter is required and cannot be None.")
        if not isinstance(item, LabelCountCache):
            raise ValueError("item must be an instance of the LabelCountCache class.")
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
            else:
                raise Exception(
                    f"Could not update label count cache in DynamoDB: {e}"
                ) from e

    def getLabelCountCache(self, label: str) -> Optional[LabelCountCache]:
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
            return itemToLabelCountCache(response["Item"])
        except ClientError as e:
            raise Exception(f"Error getting LabelCountCache: {e}")

    def listLabelCountCaches(
        self,
        limit: Optional[int] = None,
        lastEvaluatedKey: Optional[Dict] = None,
    ) -> List[LabelCountCache]:
        counts: list[LabelCountCache] = []
        try:
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSITYPE",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "TYPE"},
                "ExpressionAttributeValues": {":val": {"S": "LABEL_COUNT_CACHE"}},
                "ScanIndexForward": True,
            }
            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey
            if limit is not None:
                query_params["Limit"] = limit
            response = self._client.query(**query_params)
            counts.extend([itemToLabelCountCache(item) for item in response["Items"]])
            if limit is None:
                while "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response["LastEvaluatedKey"]
                    response = self._client.query(**query_params)
                    counts.extend(
                        [itemToLabelCountCache(item) for item in response["Items"]]
                    )
                last_evaluated_key = None
            else:
                last_evaluated_key = response.get("LastEvaluatedKey", None)
            return counts, last_evaluated_key
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "ResourceNotFoundException":
                raise ValueError("LabelCountCache table does not exist") from e
            else:
                raise Exception(f"Error listing LabelCountCaches: {e}") from e
