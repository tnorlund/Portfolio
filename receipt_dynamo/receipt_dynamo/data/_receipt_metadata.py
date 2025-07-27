# infra/lambda_layer/python/dynamo/data/_receipt_metadata.py
from typing import List, Optional, Tuple, TYPE_CHECKING

from botocore.exceptions import ClientError

from receipt_dynamo.data.base_operations import (
    BatchOperationsMixin,
    DeleteTypeDef,
    DynamoDBBaseOperations,
    PutRequestTypeDef,
    PutTypeDef,
    QueryInputTypeDef,
    TransactWriteItemTypeDef,
    WriteRequestTypeDef,
    handle_dynamodb_errors,
    SingleEntityCRUDMixin,
    TransactionalOperationsMixin,
)
from receipt_dynamo.entities import item_to_receipt_metadata, ReceiptMetadata
from receipt_dynamo.entities.util import assert_valid_uuid

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
            raise ValueError("image_id cannot be None")
        if not isinstance(image_id, str):
            raise ValueError("image_id must be a string")
        assert_valid_uuid(image_id)
        if receipt_id is None:
            raise ValueError("receipt_id cannot be None")
        if not isinstance(receipt_id, int):
            raise ValueError("receipt_id must be an integer")
        if receipt_id <= 0:
            raise ValueError("receipt_id must be positive")

        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {"S": f"RECEIPT#{receipt_id:05d}#METADATA"},
                },
            )
            item = response.get("Item")
            if item is None:
                raise ValueError("receipt_metadata does not exist")
            return item_to_receipt_metadata(item)
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "ValidationException":
                raise ValueError(
                    "receipt_metadata contains invalid attributes or values"
                )
            elif error_code == "InternalServerError":
                raise ValueError("internal server error") from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise ValueError("provisioned throughput exceeded") from e
            elif error_code == "ResourceNotFoundException":
                raise ValueError("table not found") from e
            else:
                raise ValueError(f"Error getting receipt metadata: {e}") from e

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
            raise ValueError("indices cannot be None")
        if not isinstance(indices, list):
            raise ValueError("indices must be a list")
        if not all(isinstance(index, tuple) for index in indices):
            raise ValueError("indices must be a list of tuples")
        if not all(
            isinstance(index[0], str) and isinstance(index[1], int)
            for index in indices
        ):
            raise ValueError(
                "indices must be a list of tuples of (image_id, receipt_id)"
            )
        if not all(index[1] > 0 for index in indices):
            raise ValueError("receipt_id must be positive")

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
            raise ValueError("keys cannot be None")
        if not isinstance(keys, list):
            raise ValueError("keys must be a list")
        if not all(isinstance(key, dict) for key in keys):
            raise ValueError("keys must be a list of dictionaries")
        for key in keys:
            if not {"PK", "SK"}.issubset(key.keys()):
                raise ValueError("keys must contain 'PK' and 'SK'")
            if not key["PK"]["S"].startswith("IMAGE#"):
                raise ValueError("PK must start with 'IMAGE#'")
            if not key["SK"]["S"].startswith("RECEIPT#"):
                raise ValueError("SK must start with 'RECEIPT#'")
            if not key["SK"]["S"].split("#")[-1] == "METADATA":
                raise ValueError("SK must contain 'METADATA'")

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
            raise ValueError("limit must be an integer")
        if limit is not None and limit <= 0:
            raise ValueError("limit must be positive")

        if last_evaluated_key is not None and not isinstance(
            last_evaluated_key, dict
        ):
            raise ValueError("last_evaluated_key must be a dictionary")

        metadatas: List[ReceiptMetadata] = []
        try:
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "IndexName": "GSITYPE",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "TYPE"},
                "ExpressionAttributeValues": {
                    ":val": {"S": "RECEIPT_METADATA"}
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
                raise ValueError(
                    "receipt_metadata contains invalid attributes or values"
                )
            elif error_code == "InternalServerError":
                raise ValueError("internal server error") from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise ValueError("provisioned throughput exceeded") from e
            elif error_code == "ResourceNotFoundException":
                raise ValueError("table not found") from e
            else:
                raise ValueError(f"Error listing receipt metadata: {e}") from e

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
            raise ValueError("merchant_name cannot be None")
        if not isinstance(merchant_name, str):
            raise ValueError("merchant_name must be a string")
        normalized_merchant_name = merchant_name.upper().replace(" ", "_")
        gsi1_pk = f"MERCHANT#{normalized_merchant_name}"

        metadatas: List[ReceiptMetadata] = []
        try:
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "IndexName": "GSI1",
                "KeyConditionExpression": "#pk = :pk",
                "ExpressionAttributeNames": {"#pk": "GSI1PK"},
                "ExpressionAttributeValues": {":pk": {"S": gsi1_pk}},
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
                raise ValueError(
                    "receipt_metadata contains invalid attributes or values"
                )
            elif error_code == "InternalServerError":
                raise ValueError("internal server error") from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise ValueError("provisioned throughput exceeded") from e
            elif error_code == "ResourceNotFoundException":
                raise ValueError("table not found") from e
            else:
                raise ValueError(f"Error getting receipt metadata: {e}") from e

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
            raise ValueError("place_id cannot be empty")
        if not isinstance(place_id, str):
            raise ValueError("place_id must be a string")
        if limit is not None and not isinstance(limit, int):
            raise ValueError("limit must be an integer")
        if limit is not None and limit <= 0:
            raise ValueError("limit must be positive")
        if last_evaluated_key is not None and not isinstance(
            last_evaluated_key, dict
        ):
            raise ValueError("last_evaluated_key must be a dictionary")

        metadatas: List[ReceiptMetadata] = []
        try:
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "IndexName": "GSI2",
                "KeyConditionExpression": "GSI2PK = :pk",
                "ExpressionAttributeValues": {
                    ":pk": {"S": f"PLACE#{place_id}"}
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
                raise ValueError(
                    "receipt_metadata contains invalid attributes or values"
                )
            elif error_code == "InternalServerError":
                raise ValueError("internal server error") from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise ValueError("provisioned throughput exceeded") from e
            elif error_code == "ResourceNotFoundException":
                raise ValueError("table not found") from e
            else:
                raise ValueError(f"Error listing receipt metadata: {e}") from e

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
            raise ValueError("confidence cannot be None")
        if not isinstance(confidence, float):
            raise ValueError("confidence must be a float")
        if confidence < 0 or confidence > 1:
            raise ValueError("confidence must be between 0 and 1")
        if above is not None and not isinstance(above, bool):
            raise ValueError("above must be a boolean")

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
                raise ValueError(
                    (
                        "receipt_metadata contains invalid attributes or "
                        f"values: {e}"
                    )
                )
            elif error_code == "InternalServerError":
                raise ValueError("internal server error") from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise ValueError("provisioned throughput exceeded") from e
            elif error_code == "ResourceNotFoundException":
                raise ValueError("table not found") from e
            else:
                raise ValueError(f"Error getting receipt metadata: {e}") from e
