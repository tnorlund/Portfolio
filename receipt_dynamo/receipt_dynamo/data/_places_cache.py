from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from botocore.exceptions import ClientError

from receipt_dynamo.data.base_operations import (
    DeleteRequestTypeDef,
    DeleteTypeDef,
    DynamoDBBaseOperations,
    FlattenedStandardMixin,
    TransactWriteItemTypeDef,
    WriteRequestTypeDef,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    EntityValidationError,
    OperationError,
)
from receipt_dynamo.entities.places_cache import (
    PlacesCache,
    item_to_places_cache,
)

if TYPE_CHECKING:
    from receipt_dynamo.data.base_operations import QueryInputTypeDef

# DynamoDB batch_write_item can handle up to 25 items per call
CHUNK_SIZE = 25


class _PlacesCache(
    FlattenedStandardMixin,
):
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

    @handle_dynamodb_errors("add_places_cache")
    def add_places_cache(self, item: PlacesCache):
        """
        Adds a PlacesCache to the database with a conditional check that it
        does not already exist.

        Args:
            item (PlacesCache): The PlacesCache object to add.

        Raises:
            EntityAlreadyExistsError: If a PlacesCache with the same PK/SK
                already exists
            EntityValidationError: If item parameters are invalid
        """
        if item is None:
            raise EntityValidationError("item cannot be None")
        if not isinstance(item, PlacesCache):
            raise EntityValidationError("item must be an instance of PlacesCache")
        self._add_entity(item, condition_expression="attribute_not_exists(PK)")

    @handle_dynamodb_errors("put_places_cache")
    def put_places_cache(self, item: PlacesCache):
        """
        Puts a PlacesCache item unconditionally, allowing overwrites.

        This method allows refreshing expired or stale cache entries without
        waiting for DynamoDB TTL cleanup. Use this instead of add_places_cache()
        when you want to update an existing entry or when the TTL may have
        expired.

        Args:
            item (PlacesCache): The PlacesCache object to put.

        Raises:
            EntityValidationError: If item parameters are invalid
        """
        if item is None:
            raise EntityValidationError("item cannot be None")
        if not isinstance(item, PlacesCache):
            raise EntityValidationError("item must be an instance of PlacesCache")
        self._execute_put_item(item, condition_expression=None)

    @handle_dynamodb_errors("update_places_cache")
    def update_places_cache(self, item: PlacesCache):
        """
        Updates an existing PlacesCache in the database.

        Args:
            item (PlacesCache): The PlacesCache object to update.

        Raises:
            EntityNotFoundError: If the item does not exist in the table
            EntityValidationError: If item parameters are invalid
        """
        if item is None:
            raise EntityValidationError("item cannot be None")
        if not isinstance(item, PlacesCache):
            raise EntityValidationError("item must be an instance of PlacesCache")
        self._update_entity(item, condition_expression="attribute_exists(PK)")

    @handle_dynamodb_errors("update_places_caches")
    def update_places_caches(self, caches: List[PlacesCache]):
        """
        Updates a list of PlacesCache items in the database.
        """
        if caches is None:
            raise EntityValidationError("caches cannot be None")
        if not isinstance(caches, list):
            raise EntityValidationError("caches must be a list")
        for cache in caches:
            if not isinstance(cache, PlacesCache):
                raise EntityValidationError(
                    "caches must be a list of PlacesCache objects"
                )
        self._update_entities(caches, PlacesCache, "caches")

    @handle_dynamodb_errors("increment_query_count")
    def increment_query_count(self, item: PlacesCache) -> PlacesCache:
        """
        Increments the query count for a PlacesCache item and updates its
        last_updated timestamp.
        If the item doesn't exist, it will be created with a query count of 1.

        Args:
            item (PlacesCache): The PlacesCache object to update.

        Returns:
            PlacesCache: The updated PlacesCache object.

        Raises:
            Exception: If there's an error updating the item.
        """
        if item is None:
            raise EntityValidationError("item cannot be None")
        if not isinstance(item, PlacesCache):
            raise EntityValidationError(
                "item must be an instance of the PlacesCache class."
            )

        try:
            # Update the item's attributes
            response = self._client.update_item(
                TableName=self.table_name,
                Key=item.key,
                UpdateExpression=(
                    "SET query_count = if_not_exists(query_count, :zero) "
                    "+ :inc, last_updated = :now"
                ),
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

    @handle_dynamodb_errors("delete_places_cache")
    def delete_places_cache(self, item: PlacesCache):
        """
        Deletes a single PlacesCache from the database.

        Args:
            item (PlacesCache): The PlacesCache object to delete.

        Raises:
            EntityNotFoundError: If the item does not exist
            EntityValidationError: If item parameters are invalid
        """
        self._validate_entity(item, PlacesCache, "item")
        self._delete_entity(item, condition_expression="attribute_exists(PK)")

    @handle_dynamodb_errors("delete_places_caches")
    def delete_places_caches(self, places_cache_items: List[PlacesCache]):
        """
        Deletes a list of PlacesCache items from the database.
        """
        self._validate_entity_list(
            places_cache_items, PlacesCache, "places_cache_items"
        )

        self._delete_entities(places_cache_items)

    @handle_dynamodb_errors("get_places_cache")
    def get_places_cache(
        self, search_type: str, search_value: str
    ) -> Optional[PlacesCache]:
        """
        Retrieves a single PlacesCache from DynamoDB by its primary key.

        Args:
            search_type (str): The type of search (ADDRESS, PHONE, URL).
            search_value (str): The search value.

        Returns:
            Optional[PlacesCache]: The PlacesCache object if found, None
                otherwise.
        """
        temp_cache = PlacesCache(
            search_type=search_type,  # type: ignore[arg-type]
            search_value=search_value,
            place_id="temp",  # Placeholder
            places_response={},  # Placeholder
            last_updated="2021-01-01T00:00:00",  # Placeholder
        )

        return self._get_entity(
            primary_key=temp_cache.key["PK"]["S"],
            sort_key=temp_cache.key["SK"]["S"],
            entity_class=PlacesCache,
            converter_func=item_to_places_cache,
        )

    @handle_dynamodb_errors("get_places_cache_by_place_id")
    def get_places_cache_by_place_id(self, place_id: str) -> Optional[PlacesCache]:
        """
        Retrieves a PlacesCache by its place_id using GSI1.

        Args:
            place_id (str): The Google Places place_id.

        Returns:
            Optional[PlacesCache]: The PlacesCache object if found, None
                otherwise.
        """
        results, _ = self._query_entities(
            index_name="GSI1",
            key_condition_expression="#gsi1pk = :gsi1pk AND #gsi1sk = :gsi1sk",
            expression_attribute_names={
                "#gsi1pk": "GSI1PK",
                "#gsi1sk": "GSI1SK",
            },
            expression_attribute_values={
                ":gsi1pk": {"S": "PLACE_ID"},
                ":gsi1sk": {"S": f"PLACE_ID#{place_id}"},
            },
            converter_func=item_to_places_cache,
            limit=1,
        )
        return results[0] if results else None

    @handle_dynamodb_errors("list_places_caches")
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
            last_evaluated_key (Optional[Dict]): Key to continue from a
                previous query.

        Returns:
            Tuple[List[PlacesCache], Optional[Dict]]: List of items and last
                evaluated key.
        """
        if limit is not None and not isinstance(limit, int):
            raise EntityValidationError("limit must be an integer or None.")
        if last_evaluated_key is not None and not isinstance(last_evaluated_key, dict):
            raise EntityValidationError(
                "last_evaluated_key must be a dictionary or None."
            )

        return self._query_by_type(
            entity_type="PLACES_CACHE",
            converter_func=item_to_places_cache,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("invalidate_old_cache_items")
    def invalidate_old_cache_items(self, days_old: int):
        """
        Deletes cache items that are older than the specified number of days.

        Args:
            days_old (int): Number of days after which items should be
                considered old.
        """
        cutoff_date = (
            datetime.now(timezone.utc) - timedelta(days=days_old)
        ).isoformat()

        # Query using GSI2 (LAST_USED index)
        items, _ = self._query_entities(
            index_name="GSI2",
            key_condition_expression="GSI2_PK = :pk AND GSI2_SK < :cutoff",
            expression_attribute_names=None,
            expression_attribute_values={
                ":pk": {"S": "LAST_USED"},
                ":cutoff": {"S": cutoff_date},
            },
            converter_func=item_to_places_cache,
            limit=None,
            last_evaluated_key=None,
        )

        # Delete items in batches
        if items:
            request_items = [
                WriteRequestTypeDef(
                    DeleteRequest=DeleteRequestTypeDef(
                        Key={"PK": item.key["PK"], "SK": item.key["SK"]}
                    )
                )
                for item in items
            ]
            self._batch_write_with_retry(request_items)
