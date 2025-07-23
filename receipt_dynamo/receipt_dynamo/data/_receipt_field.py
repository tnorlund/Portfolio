# infra/lambda_layer/python/dynamo/data/_receipt_field.py
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from botocore.exceptions import ClientError
from receipt_dynamo.data.base_operations import (
    BatchOperationsMixin,
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    TransactionalOperationsMixin,
    handle_dynamodb_errors,
)
from receipt_dynamo.entities.receipt_field import (
    ReceiptField,
    item_to_receipt_field,
)
from receipt_dynamo.entities.util import assert_valid_uuid

if TYPE_CHECKING:
    from receipt_dynamo.data._base import QueryInputTypeDef

# These are used at runtime, not just for type checking
from receipt_dynamo.data._base import (
    DeleteTypeDef,
    PutRequestTypeDef,
    PutTypeDef,
    QueryInputTypeDef,
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

# DynamoDB batch_write_item can only handle up to 25 items per call
CHUNK_SIZE = 25


def validate_last_evaluated_key(lek: Dict[str, Any]) -> None:
    required_keys = {"PK", "SK"}
    if not required_keys.issubset(lek.keys()):
        raise ValueError(
            f"LastEvaluatedKey must contain keys: {required_keys}"
        )
    for key in required_keys:
        if not isinstance(lek[key], dict) or "S" not in lek[key]:
            raise ValueError(
                f"LastEvaluatedKey[{key}] must be a dict containing a key 'S'"
            )


class _ReceiptField(
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    BatchOperationsMixin,
    TransactionalOperationsMixin,
):
    """
    A class providing methods to interact with "ReceiptField" entities in DynamoDB.
    This class is typically used within a DynamoClient to access and manage
    receipt field records.

    Attributes
    ----------
    _client : boto3.client
        The Boto3 DynamoDB client (must be set externally).
    table_name : str
        The name of the DynamoDB table (must be set externally).

    Methods
    -------
    add_receipt_field(receipt_field: ReceiptField):
        Adds a single ReceiptField item to the database, ensuring unique ID.
    add_receipt_fields(receipt_fields: List[ReceiptField]):
        Adds multiple ReceiptField items to the database in chunks of up to 25 items.
    update_receipt_field(receipt_field: ReceiptField):
        Updates an existing ReceiptField item in the database.
    update_receipt_fields(receipt_fields: List[ReceiptField]):
        Updates multiple ReceiptField items using transactions.
    delete_receipt_field(receipt_field: ReceiptField):
        Deletes a single ReceiptField item from the database.
    delete_receipt_fields(receipt_fields: List[ReceiptField]):
        Deletes multiple ReceiptField items using transactions.
    get_receipt_field(field_type: str, image_id: str, receipt_id: int) -> ReceiptField:
        Retrieves a single ReceiptField item by its composite key.
    list_receipt_fields(...) -> Tuple[List[ReceiptField], dict | None]:
        Lists ReceiptField records with optional pagination.
    get_receipt_fields_by_image(...) -> Tuple[List[ReceiptField], dict | None]:
        Retrieves ReceiptField records by image ID.
    get_receipt_fields_by_receipt(...) -> Tuple[List[ReceiptField], dict | None]:
        Retrieves ReceiptField records by receipt ID.
    """

    @handle_dynamodb_errors("add_receipt_field")
    def add_receipt_field(self, receipt_field: ReceiptField) -> None:
        """
        Adds a receipt field to the database.

        Parameters
        ----------
        receipt_field : ReceiptField
            The receipt field to add to the database.

        Raises
        ------
        ValueError
            When a receipt field with the same ID already exists.
        """
        self._validate_entity(receipt_field, ReceiptField, "receiptField")
        self._add_entity(receipt_field)

    @handle_dynamodb_errors("add_receipt_fields")
    def add_receipt_fields(self, receipt_fields: list[ReceiptField]) -> None:
        """
        Adds a list of receipt fields to the database.

        Parameters
        ----------
        receipt_fields : list[ReceiptField]
            The receipt fields to add to the database.

        Raises
        ------
        ValueError
            If receipt_fields is invalid or if an error occurs during batch write.
        """
        self._validate_entity_list(
            receipt_fields, ReceiptField, "receiptFields"
        )

        request_items = [
            WriteRequestTypeDef(
                PutRequest=PutRequestTypeDef(Item=field.to_item())
            )
            for field in receipt_fields
        ]
        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("update_receipt_field")
    def update_receipt_field(self, receipt_field: ReceiptField) -> None:
        """
        Updates a receipt field in the database.

        Parameters
        ----------
        receipt_field : ReceiptField
            The receipt field to update in the database.

        Raises
        ------
        ValueError
            When the receipt field does not exist.
        """
        self._validate_entity(receipt_field, ReceiptField, "receiptField")
        self._update_entity(receipt_field)

    @handle_dynamodb_errors("update_receipt_fields")
    def update_receipt_fields(
        self, receipt_fields: list[ReceiptField]
    ) -> None:
        """
        Updates a list of receipt fields in the database using transactions.

        Parameters
        ----------
        receipt_fields : list[ReceiptField]
            The receipt fields to update in the database.

        Raises
        ------
        ValueError
            When given a bad parameter or if a field doesn't exist.
        """
        self._validate_entity_list(
            receipt_fields, ReceiptField, "receiptFields"
        )

        transact_items = [
            TransactWriteItemTypeDef(
                Put=PutTypeDef(
                    TableName=self.table_name,
                    Item=field.to_item(),
                    ConditionExpression="attribute_exists(PK)",
                )
            )
            for field in receipt_fields
        ]
        self._transact_write_with_chunking(transact_items)

    @handle_dynamodb_errors("delete_receipt_field")
    def delete_receipt_field(self, receipt_field: ReceiptField) -> None:
        """
        Deletes a receipt field from the database.

        Parameters
        ----------
        receipt_field : ReceiptField
            The receipt field to delete from the database.

        Raises
        ------
        ValueError
            When the receipt field does not exist.
        """
        self._validate_entity(receipt_field, ReceiptField, "receiptField")
        self._delete_entity(receipt_field)

    @handle_dynamodb_errors("delete_receipt_fields")
    def delete_receipt_fields(
        self, receipt_fields: list[ReceiptField]
    ) -> None:
        """
        Deletes a list of receipt fields from the database using transactions.

        Parameters
        ----------
        receipt_fields : list[ReceiptField]
            The receipt fields to delete from the database.

        Raises
        ------
        ValueError
            When a receipt field does not exist or if another error occurs.
        """
        self._validate_entity_list(
            receipt_fields, ReceiptField, "receiptFields"
        )

        transact_items = [
            TransactWriteItemTypeDef(
                Delete=DeleteTypeDef(
                    TableName=self.table_name,
                    Key=field.key,
                    ConditionExpression="attribute_exists(PK)",
                )
            )
            for field in receipt_fields
        ]
        self._transact_write_with_chunking(transact_items)

    def get_receipt_field(
        self, field_type: str, image_id: str, receipt_id: int
    ) -> ReceiptField:
        """
        Retrieves a receipt field from the database.

        Parameters
        ----------
        field_type : str
            The type of field to retrieve.
        image_id : str
            The ID of the image the receipt belongs to.
        receipt_id : int
            The ID of the receipt.

        Returns
        -------
        ReceiptField
            The receipt field object.

        Raises
        ------
        ValueError
            If input parameters are invalid or if the field does not exist.
        """
        if field_type is None:
            raise ValueError("Field type is required and cannot be None.")
        if image_id is None:
            raise ValueError("Image ID is required and cannot be None.")
        if receipt_id is None:
            raise ValueError("Receipt ID is required and cannot be None.")

        # Validate image_id as a UUID and receipt_id as a positive integer
        assert_valid_uuid(image_id)
        if not isinstance(receipt_id, int) or receipt_id <= 0:
            raise ValueError("Receipt ID must be a positive integer.")
        if not isinstance(field_type, str) or not field_type:
            raise ValueError("Field type must be a non-empty string.")

        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"FIELD#{field_type.upper()}"},
                    "SK": {"S": f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}"},
                },
            )
            if "Item" in response:
                return item_to_receipt_field(response["Item"])
            else:
                raise ValueError(
                    f"Receipt field for Field Type '{field_type}', Image ID '{image_id}', and Receipt ID {receipt_id} does not exist."
                )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                ) from e
            elif error_code == "ValidationException":
                raise OperationError(f"Validation error: {e}") from e
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}") from e
            elif error_code == "AccessDeniedException":
                raise DynamoDBAccessError(f"Access denied: {e}") from e
            else:
                raise OperationError(
                    f"Error getting receipt field: {e}"
                ) from e

    def list_receipt_fields(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[ReceiptField], dict | None]:
        """
        Retrieve receipt field records from the database with support for precise pagination.

        Parameters
        ----------
        limit : int, optional
            The maximum number of receipt field items to return.
        last_evaluated_key : dict, optional
            A key that marks the starting point for the query.

        Returns
        -------
        tuple
            - A list of ReceiptField objects.
            - A dict representing the LastEvaluatedKey from the final query page, or None if there are no further pages.

        Raises
        ------
        ValueError
            If the limit is not an integer or is less than or equal to 0.
        """
        if limit is not None and not isinstance(limit, int):
            raise ValueError("Limit must be an integer")
        if limit is not None and limit <= 0:
            raise ValueError("Limit must be greater than 0")
        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise ValueError("LastEvaluatedKey must be a dictionary")
            validate_last_evaluated_key(last_evaluated_key)

        fields: List[ReceiptField] = []
        try:
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "IndexName": "GSITYPE",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "TYPE"},
                "ExpressionAttributeValues": {":val": {"S": "RECEIPT_FIELD"}},
            }
            if last_evaluated_key is not None:
                query_params["ExclusiveStartKey"] = last_evaluated_key

            while True:
                if limit is not None:
                    remaining = limit - len(fields)
                    query_params["Limit"] = remaining

                response = self._client.query(**query_params)
                fields.extend(
                    [item_to_receipt_field(item) for item in response["Items"]]
                )

                if limit is not None and len(fields) >= limit:
                    fields = fields[:limit]
                    last_evaluated_key = response.get("LastEvaluatedKey", None)
                    break

                if "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                else:
                    last_evaluated_key = None
                    break

            return fields, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    f"Could not list receipt fields from the database: {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                ) from e
            elif error_code == "ValidationException":
                raise DynamoDBValidationError(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}") from e
            else:
                raise DynamoDBError(
                    f"Could not list receipt fields from the database: {e}"
                ) from e

    def get_receipt_fields_by_image(
        self,
        image_id: str,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[ReceiptField], dict | None]:
        """
        Retrieve receipt fields by image ID using GSI1.

        Parameters
        ----------
        image_id : str
            The image ID to search for.
        limit : int, optional
            The maximum number of fields to return.
        last_evaluated_key : dict, optional
            The key to start the query from.

        Returns
        -------
        tuple[list[ReceiptField], dict | None]
            A tuple containing:
            - List of ReceiptField objects
            - Last evaluated key for pagination (None if no more pages)

        Raises
        ------
        ValueError
            If the image_id is invalid or if pagination parameters are invalid.
        """
        if not isinstance(image_id, str):
            raise ValueError("Image ID must be a string")
        assert_valid_uuid(image_id)
        if limit is not None and not isinstance(limit, int):
            raise ValueError("Limit must be an integer")
        if limit is not None and limit <= 0:
            raise ValueError("Limit must be greater than 0")
        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise ValueError("LastEvaluatedKey must be a dictionary")
            validate_last_evaluated_key(last_evaluated_key)

        fields: List[ReceiptField] = []
        try:
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "IndexName": "GSI1",
                "KeyConditionExpression": "GSI1PK = :pk",
                "ExpressionAttributeValues": {
                    ":pk": {"S": f"IMAGE#{image_id}"}
                },
            }
            if last_evaluated_key is not None:
                query_params["ExclusiveStartKey"] = last_evaluated_key

            while True:
                if limit is not None:
                    remaining = limit - len(fields)
                    query_params["Limit"] = remaining

                response = self._client.query(**query_params)
                fields.extend(
                    [item_to_receipt_field(item) for item in response["Items"]]
                )

                if limit is not None and len(fields) >= limit:
                    fields = fields[:limit]
                    last_evaluated_key = response.get("LastEvaluatedKey", None)
                    break

                if "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                else:
                    last_evaluated_key = None
                    break

            return fields, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    f"Could not list receipt fields by image ID: {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                ) from e
            elif error_code == "ValidationException":
                raise DynamoDBValidationError(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}") from e
            elif error_code == "AccessDeniedException":
                raise DynamoDBAccessError(f"Access denied: {e}") from e
            else:
                raise DynamoDBError(
                    f"Could not list receipt fields by image ID: {e}"
                ) from e

    def get_receipt_fields_by_receipt(
        self,
        image_id: str,
        receipt_id: int,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[ReceiptField], dict | None]:
        """
        Retrieve receipt fields by receipt ID using GSI1.

        Parameters
        ----------
        image_id : str
            The image ID to search for.
        receipt_id : int
            The receipt ID to search for.
        limit : int, optional
            The maximum number of fields to return.
        last_evaluated_key : dict, optional
            The key to start the query from.

        Returns
        -------
        tuple[list[ReceiptField], dict | None]
            A tuple containing:
            - List of ReceiptField objects
            - Last evaluated key for pagination (None if no more pages)

        Raises
        ------
        ValueError
            If the image_id or receipt_id is invalid or if pagination parameters are invalid.
        """
        if not isinstance(image_id, str):
            raise ValueError("Image ID must be a string")
        assert_valid_uuid(image_id)
        if not isinstance(receipt_id, int) or receipt_id <= 0:
            raise ValueError("Receipt ID must be a positive integer")
        if limit is not None and not isinstance(limit, int):
            raise ValueError("Limit must be an integer")
        if limit is not None and limit <= 0:
            raise ValueError("Limit must be greater than 0")
        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise ValueError("LastEvaluatedKey must be a dictionary")
            validate_last_evaluated_key(last_evaluated_key)

        fields: List[ReceiptField] = []
        try:
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "IndexName": "GSI1",
                "KeyConditionExpression": "GSI1PK = :pk AND begins_with(GSI1SK, :sk_prefix)",
                "ExpressionAttributeValues": {
                    ":pk": {"S": f"IMAGE#{image_id}"},
                    ":sk_prefix": {"S": f"RECEIPT#{receipt_id:05d}"},
                },
            }
            if last_evaluated_key is not None:
                query_params["ExclusiveStartKey"] = last_evaluated_key

            while True:
                if limit is not None:
                    remaining = limit - len(fields)
                    query_params["Limit"] = remaining

                response = self._client.query(**query_params)
                fields.extend(
                    [item_to_receipt_field(item) for item in response["Items"]]
                )

                if limit is not None and len(fields) >= limit:
                    fields = fields[:limit]
                    last_evaluated_key = response.get("LastEvaluatedKey", None)
                    break

                if "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                else:
                    last_evaluated_key = None
                    break

            return fields, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    f"Could not list receipt fields by receipt ID: {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                ) from e
            elif error_code == "ValidationException":
                raise DynamoDBValidationError(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}") from e
            elif error_code == "AccessDeniedException":
                raise DynamoDBAccessError(f"Access denied: {e}") from e
            else:
                raise DynamoDBError(
                    f"Could not list receipt fields by receipt ID: {e}"
                ) from e
