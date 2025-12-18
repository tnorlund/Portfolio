"""
Data operations for ReceiptPlace entities in DynamoDB.

ReceiptPlace is the enhanced replacement for ReceiptMetadata that includes
geographic coordinates, business hours, ratings, and other rich location data
from Google Places API v1.

This module provides CRUD operations and GSI queries for managing ReceiptPlace
records in DynamoDB.
"""

from typing import TYPE_CHECKING, List, Optional, Tuple

from botocore.exceptions import ClientError

from receipt_dynamo.data.base_operations import (
    DeleteTypeDef,
    DynamoDBBaseOperations,
    FlattenedStandardMixin,
    PutRequestTypeDef,
    PutTypeDef,
    QueryInputTypeDef,
    TransactWriteItemTypeDef,
    WriteRequestTypeDef,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    EntityValidationError,
)
from receipt_dynamo.entities import ReceiptPlace

if TYPE_CHECKING:
    pass

# DynamoDB batch_write_item can only handle up to 25 items per call
CHUNK_SIZE = 25


class _ReceiptPlace(FlattenedStandardMixin):
    """
    Data operations for ReceiptPlace entities in DynamoDB.

    ReceiptPlace captures comprehensive merchant place information including
    geographic coordinates, business hours, ratings, and other data from
    Google Places API v1.

    This class provides methods to interact with ReceiptPlace entities,
    supporting:
    - CRUD operations (add, update, delete, get)
    - GSI queries by merchant name, place_id, validation status, and geohash
    - Spatial queries for nearby place detection
    - Batch operations for efficiency

    Attributes
    ----------
    _client : boto3.client
        The Boto3 DynamoDB client (must be set externally).
    table_name : str
        The name of the DynamoDB table (must be set externally).

    Methods
    -------
    add_receipt_place(receipt_place: ReceiptPlace):
        Adds a single ReceiptPlace item to the database.
    add_receipt_places(receipt_places: List[ReceiptPlace]):
        Adds multiple ReceiptPlace items in batches.
    update_receipt_place(receipt_place: ReceiptPlace):
        Updates an existing ReceiptPlace item.
    update_receipt_places(receipt_places: List[ReceiptPlace]):
        Updates multiple ReceiptPlace items using transactions.
    delete_receipt_place(receipt_place: ReceiptPlace):
        Deletes a single ReceiptPlace item.
    delete_receipt_places(receipt_places: List[ReceiptPlace]):
        Deletes multiple ReceiptPlace items using transactions.
    get_receipt_place(image_id: str, receipt_id: int) -> ReceiptPlace:
        Retrieves a single ReceiptPlace item by indices.
    get_receipt_places_by_indices(...) -> List[ReceiptPlace]:
        Retrieves multiple ReceiptPlace items by indices.
    get_receipt_places(...) -> List[ReceiptPlace]:
        Retrieves multiple ReceiptPlace items using batch get.
    list_receipt_places(...) -> Tuple[List[ReceiptPlace], dict | None]:
        Lists ReceiptPlace records with pagination.
    get_receipt_places_by_merchant(...) -> Tuple[List[ReceiptPlace], dict | None]:
        Retrieves ReceiptPlace records by merchant name (GSI1).
    list_receipt_places_with_place_id(...) -> Tuple[List[ReceiptPlace], dict | None]:
        Retrieves ReceiptPlace records by place_id (GSI2).
    get_receipt_places_by_status(...) -> Tuple[List[ReceiptPlace], dict | None]:
        Retrieves ReceiptPlace records by validation status (GSI3).
    get_receipt_places_by_geohash(...) -> Tuple[List[ReceiptPlace], dict | None]:
        Retrieves ReceiptPlace records by geohash for spatial queries (GSI4).
    """

    @handle_dynamodb_errors("add_receipt_place")
    def add_receipt_place(self, receipt_place: ReceiptPlace) -> None:
        """
        Adds a single ReceiptPlace record to DynamoDB.

        Parameters
        ----------
        receipt_place : ReceiptPlace
            The ReceiptPlace instance to add.

        Raises
        ------
        ValueError
            If receipt_place is None, not a ReceiptPlace, or if the
            record already exists.
        """
        self._validate_entity(receipt_place, ReceiptPlace, "receipt_place")
        self._add_entity(
            receipt_place,
            condition_expression=(
                "attribute_not_exists(PK) and attribute_not_exists(SK)"
            ),
        )

    @handle_dynamodb_errors("add_receipt_places")
    def add_receipt_places(self, receipt_places: List[ReceiptPlace]) -> None:
        """
        Adds multiple ReceiptPlace records to DynamoDB in batches.

        Parameters
        ----------
        receipt_places : List[ReceiptPlace]
            A list of ReceiptPlace instances to add.

        Raises
        ------
        ValueError
            If receipt_places is invalid or if an error occurs during batch
            write.
        """
        self._validate_entity_list(receipt_places, ReceiptPlace, "receipt_places")

        request_items = [
            WriteRequestTypeDef(PutRequest=PutRequestTypeDef(Item=item.to_item()))
            for item in receipt_places
        ]
        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("update_receipt_place")
    def update_receipt_place(self, receipt_place: ReceiptPlace) -> None:
        """
        Updates an existing ReceiptPlace record in DynamoDB.

        Parameters
        ----------
        receipt_place : ReceiptPlace
            The ReceiptPlace instance to update.

        Raises
        ------
        ValueError
            If receipt_place is invalid or if the record does not exist.
        """
        self._validate_entity(receipt_place, ReceiptPlace, "receipt_place")
        self._update_entity(
            receipt_place,
            condition_expression=("attribute_exists(PK) and attribute_exists(SK)"),
        )

    @handle_dynamodb_errors("update_receipt_places")
    def update_receipt_places(self, receipt_places: List[ReceiptPlace]) -> None:
        """
        Updates multiple ReceiptPlace records in DynamoDB using transactions.

        Parameters
        ----------
        receipt_places : List[ReceiptPlace]
            A list of ReceiptPlace instances to update.

        Raises
        ------
        ValueError
            If receipt_places is invalid or if any record does not exist.
        """
        self._validate_entity_list(receipt_places, ReceiptPlace, "receipt_places")

        transact_items = [
            TransactWriteItemTypeDef(
                Put=PutTypeDef(
                    TableName=self.table_name,
                    Item=item.to_item(),
                    ConditionExpression=(
                        "attribute_exists(PK) and attribute_exists(SK)"
                    ),
                )
            )
            for item in receipt_places
        ]
        self._transact_write_with_chunking(transact_items)

    @handle_dynamodb_errors("delete_receipt_place")
    def delete_receipt_place(self, receipt_place: ReceiptPlace) -> None:
        """
        Deletes a single ReceiptPlace record from DynamoDB.

        Parameters
        ----------
        receipt_place : ReceiptPlace
            The ReceiptPlace instance to delete.

        Raises
        ------
        ValueError
            If receipt_place is invalid.
        """
        self._validate_entity(receipt_place, ReceiptPlace, "receipt_place")
        self._delete_entity(receipt_place)

    @handle_dynamodb_errors("delete_receipt_places")
    def delete_receipt_places(self, receipt_places: List[ReceiptPlace]) -> None:
        """
        Deletes multiple ReceiptPlace records from DynamoDB.

        Parameters
        ----------
        receipt_places : List[ReceiptPlace]
            A list of ReceiptPlace instances to delete.

        Raises
        ------
        ValueError
            If receipt_places is invalid or if any record does not exist.
        """
        self._validate_entity_list(receipt_places, ReceiptPlace, "receipt_places")

        transact_items = [
            TransactWriteItemTypeDef(
                Delete=DeleteTypeDef(
                    TableName=self.table_name,
                    Key=item.key,
                    ConditionExpression=(
                        "attribute_exists(PK) and attribute_exists(SK)"
                    ),
                )
            )
            for item in receipt_places
        ]
        self._transact_write_with_chunking(transact_items)

    @handle_dynamodb_errors("get_receipt_place")
    def get_receipt_place(self, image_id: str, receipt_id: int) -> ReceiptPlace:
        """
        Retrieves a single ReceiptPlace record from DynamoDB.

        Parameters
        ----------
        image_id : str
            The image_id of the ReceiptPlace record to retrieve.
        receipt_id : int
            The receipt_id of the ReceiptPlace record to retrieve.

        Returns
        -------
        ReceiptPlace
            The corresponding ReceiptPlace instance.

        Raises
        ------
        ValueError
            If parameters are invalid or if the record does not exist.
        """
        self._validate_image_id(image_id)
        self._validate_receipt_id(receipt_id)

        result = self._get_entity(
            primary_key=f"IMAGE#{image_id}",
            sort_key=f"RECEIPT#{receipt_id:05d}#PLACE",
            entity_class=ReceiptPlace,
            converter_func=lambda item: ReceiptPlace(**item),
        )

        if result is None:
            raise EntityNotFoundError(
                f"ReceiptPlace with image_id={image_id}, "
                f"receipt_id={receipt_id} does not exist"
            )

        return result

    @handle_dynamodb_errors("get_receipt_places_by_indices")
    def get_receipt_places_by_indices(
        self, indices: list[tuple[str, int]]
    ) -> list[ReceiptPlace]:
        """
        Retrieves a list of ReceiptPlace records from DynamoDB by indices.

        Parameters
        ----------
        indices : list[tuple[str, int]]
            A list of tuples of (image_id, receipt_id).

        Returns
        -------
        list[ReceiptPlace]
            A list of ReceiptPlace records.

        Raises
        ------
        ValueError
            If indices is invalid.
        """
        if indices is None:
            raise EntityValidationError("indices cannot be None")
        if not isinstance(indices, list):
            raise EntityValidationError("indices must be a list")
        if not all(isinstance(index, tuple) for index in indices):
            raise EntityValidationError("indices must be a list of tuples")
        if not all(
            isinstance(index[0], str) and isinstance(index[1], int) for index in indices
        ):
            raise EntityValidationError(
                "indices must be a list of tuples of (image_id, receipt_id)"
            )
        if not all(index[1] > 0 for index in indices):
            raise EntityValidationError("receipt_id must be positive")

        keys = [
            {
                "PK": {"S": f"IMAGE#{index[0]}"},
                "SK": {"S": f"RECEIPT#{index[1]:05d}#PLACE"},
            }
            for index in indices
        ]
        return self.get_receipt_places(keys)

    @handle_dynamodb_errors("get_receipt_places")
    def get_receipt_places(self, keys: list[dict]) -> list[ReceiptPlace]:
        """
        Retrieves a list of ReceiptPlace records from DynamoDB using keys.

        Parameters
        ----------
        keys : list[dict]
            A list of keys to retrieve the ReceiptPlace records by.

        Returns
        -------
        list[ReceiptPlace]
            A list of ReceiptPlace records.

        Raises
        ------
        ValueError
            If keys is invalid.
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
            if not key["SK"]["S"].split("#")[-1] == "PLACE":
                raise EntityValidationError("SK must contain 'PLACE'")

        results = []
        for i in range(0, len(keys), CHUNK_SIZE):
            chunk = keys[i : i + CHUNK_SIZE]
            response = self._client.batch_get_item(
                RequestItems={self.table_name: {"Keys": chunk}}
            )
            batch_items = response["Responses"].get(self.table_name, [])
            results.extend(batch_items)
            unprocessed = response.get("UnprocessedKeys", {})
            while unprocessed.get(self.table_name):
                response = self._client.batch_get_item(RequestItems=unprocessed)
                batch_items = response["Responses"].get(self.table_name, [])
                results.extend(batch_items)
                unprocessed = response.get("UnprocessedKeys", {})
        return [ReceiptPlace(**result) for result in results]

    @handle_dynamodb_errors("list_receipt_places")
    def list_receipt_places(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> Tuple[List[ReceiptPlace], dict | None]:
        """
        Lists ReceiptPlace records from DynamoDB with optional pagination.

        Parameters
        ----------
        limit : int, optional
            Maximum number of records to retrieve.
        last_evaluated_key : dict, optional
            The key to start pagination from.

        Returns
        -------
        Tuple[List[ReceiptPlace], dict | None]
            A tuple containing the list of ReceiptPlace records and the last
            evaluated key.

        Raises
        ------
        ValueError
            If parameters are invalid.
        """
        if limit is not None and not isinstance(limit, int):
            raise EntityValidationError("limit must be an integer")
        if limit is not None and limit <= 0:
            raise EntityValidationError("limit must be positive")

        if last_evaluated_key is not None and not isinstance(last_evaluated_key, dict):
            raise EntityValidationError("last_evaluated_key must be a dictionary")

        return self._query_by_type(
            entity_type="RECEIPT_PLACE",
            converter_func=lambda item: ReceiptPlace(**item),
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("get_receipt_places_by_merchant")
    def get_receipt_places_by_merchant(
        self,
        merchant_name: str,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> Tuple[List[ReceiptPlace], dict | None]:
        """
        Retrieves ReceiptPlace records from DynamoDB by merchant name (GSI1).

        Parameters
        ----------
        merchant_name : str
            The merchant name to filter by.
        limit : int, optional
            Maximum number of records to retrieve.
        last_evaluated_key : dict, optional
            The key to start pagination from.

        Returns
        -------
        Tuple[List[ReceiptPlace], dict | None]
            A tuple containing the list of ReceiptPlace records and the last
            evaluated key.

        Raises
        ------
        ValueError
            If merchant_name is invalid.
        """
        if merchant_name is None:
            raise EntityValidationError("merchant_name cannot be None")
        if not isinstance(merchant_name, str):
            raise EntityValidationError("merchant_name must be a string")
        normalized_merchant_name = merchant_name.upper().replace(" ", "_")
        gsi1_pk = f"MERCHANT#{normalized_merchant_name}"

        return self._query_entities(
            index_name="GSI1",
            key_condition_expression="#pk = :pk",
            expression_attribute_names={"#pk": "GSI1PK"},
            expression_attribute_values={":pk": {"S": gsi1_pk}},
            converter_func=lambda item: ReceiptPlace(**item),
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("list_receipt_places_with_place_id")
    def list_receipt_places_with_place_id(
        self,
        place_id: str,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> Tuple[List[ReceiptPlace], dict | None]:
        """
        Retrieves ReceiptPlace records that have a specific place_id (GSI2).

        Uses GSI2 for efficient direct querying by place_id.

        Parameters
        ----------
        place_id : str
            The place_id to query for.
        limit : int, optional
            Maximum number of records to retrieve.
        last_evaluated_key : dict, optional
            The key to start pagination from.

        Returns
        -------
        Tuple[List[ReceiptPlace], dict | None]
            A tuple containing the list of ReceiptPlace records and the last
            evaluated key.

        Raises
        ------
        ValueError
            If place_id is invalid.
        """
        if not place_id:
            raise EntityValidationError("place_id cannot be empty")
        if not isinstance(place_id, str):
            raise EntityValidationError("place_id must be a string")
        if limit is not None and not isinstance(limit, int):
            raise EntityValidationError("limit must be an integer")
        if limit is not None and limit <= 0:
            raise EntityValidationError("limit must be positive")
        if last_evaluated_key is not None and not isinstance(last_evaluated_key, dict):
            raise EntityValidationError("last_evaluated_key must be a dictionary")

        return self._query_entities(
            index_name="GSI2",
            key_condition_expression="GSI2PK = :pk",
            expression_attribute_names=None,
            expression_attribute_values={":pk": {"S": f"PLACE#{place_id}"}},
            converter_func=lambda item: ReceiptPlace(**item),
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("get_receipt_places_by_status")
    def get_receipt_places_by_status(
        self,
        validation_status: str,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> Tuple[List[ReceiptPlace], dict | None]:
        """
        Retrieves ReceiptPlace records by validation status (GSI3).

        Parameters
        ----------
        validation_status : str
            The validation status to filter by (MATCHED, UNSURE, NO_MATCH).
        limit : int, optional
            Maximum number of records to retrieve.
        last_evaluated_key : dict, optional
            The key to start pagination from.

        Returns
        -------
        Tuple[List[ReceiptPlace], dict | None]
            A tuple containing the list of ReceiptPlace records and the last
            evaluated key.

        Raises
        ------
        ValueError
            If validation_status is invalid.
        """
        if not validation_status:
            raise EntityValidationError("validation_status cannot be empty")
        if not isinstance(validation_status, str):
            raise EntityValidationError("validation_status must be a string")
        if limit is not None and not isinstance(limit, int):
            raise EntityValidationError("limit must be an integer")
        if limit is not None and limit <= 0:
            raise EntityValidationError("limit must be positive")
        if last_evaluated_key is not None and not isinstance(last_evaluated_key, dict):
            raise EntityValidationError("last_evaluated_key must be a dictionary")

        return self._query_entities(
            index_name="GSI3",
            key_condition_expression="GSI3PK = :pk AND begins_with(GSI3SK, :sk)",
            expression_attribute_names=None,
            expression_attribute_values={
                ":pk": {"S": "PLACE_VALIDATION"},
                ":sk": {"S": f"STATUS#{validation_status}"},
            },
            converter_func=lambda item: ReceiptPlace(**item),
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("get_receipt_places_by_geohash")
    def get_receipt_places_by_geohash(
        self,
        geohash: str,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> Tuple[List[ReceiptPlace], dict | None]:
        """
        Retrieves ReceiptPlace records by geohash for spatial queries (GSI4).

        Uses GSI4 to find places within a specific geographic region defined
        by the geohash.

        Parameters
        ----------
        geohash : str
            The geohash to query for (precision 6-7 for ~1km cells).
        limit : int, optional
            Maximum number of records to retrieve.
        last_evaluated_key : dict, optional
            The key to start pagination from.

        Returns
        -------
        Tuple[List[ReceiptPlace], dict | None]
            A tuple containing the list of ReceiptPlace records and the last
            evaluated key.

        Raises
        ------
        ValueError
            If geohash is invalid.
        """
        if not geohash:
            raise EntityValidationError("geohash cannot be empty")
        if not isinstance(geohash, str):
            raise EntityValidationError("geohash must be a string")
        if len(geohash) < 6:
            raise EntityValidationError("geohash must be at least 6 characters")
        if limit is not None and not isinstance(limit, int):
            raise EntityValidationError("limit must be an integer")
        if limit is not None and limit <= 0:
            raise EntityValidationError("limit must be positive")
        if last_evaluated_key is not None and not isinstance(last_evaluated_key, dict):
            raise EntityValidationError("last_evaluated_key must be a dictionary")

        return self._query_entities(
            index_name="GSI4",
            key_condition_expression="GSI4PK = :pk",
            expression_attribute_names=None,
            expression_attribute_values={":pk": {"S": f"GEOHASH#{geohash}"}},
            converter_func=lambda item: ReceiptPlace(**item),
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )
