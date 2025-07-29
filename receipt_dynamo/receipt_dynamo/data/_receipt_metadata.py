# infra/lambda_layer/python/dynamo/data/_receipt_metadata.py
from typing import TYPE_CHECKING, List, Optional, Tuple

from botocore.exceptions import ClientError

from receipt_dynamo.data.base_operations import (
    BatchOperationsMixin,
    DeleteTypeDef,
    DynamoDBBaseOperations,
    PutRequestTypeDef,
    PutTypeDef,
    QueryInputTypeDef,
    SingleEntityCRUDMixin,
    TransactionalOperationsMixin,
    TransactWriteItemTypeDef,
    WriteRequestTypeDef,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import EntityNotFoundError
from receipt_dynamo.entities import ReceiptMetadata, item_to_receipt_metadata
from receipt_dynamo.entities.util import assert_valid_uuid
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    EntityValidationError,
)

if TYPE_CHECKING:
    pass

# DynamoDB batch_write_item can only handle up to 25 items per call
CHUNK_SIZE = 25


class _ReceiptMetadata(
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    BatchOperationsMixin,
    TransactionalOperationsMixin,
):
    """
    A class providing methods to interact with "ReceiptMetadata" entities in
    DynamoDB. This class is typically used within a DynamoClient to access and
    manage receipt metadata records.

    Attributes
    ----------
    _client : boto3.client
        The Boto3 DynamoDB client (must be set externally).
    table_name : str
        The name of the DynamoDB table (must be set externally).

    Methods
    -------
    add_receipt_metadata(receipt_metadata: ReceiptMetadata):
        Adds a single ReceiptMetadata item to the database, ensuring unique ID.
    add_receipt_metadatas(receipt_metadatas: List[ReceiptMetadata]):
        Adds multiple ReceiptMetadata items to the database in chunks of up to
        25 items.
    update_receipt_metadata(receipt_metadata: ReceiptMetadata):
        Updates an existing ReceiptMetadata item in the database.
    update_receipt_metadatas(receipt_metadatas: List[ReceiptMetadata]):
        Updates multiple ReceiptMetadata items using transactions.
    delete_receipt_metadata(receipt_metadata: ReceiptMetadata):
        Deletes a single ReceiptMetadata item from the database.
    delete_receipt_metadatas(receipt_metadatas: List[ReceiptMetadata]):
        Deletes multiple ReceiptMetadata items using transactions.
    get_receipt_metadata(image_id: str, receipt_id: int) -> ReceiptMetadata:
        Retrieves a single ReceiptMetadata item by image and receipt IDs.
    get_receipt_metadatas_by_indices(
        indices: list[tuple[str, int]]
    ) -> list[ReceiptMetadata]:
        Retrieves multiple ReceiptMetadata items by their indices.
    get_receipt_metadatas(keys: list[dict]) -> list[ReceiptMetadata]:
        Retrieves multiple ReceiptMetadata items using batch get.
    list_receipt_metadatas(...) -> Tuple[List[ReceiptMetadata], dict | None]:
        Lists ReceiptMetadata records with optional pagination.
    get_receipt_metadatas_by_merchant(
        ...
    ) -> Tuple[List[ReceiptMetadata], dict | None]:
        Retrieves ReceiptMetadata records by merchant name.
    list_receipt_metadatas_with_place_id(
        ...
    ) -> Tuple[List[ReceiptMetadata], dict | None]:
        Retrieves ReceiptMetadata records that have a specific place_id.
    get_receipt_metadatas_by_confidence(
        ...
    ) -> Tuple[List[ReceiptMetadata], dict | None]:
        Retrieves ReceiptMetadata records by confidence score.
    """

    @handle_dynamodb_errors("add_receipt_metadata")
    def add_receipt_metadata(self, receipt_metadata: ReceiptMetadata) -> None:
        """
        Adds a single ReceiptMetadata record to DynamoDB.

        Parameters
        ----------
        receipt_metadata : ReceiptMetadata
            The ReceiptMetadata instance to add.

        Raises
        ------
        ValueError
            If receipt_metadata is None, not a ReceiptMetadata, or if the
            record already exists.
        """
        self._validate_entity(
            receipt_metadata, ReceiptMetadata, "receipt_metadata"
        )
        self._add_entity(
            receipt_metadata,
            condition_expression=(
                "attribute_not_exists(PK) and attribute_not_exists(SK)"
            ),
        )

    @handle_dynamodb_errors("add_receipt_metadatas")
    def add_receipt_metadatas(
        self, receipt_metadatas: List[ReceiptMetadata]
    ) -> None:
        """
        Adds multiple ReceiptMetadata records to DynamoDB in batches.

        Parameters
        ----------
        receipt_metadatas : List[ReceiptMetadata]
            A list of ReceiptMetadata instances to add.

        Raises
        ------
        ValueError
            If receipt_metadatas is invalid or if an error occurs during batch
            write.
        """
        self._validate_entity_list(
            receipt_metadatas, ReceiptMetadata, "receipt_metadatas"
        )

        request_items = [
            WriteRequestTypeDef(
                PutRequest=PutRequestTypeDef(Item=item.to_item())
            )
            for item in receipt_metadatas
        ]
        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("update_receipt_metadata")
    def update_receipt_metadata(
        self, receipt_metadata: ReceiptMetadata
    ) -> None:
        """
        Updates an existing ReceiptMetadata record in DynamoDB.

        Parameters
        ----------
        receipt_metadata : ReceiptMetadata
            The ReceiptMetadata instance to update.

        Raises
        ------
        ValueError
            If receipt_metadata is invalid or if the record does not exist.
        """
        self._validate_entity(
            receipt_metadata, ReceiptMetadata, "receipt_metadata"
        )
        self._update_entity(
            receipt_metadata,
            condition_expression=(
                "attribute_exists(PK) and attribute_exists(SK)"
            ),
        )

    @handle_dynamodb_errors("update_receipt_metadatas")
    def update_receipt_metadatas(
        self, receipt_metadatas: List[ReceiptMetadata]
    ) -> None:
        """
        Updates multiple ReceiptMetadata records in DynamoDB using
        transactions.

        Parameters
        ----------
        receipt_metadatas : List[ReceiptMetadata]
            A list of ReceiptMetadata instances to update.

        Raises
        ------
        ValueError
            If receipt_metadatas is invalid or if any record does not exist.
        """
        self._validate_entity_list(
            receipt_metadatas, ReceiptMetadata, "receipt_metadatas"
        )

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
            for item in receipt_metadatas
        ]
        self._transact_write_with_chunking(transact_items)

    @handle_dynamodb_errors("delete_receipt_metadata")
    def delete_receipt_metadata(
        self, receipt_metadata: ReceiptMetadata
    ) -> None:
        """
        Deletes a single ReceiptMetadata record from DynamoDB.

        Parameters
        ----------
        receipt_metadata : ReceiptMetadata
            The ReceiptMetadata instance to delete.

        Raises
        ------
        ValueError
            If receipt_metadata is invalid.
        """
        self._validate_entity(
            receipt_metadata, ReceiptMetadata, "receipt_metadata"
        )
        self._delete_entity(receipt_metadata)

    @handle_dynamodb_errors("delete_receipt_metadatas")
    def delete_receipt_metadatas(
        self, receipt_metadatas: List[ReceiptMetadata]
    ) -> None:
        """
        Deletes multiple ReceiptMetadata records from DynamoDB.

        Parameters
        ----------
        receipt_metadatas : List[ReceiptMetadata]
            A list of ReceiptMetadata instances to delete.

        Raises
        ------
        ValueError
            If receipt_metadatas is invalid or if any record does not exist.
        """
        self._validate_entity_list(
            receipt_metadatas, ReceiptMetadata, "receipt_metadatas"
        )

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
            for item in receipt_metadatas
        ]
        self._transact_write_with_chunking(transact_items)

    def get_receipt_metadata(
        self, image_id: str, receipt_id: int
    ) -> ReceiptMetadata:
        """
        Retrieves a single ReceiptMetadata record from DynamoDB.

        Parameters
        ----------
        image_id : str
            The image_id of the ReceiptMetadata record to retrieve.
        receipt_id : int
            The receipt_id of the ReceiptMetadata record to retrieve.

        Returns
        -------
        ReceiptMetadata
            The corresponding ReceiptMetadata instance.

        Raises
        ------
        ValueError
            If parameters are invalid or if the record does not exist.
        """
        if image_id is None:
            raise EntityValidationError("image_id cannot be None")
        if not isinstance(image_id, str):
            raise EntityValidationError("image_id must be a string")
        assert_valid_uuid(image_id)
        if receipt_id is None:
            raise EntityValidationError("receipt_id cannot be None")
        if not isinstance(receipt_id, int):
            raise EntityValidationError("receipt_id must be an integer")
        if receipt_id <= 0:
            raise EntityValidationError("receipt_id must be positive")

        result = self._get_entity(
            primary_key=f"IMAGE#{image_id}",
            sort_key=f"RECEIPT#{receipt_id:05d}#METADATA",
            entity_class=ReceiptMetadata,
            converter_func=item_to_receipt_metadata
        )
        
        if result is None:
            raise EntityNotFoundError(
                f"ReceiptMetadata with image_id={image_id}, receipt_id={receipt_id} does not exist"
            )
        
        return result

    def get_receipt_metadatas_by_indices(
        self, indices: list[tuple[str, int]]
    ) -> list[ReceiptMetadata]:
        """
        Retrieves a list of ReceiptMetadata records from DynamoDB by indices.

        Parameters
        ----------
        indices : list[tuple[str, int]]
            A list of tuples of (image_id, receipt_id).

        Returns
        -------
        list[ReceiptMetadata]
            A list of ReceiptMetadata records.

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
            isinstance(index[0], str) and isinstance(index[1], int)
            for index in indices
        ):
            raise EntityValidationError(
                "indices must be a list of tuples of (image_id, receipt_id)"
            )
        if not all(index[1] > 0 for index in indices):
            raise EntityValidationError("receipt_id must be positive")

        keys = [
            {
                "PK": {"S": f"IMAGE#{index[0]}"},
                "SK": {"S": f"RECEIPT#{index[1]:05d}#METADATA"},
            }
            for index in indices
        ]
        return self.get_receipt_metadatas(keys)

    def get_receipt_metadatas(self, keys: list[dict]) -> list[ReceiptMetadata]:
        """
        Retrieves a list of ReceiptMetadata records from DynamoDB using keys.

        Parameters
        ----------
        keys : list[dict]
            A list of keys to retrieve the ReceiptMetadata records by.

        Returns
        -------
        list[ReceiptMetadata]
            A list of ReceiptMetadata records.

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
            if not key["SK"]["S"].split("#")[-1] == "METADATA":
                raise EntityValidationError("SK must contain 'METADATA'")

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
                response = self._client.batch_get_item(
                    RequestItems=unprocessed
                )
                batch_items = response["Responses"].get(self.table_name, [])
                results.extend(batch_items)
                unprocessed = response.get("UnprocessedKeys", {})
        return [item_to_receipt_metadata(result) for result in results]

    def list_receipt_metadatas(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> Tuple[List[ReceiptMetadata], dict | None]:
        """
        Lists ReceiptMetadata records from DynamoDB with optional pagination.

        Parameters
        ----------
        limit : int, optional
            Maximum number of records to retrieve.
        last_evaluated_key : dict, optional
            The key to start pagination from.

        Returns
        -------
        Tuple[List[ReceiptMetadata], dict | None]
            A tuple containing the list of ReceiptMetadata records and the last
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

        if last_evaluated_key is not None and not isinstance(
            last_evaluated_key, dict
        ):
            raise EntityValidationError("last_evaluated_key must be a dictionary")

        return self._query_entities(
            index_name="GSITYPE",
            key_condition_expression="#t = :val",
            expression_attribute_names={"#t": "TYPE"},
            expression_attribute_values={":val": {"S": "RECEIPT_METADATA"}},
            converter_func=item_to_receipt_metadata,
            limit=limit,
            last_evaluated_key=last_evaluated_key
        )

    def get_receipt_metadatas_by_merchant(
        self,
        merchant_name: str,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> Tuple[List[ReceiptMetadata], dict | None]:
        """
        Retrieves ReceiptMetadata records from DynamoDB by merchant name.

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
        Tuple[List[ReceiptMetadata], dict | None]
            A tuple containing the list of ReceiptMetadata records and the last
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
            converter_func=item_to_receipt_metadata,
            limit=limit,
            last_evaluated_key=last_evaluated_key
        )

    def list_receipt_metadatas_with_place_id(
        self,
        place_id: str,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> Tuple[List[ReceiptMetadata], dict | None]:
        """
        Retrieves ReceiptMetadata records that have a specific place_id.

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
        Tuple[List[ReceiptMetadata], dict | None]
            A tuple containing the list of ReceiptMetadata records and the last
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
        if last_evaluated_key is not None and not isinstance(
            last_evaluated_key, dict
        ):
            raise EntityValidationError("last_evaluated_key must be a dictionary")

        return self._query_entities(
            index_name="GSI2",
            key_condition_expression="GSI2PK = :pk",
            expression_attribute_names=None,
            expression_attribute_values={":pk": {"S": f"PLACE#{place_id}"}},
            converter_func=item_to_receipt_metadata,
            limit=limit,
            last_evaluated_key=last_evaluated_key
        )

    def get_receipt_metadatas_by_confidence(
        self,
        confidence: float,
        above: bool = True,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> Tuple[List[ReceiptMetadata], dict | None]:
        """
        Retrieves ReceiptMetadata records by confidence score.

        Parameters
        ----------
        confidence : float
            The confidence score to filter by.
        above : bool, optional
            Whether to filter above or below the confidence score.
        limit : int, optional
            Maximum number of records to retrieve.
        last_evaluated_key : dict, optional
            The key to start pagination from.

        Returns
        -------
        Tuple[List[ReceiptMetadata], dict | None]
            A tuple containing the list of ReceiptMetadata records and the last
            evaluated key.

        Raises
        ------
        ValueError
            If confidence is invalid.
        """
        if confidence is None:
            raise EntityValidationError("confidence cannot be None")
        if not isinstance(confidence, float):
            raise EntityValidationError("confidence must be a float")
        if confidence < 0 or confidence > 1:
            raise EntityValidationError("confidence must be between 0 and 1")
        if above is not None and not isinstance(above, bool):
            raise EntityValidationError("above must be a boolean")

        formatted_score = f"CONFIDENCE#{confidence:.4f}"

        if above:
            key_expr = "GSI2PK = :pk AND GSI2SK >= :sk"
        else:
            key_expr = "GSI2PK = :pk AND GSI2SK <= :sk"

        metadatas: List[ReceiptMetadata] = []
        try:
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "IndexName": "GSI2",
                "KeyConditionExpression": key_expr,
                "ExpressionAttributeValues": {
                    ":pk": {"S": "MERCHANT_VALIDATION"},
                    ":sk": {"S": formatted_score},
                },
            }
            if last_evaluated_key is not None:
                query_params["ExclusiveStartKey"] = last_evaluated_key
            if limit is not None:
                query_params["Limit"] = limit

            response = self._client.query(**query_params)
            metadatas.extend(
                item_to_receipt_metadata(item)
                for item in response.get("Items", [])
            )
            last_evaluated_key = response.get("LastEvaluatedKey")
            return metadatas, last_evaluated_key
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "ValidationException":
                raise EntityValidationError(
                    (
                        "receipt_metadata contains invalid attributes or "
                        f"values: {e}"
            )
                ) from e
            elif error_code == "InternalServerError":
                raise EntityValidationError("internal server error") from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise EntityValidationError("provisioned throughput exceeded") from e
            elif error_code == "ResourceNotFoundException":
                raise EntityNotFoundError("table not found") from e
            else:
                raise EntityValidationError(f"Error getting receipt metadata: {e}") from e
