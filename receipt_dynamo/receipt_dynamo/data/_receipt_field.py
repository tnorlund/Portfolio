# infra/lambda_layer/python/dynamo/data/_receipt_field.py
from typing import TYPE_CHECKING, Any, Dict, Optional

from receipt_dynamo.data.base_operations import (
    DeleteTypeDef,
    DynamoDBBaseOperations,
    FlattenedStandardMixin,
    PutRequestTypeDef,
    PutTypeDef,
    TransactWriteItemTypeDef,
    WriteRequestTypeDef,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    EntityValidationError,
)
from receipt_dynamo.entities.receipt_field import (
    ReceiptField,
    item_to_receipt_field,
)

if TYPE_CHECKING:
    pass

# DynamoDB batch_write_item can only handle up to 25 items per call
CHUNK_SIZE = 25


def validate_last_evaluated_key(lek: Dict[str, Any]) -> None:
    required_keys = {"PK", "SK"}
    if not required_keys.issubset(lek.keys()):
        raise EntityValidationError(
            f"LastEvaluatedKey must contain keys: {required_keys}"
        )
    for key in required_keys:
        if not isinstance(lek[key], dict) or "S" not in lek[key]:
            raise EntityValidationError(
                f"LastEvaluatedKey[{key}] must be a dict containing a key 'S'"
            )


class _ReceiptField(
    DynamoDBBaseOperations,
    FlattenedStandardMixin,
):
    """
    A class providing methods to interact with "ReceiptField" entities in
    DynamoDB.
    This class is typically used within a DynamoClient to access and manage
    receipt field records.

    .. deprecated::
        This class is deprecated and not used in production. Consider removing
        if no longer needed for historical data access.

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
        Adds multiple ReceiptField items to the database in chunks of up to
        25 items.
    update_receipt_field(receipt_field: ReceiptField):
        Updates an existing ReceiptField item in the database.
    update_receipt_fields(receipt_fields: List[ReceiptField]):
        Updates multiple ReceiptField items using transactions.
    delete_receipt_field(receipt_field: ReceiptField):
        Deletes a single ReceiptField item from the database.
    delete_receipt_fields(receipt_fields: List[ReceiptField]):
        Deletes multiple ReceiptField items using transactions.
    get_receipt_field(field_type: str, image_id: str, receipt_id: int) ->
        ReceiptField:
        Retrieves a single ReceiptField item by its composite key.
    list_receipt_fields(...) -> Tuple[List[ReceiptField], dict | None]:
        Lists ReceiptField records with optional pagination.
    get_receipt_fields_by_image(...) -> Tuple[List[ReceiptField], dict | None]:
        Retrieves ReceiptField records by image ID.
    get_receipt_fields_by_receipt(...) ->
        Tuple[List[ReceiptField], dict | None]:
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
        self._validate_entity(receipt_field, ReceiptField, "receipt_field")
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
            If receipt_fields is invalid or if an error occurs during batch
            write.
        """
        self._validate_entity_list(receipt_fields, ReceiptField, "receipt_fields")

        request_items = [
            WriteRequestTypeDef(PutRequest=PutRequestTypeDef(Item=field.to_item()))
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
        self._validate_entity(receipt_field, ReceiptField, "receipt_field")
        self._update_entity(receipt_field)

    @handle_dynamodb_errors("update_receipt_fields")
    def update_receipt_fields(self, receipt_fields: list[ReceiptField]) -> None:
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
        self._validate_entity_list(receipt_fields, ReceiptField, "receipt_fields")

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
        self._validate_entity(receipt_field, ReceiptField, "receipt_field")
        self._delete_entity(receipt_field)

    @handle_dynamodb_errors("delete_receipt_fields")
    def delete_receipt_fields(self, receipt_fields: list[ReceiptField]) -> None:
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
        self._validate_entity_list(receipt_fields, ReceiptField, "receipt_fields")

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

    @handle_dynamodb_errors("get_receipt_field")
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
            raise EntityValidationError("field_type cannot be None")
        self._validate_image_id(image_id)
        if not isinstance(receipt_id, int) or receipt_id <= 0:
            raise EntityValidationError("Receipt ID must be a positive integer.")
        if not isinstance(field_type, str) or not field_type:
            raise EntityValidationError("Field type must be a non-empty string.")

        result = self._get_entity(
            primary_key=f"FIELD#{field_type.upper()}",
            sort_key=f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}",
            entity_class=ReceiptField,
            converter_func=item_to_receipt_field,
        )

        if result is None:
            raise EntityNotFoundError(
                f"Receipt field for Field Type '{field_type}', "
                f"Image ID '{image_id}', and Receipt ID {receipt_id} "
                f"does not exist."
            )

        return result

    @handle_dynamodb_errors("list_receipt_fields")
    def list_receipt_fields(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[ReceiptField], dict | None]:
        """
        Retrieve receipt field records from the database with support for
        precise pagination.

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
            - A dict representing the LastEvaluatedKey from the final query
              page, or None if there are no further pages.

        Raises
        ------
        ValueError
            If the limit is not an integer or is less than or equal to 0.
        """
        if limit is not None and not isinstance(limit, int):
            raise EntityValidationError("Limit must be an integer")
        if limit is not None and limit <= 0:
            raise EntityValidationError("Limit must be greater than 0")
        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise EntityValidationError("LastEvaluatedKey must be a dictionary")
            validate_last_evaluated_key(last_evaluated_key)

        # Use the QueryByTypeMixin for standardized GSITYPE queries
        return self._query_by_type(
            entity_type="RECEIPT_FIELD",
            converter_func=item_to_receipt_field,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("get_receipt_fields_by_image")
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
            raise EntityValidationError("Image ID must be a string")
        self._validate_image_id(image_id)
        if limit is not None and not isinstance(limit, int):
            raise EntityValidationError("Limit must be an integer")
        if limit is not None and limit <= 0:
            raise EntityValidationError("Limit must be greater than 0")
        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise EntityValidationError("LastEvaluatedKey must be a dictionary")
            validate_last_evaluated_key(last_evaluated_key)

        return self._query_entities(
            index_name="GSI1",
            key_condition_expression="GSI1PK = :pk",
            expression_attribute_names=None,
            expression_attribute_values={":pk": {"S": f"IMAGE#{image_id}"}},
            converter_func=item_to_receipt_field,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("get_receipt_fields_by_receipt")
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
            If the image_id or receipt_id is invalid or if pagination
            parameters are invalid.
        """
        if not isinstance(image_id, str):
            raise EntityValidationError("Image ID must be a string")
        self._validate_image_id(image_id)
        if not isinstance(receipt_id, int) or receipt_id <= 0:
            raise EntityValidationError("Receipt ID must be a positive integer")
        if limit is not None and not isinstance(limit, int):
            raise EntityValidationError("Limit must be an integer")
        if limit is not None and limit <= 0:
            raise EntityValidationError("Limit must be greater than 0")
        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise EntityValidationError("LastEvaluatedKey must be a dictionary")
            validate_last_evaluated_key(last_evaluated_key)

        return self._query_entities(
            index_name="GSI1",
            key_condition_expression=(
                "GSI1PK = :pk AND begins_with(GSI1SK, :sk_prefix)"
            ),
            expression_attribute_names=None,
            expression_attribute_values={
                ":pk": {"S": f"IMAGE#{image_id}"},
                ":sk_prefix": {"S": f"RECEIPT#{receipt_id:05d}"},
            },
            converter_func=item_to_receipt_field,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )
