from datetime import datetime, timezone
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from botocore.exceptions import ClientError

from receipt_dynamo.data._base import DynamoClientProtocol

if TYPE_CHECKING:
    from receipt_dynamo.data._base import (
        DeleteRequestTypeDef,
        DeleteTypeDef,
        QueryInputTypeDef,
        TransactWriteItemTypeDef,
        WriteRequestTypeDef,
    )

# These are used at runtime, not just for type checking
from receipt_dynamo.data._base import (
    DeleteRequestTypeDef,
    DeleteTypeDef,
    PutRequestTypeDef,
    PutTypeDef,
    TransactWriteItemTypeDef,
    WriteRequestTypeDef,
)
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBAccessError,
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    DynamoDBValidationError,
    OperationError,
)
from receipt_dynamo.entities.places_cache import (
    PlacesCache,
    item_to_places_cache,
)

# DynamoDB batch_write_item can handle up to 25 items per call
CHUNK_SIZE = 25


class _PlacesCache(DynamoClientProtocol):
    """
    Provides methods for accessing PlacesCache items in DynamoDB.

    Table schema for PlacesCache:
      PK = "PLACES#<search_type>"
      SK = "VALUE#<padded_search_value>"
      TYPE = "PLACES_CACHE"
      GSI1_PK = "PLACE_ID"
      GSI1_SK = "PLACE_ID#<place_id>"
      GSI2_PK = "LAST_USED"
      GSI2_SK = "<timestamp>"
    """

    def add_places_cache(self, item: PlacesCache):
        """
        Adds a PlacesCache to the database with a conditional check that it does not already exist.

        Args:
            item (PlacesCache): The PlacesCache object to add.

        Raises:
            ValueError: If a PlacesCache with the same PK/SK already exists or if invalid parameters.
        """
        if item is None:
            raise ValueError("item parameter is required and cannot be None.")
        if not isinstance(item, PlacesCache):
            raise ValueError(
                "item must be an instance of the PlacesCache class."
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
                    f"PlacesCache for search_type={item.search_type}, "
                    f"search_value={item.search_value} already exists."
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    "Provisioned throughput exceeded"
                ) from e
            elif error_code == "ValidationException":
                raise DynamoDBValidationError(
                    "One or more parameters given were invalid"
                ) from e
            elif error_code == "AccessDeniedException":
                raise DynamoDBAccessError("Access denied") from e
            else:
                raise DynamoDBError(
                    "Could not add places cache item to DynamoDB"
                ) from e

    def update_places_cache(self, item: PlacesCache):
        """
        Updates an existing PlacesCache in the database.

        Args:
            item (PlacesCache): The PlacesCache object to update.

        Raises:
            ValueError: If the item does not exist in the table.
        """
        if item is None:
            raise ValueError("item parameter is required and cannot be None.")
        if not isinstance(item, PlacesCache):
            raise ValueError(
                "item must be an instance of the PlacesCache class."
            )

        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=item.to_item(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            if (
                e.response["Error"]["Code"]
                == "ConditionalCheckFailedException"
            ):
                raise ValueError(
                    f"PlacesCache for search_type={item.search_type}, "
                    f"search_value={item.search_value} does not exist."
                ) from e
            else:
                raise OperationError(f"Error updating PlacesCache: {e}") from e

    def increment_query_count(self, item: PlacesCache) -> PlacesCache:
        """
        Increments the query count for a PlacesCache item and updates its last_updated timestamp.
        If the item doesn't exist, it will be created with a query count of 1.

        Args:
            item (PlacesCache): The PlacesCache object to update.

        Returns:
            PlacesCache: The updated PlacesCache object.

        Raises:
            Exception: If there's an error updating the item.
        """
        if item is None:
            raise ValueError("item parameter is required and cannot be None.")
        if not isinstance(item, PlacesCache):
            raise ValueError(
                "item must be an instance of the PlacesCache class."
            )

        try:
            # Update the item's attributes
            response = self._client.update_item(
                TableName=self.table_name,
                Key=item.key,
                UpdateExpression="SET query_count = if_not_exists(query_count, :zero) + :inc, last_updated = :now",
                ExpressionAttributeValues={
                    ":inc": {"N": "1"},
                    ":zero": {"N": "0"},
                    ":now": {"S": datetime.now().isoformat()},
                },
                ReturnValues="ALL_NEW",
            )

            # Convert the response back to a PlacesCache object
            if "Attributes" in response:
                return item_to_places_cache(response["Attributes"])
            return item

        except ClientError as e:
            raise OperationError(f"Error incrementing query count: {e}") from e

    def delete_places_cache(self, item: PlacesCache):
        """
        Deletes a single PlacesCache from the database.

        Args:
            item (PlacesCache): The PlacesCache object to delete.

        Raises:
            ValueError: If the item does not exist.
        """
        if item is None:
            raise ValueError("item parameter is required and cannot be None.")
        if not isinstance(item, PlacesCache):
            raise ValueError(
                "item must be an instance of the PlacesCache class."
            )

        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key=item.key,
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            if (
                e.response["Error"]["Code"]
                == "ConditionalCheckFailedException"
            ):
                raise ValueError(
                    f"PlacesCache with search_type={item.search_type}, "
                    f"search_value={item.search_value} does not exist"
                ) from e
            else:
                raise OperationError(f"Error deleting PlacesCache: {e}") from e

    def delete_places_caches(self, places_cache_items: List[PlacesCache]):
        """
        Deletes a list of PlacesCache items from the database.
        """
        if places_cache_items is None:
            raise ValueError(
                "places_cache_items parameter is required and cannot be None."
            )
        if not isinstance(places_cache_items, list):
            raise ValueError("places_cache_items must be a list.")
        if not all(
            isinstance(item, PlacesCache) for item in places_cache_items
        ):
            raise ValueError(
                "All items in places_cache_items must be PlacesCache objects."
            )

        for i in range(0, len(places_cache_items), CHUNK_SIZE):
            chunk = places_cache_items[i : i + CHUNK_SIZE]
            transact_items = [
                TransactWriteItemTypeDef(
                    Delete=DeleteTypeDef(
                        TableName=self.table_name,
                        Key=item.key,
                        # ConditionExpression="attribute_exists(PK)",
                    )
                )
                for item in chunk
            ]
            # Deduplicate transact_items by PK and SK values
            seen_keys = set()
            deduped_items = []
            for tx in transact_items:
                # Type ignore needed because mypy has trouble with deeply nested TypedDicts
                key = tx["Delete"]["Key"]  # type: ignore[index]
                pk = key["PK"]["S"]  # type: ignore[index,call-overload]
                sk = key["SK"]["S"]  # type: ignore[index,call-overload]
                if (pk, sk) not in seen_keys:
                    seen_keys.add((pk, sk))
                    deduped_items.append(tx)
            transact_items = deduped_items

            try:
                self._client.transact_write_items(TransactItems=transact_items)
            except ClientError as e:
                error_code = e.response["Error"]["Code"]
                if error_code == "ConditionalCheckFailedException":
                    raise ValueError(
                        "places_cache_items contains invalid attributes or values"
                    ) from e
                elif error_code == "ValidationException":
                    raise ValueError(
                        "places_cache_items contains invalid attributes or values"
                    ) from e
                elif error_code == "InternalServerError":
                    raise ValueError("internal server error") from e
                elif error_code == "ProvisionedThroughputExceededException":
                    raise ValueError("provisioned throughput exceeded") from e
                elif error_code == "ResourceNotFoundException":
                    raise ValueError("table not found") from e
                else:
                    raise ValueError(
                        f"Error deleting places caches: {e}"
                    ) from e

    def get_places_cache(
        self, search_type: str, search_value: str
    ) -> Optional[PlacesCache]:
        """
        Retrieves a single PlacesCache from DynamoDB by its primary key.

        Args:
            search_type (str): The type of search (ADDRESS, PHONE, URL).
            search_value (str): The search value.

        Returns:
            Optional[PlacesCache]: The PlacesCache object if found, None otherwise.
        """
        temp_cache = PlacesCache(
            search_type=search_type,  # type: ignore[arg-type]
            search_value=search_value,
            place_id="temp",  # Placeholder
            places_response={},  # Placeholder
            last_updated="2021-01-01T00:00:00",  # Placeholder
        )

        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key=temp_cache.key,
            )
            if "Item" not in response:
                return None
            return item_to_places_cache(response["Item"])
        except ClientError as e:
            raise OperationError(f"Error getting PlacesCache: {e}") from e

    def get_places_cache_by_place_id(
        self, place_id: str
    ) -> Optional[PlacesCache]:
        """
        Retrieves a PlacesCache by its place_id using GSI1.

        Args:
            place_id (str): The Google Places place_id.

        Returns:
            Optional[PlacesCache]: The PlacesCache object if found, None otherwise.
        """
        try:
            response = self._client.query(
                TableName=self.table_name,
                IndexName="GSI1",
                KeyConditionExpression="#gsi1pk = :gsi1pk AND #gsi1sk = :gsi1sk",
                ExpressionAttributeNames={
                    "#gsi1pk": "GSI1PK",
                    "#gsi1sk": "GSI1SK",
                },
                ExpressionAttributeValues={
                    ":gsi1pk": {"S": "PLACE_ID"},
                    ":gsi1sk": {"S": f"PLACE_ID#{place_id}"},
                },
            )
            if not response["Items"]:
                return None
            return item_to_places_cache(response["Items"][0])
        except ClientError as e:
            raise OperationError(
                f"Error getting PlacesCache by place_id: {e}"
            ) from e

    def list_places_caches(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict] = None,
    ) -> Tuple[List[PlacesCache], Optional[Dict]]:
        """
        Lists PlacesCache items from the database using GSI2 (LAST_USED index).
        Supports optional pagination via a limit and a LastEvaluatedKey.

        Args:
            limit (Optional[int]): Maximum number of items to return.
            last_evaluated_key (Optional[Dict]): Key to continue from a previous query.

        Returns:
            Tuple[List[PlacesCache], Optional[Dict]]: List of items and last evaluated key.
        """
        if limit is not None and not isinstance(limit, int):
            raise ValueError("limit must be an integer or None.")
        if last_evaluated_key is not None and not isinstance(
            last_evaluated_key, dict
        ):
            raise ValueError(
                "last_evaluated_key must be a dictionary or None."
            )

        places_caches = []
        try:
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "IndexName": "GSITYPE",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "TYPE"},
                "ExpressionAttributeValues": {":val": {"S": "PLACES_CACHE"}},
            }
            if last_evaluated_key is not None:
                query_params["ExclusiveStartKey"] = last_evaluated_key
            if limit is not None:
                query_params["Limit"] = limit

            response = self._client.query(**query_params)
            places_caches.extend(
                [item_to_places_cache(item) for item in response["Items"]]
            )

            if limit is None:
                # Paginate through all the places caches
                while "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                    response = self._client.query(**query_params)
                    places_caches.extend(
                        [
                            item_to_places_cache(item)
                            for item in response["Items"]
                        ]
                    )
                last_evaluated_key = None
            else:
                last_evaluated_key = response.get("LastEvaluatedKey", None)

            return places_caches, last_evaluated_key

        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    f"Could not list places caches from DynamoDB: {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                ) from e
            elif error_code == "ValidationException":
                raise ValueError(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}") from e
            else:
                raise OperationError(
                    f"Error listing places caches: {e}"
                ) from e

    def invalidate_old_cache_items(self, days_old: int):
        """
        Deletes cache items that are older than the specified number of days.

        Args:
            days_old (int): Number of days after which items should be considered old.
        """
        from datetime import datetime, timedelta, timezone

        cutoff_date = (
            datetime.now(timezone.utc) - timedelta(days=days_old)
        ).isoformat()

        try:
            # Query using GSI2 (LAST_USED index)
            response = self._client.query(
                TableName=self.table_name,
                IndexName="GSI2",
                KeyConditionExpression="GSI2_PK = :pk AND GSI2_SK < :cutoff",
                ExpressionAttributeValues={
                    ":pk": {"S": "LAST_USED"},
                    ":cutoff": {"S": cutoff_date},
                },
            )

            # Delete items in batches
            items_to_delete = response["Items"]
            while items_to_delete:
                batch = items_to_delete[:CHUNK_SIZE]
                items_to_delete = items_to_delete[CHUNK_SIZE:]

                request_items = [
                    WriteRequestTypeDef(
                        DeleteRequest=DeleteRequestTypeDef(
                            Key={"PK": item["PK"], "SK": item["SK"]}
                        )
                    )
                    for item in batch
                ]

                self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )

        except ClientError as e:
            raise OperationError(
                f"Error invalidating old cache items: {e}"
            ) from e
