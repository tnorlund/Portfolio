from botocore.exceptions import ClientError

from receipt_dynamo.data.base_operations import (
    DeleteRequestTypeDef,
    FlattenedStandardMixin,
    PutRequestTypeDef,
    WriteRequestTypeDef,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    EntityValidationError,
)
from receipt_dynamo.entities.receipt_row import (
    ReceiptRow,
    item_to_receipt_row,
)

# DynamoDB batch_write_item can only handle up to 25 items per call
CHUNK_SIZE = 25


class _ReceiptRow(FlattenedStandardMixin):
    """
    A class providing methods to interact with "ReceiptRow" entities in
    DynamoDB. ReceiptRow materializes a receipt's visual rows (the
    ``group_lines_into_visual_rows`` grouping) as durable entities keyed by
    the row's primary line id.

    This class is typically used within a DynamoClient to access and manage
    receipt row records.

    Attributes
    ----------
    _client : boto3.client
        The Boto3 DynamoDB client (must be set externally).
    table_name : str
        The name of the DynamoDB table (must be set externally).

    Methods
    -------
    add_receipt_row(receipt_row: ReceiptRow):
        Adds a single ReceiptRow.
    add_receipt_rows(receipt_rows: list[ReceiptRow]):
        Adds multiple ReceiptRows.
    update_receipt_row(receipt_row: ReceiptRow):
        Updates a ReceiptRow.
    update_receipt_rows(receipt_rows: list[ReceiptRow]):
        Updates multiple ReceiptRows.
    delete_receipt_row(receipt_id: int, image_id: str, row_id: int):
        Deletes a single ReceiptRow by IDs.
    delete_receipt_rows(receipt_rows: list[ReceiptRow]):
        Deletes multiple ReceiptRows.
    get_receipt_row(receipt_id: int, image_id: str, row_id: int)
        -> ReceiptRow:
        Retrieves a single ReceiptRow by IDs.
    get_receipt_rows_from_receipt(image_id: str, receipt_id: int)
        -> list[ReceiptRow]:
        Retrieves all ReceiptRows for a given receipt.
    list_receipt_rows(...) -> tuple[list[ReceiptRow], dict | None]:
        Returns all ReceiptRows from the table with pagination.
    """

    @handle_dynamodb_errors("add_receipt_row")
    def add_receipt_row(self, receipt_row: ReceiptRow) -> None:
        """
        Adds a single ReceiptRow to DynamoDB.

        Parameters
        ----------
        receipt_row : ReceiptRow
            The ReceiptRow to add.

        Raises
        ------
        ValueError
            If the receipt_row already exists.
        """
        self._validate_entity(receipt_row, ReceiptRow, "receipt_row")
        self._add_entity(
            receipt_row, condition_expression="attribute_not_exists(PK)"
        )

    @handle_dynamodb_errors("add_receipt_rows")
    def add_receipt_rows(self, receipt_rows: list[ReceiptRow]) -> None:
        """
        Adds multiple ReceiptRows to DynamoDB in batches.

        Parameters
        ----------
        receipt_rows : list[ReceiptRow]
            The ReceiptRows to add.

        Raises
        ------
        ValueError
            If receipt_rows is invalid.
        """
        self._validate_entity_list(receipt_rows, ReceiptRow, "receipt_rows")

        request_items = [
            WriteRequestTypeDef(PutRequest=PutRequestTypeDef(Item=r.to_item()))
            for r in receipt_rows
        ]
        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("update_receipt_row")
    def update_receipt_row(self, receipt_row: ReceiptRow) -> None:
        """
        Updates an existing ReceiptRow in DynamoDB.

        Parameters
        ----------
        receipt_row : ReceiptRow
            The ReceiptRow to update.

        Raises
        ------
        ValueError
            If the receipt_row does not exist.
        """
        self._validate_entity(receipt_row, ReceiptRow, "receipt_row")
        self._update_entity(
            receipt_row, condition_expression="attribute_exists(PK)"
        )

    @handle_dynamodb_errors("update_receipt_rows")
    def update_receipt_rows(self, receipt_rows: list[ReceiptRow]) -> None:
        """
        Updates multiple existing ReceiptRows in DynamoDB.

        Parameters
        ----------
        receipt_rows : list[ReceiptRow]
            The ReceiptRows to update.

        Raises
        ------
        ValueError
            If receipt_rows is invalid or if any receipt_row does not exist.
        """
        self._update_entities(receipt_rows, ReceiptRow, "receipt_rows")

    @handle_dynamodb_errors("delete_receipt_row")
    def delete_receipt_row(
        self, receipt_id: int, image_id: str, row_id: int
    ) -> None:
        """
        Deletes a single ReceiptRow by IDs.

        Parameters
        ----------
        receipt_id : int
            The receipt ID.
        image_id : str
            The image ID.
        row_id : int
            The row ID (the row's primary line id).

        Raises
        ------
        ValueError
            If the row is not found.
        """
        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {"S": f"RECEIPT#{receipt_id:05d}#ROW#{row_id:05d}"},
                },
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            if (
                e.response["Error"]["Code"]
                == "ConditionalCheckFailedException"
            ):
                raise EntityNotFoundError(
                    f"ReceiptRow with receipt_id {receipt_id}, "
                    f"image_id {image_id}, and row_id {row_id} "
                    "not found"
                ) from e

            raise

    @handle_dynamodb_errors("delete_receipt_rows")
    def delete_receipt_rows(self, receipt_rows: list[ReceiptRow]) -> None:
        """
        Deletes multiple ReceiptRows in batch.

        Parameters
        ----------
        receipt_rows : list[ReceiptRow]
            The ReceiptRows to delete.

        Raises
        ------
        ValueError
            If unable to delete receipt_rows.
        """
        self._validate_entity_list(receipt_rows, ReceiptRow, "receipt_rows")

        try:
            for i in range(0, len(receipt_rows), CHUNK_SIZE):
                chunk = receipt_rows[i : i + CHUNK_SIZE]
                request_items = [
                    WriteRequestTypeDef(
                        DeleteRequest=DeleteRequestTypeDef(Key=r.key)
                    )
                    for r in chunk
                ]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    response = self._client.batch_write_item(
                        RequestItems=unprocessed
                    )
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            raise EntityValidationError(
                "Could not delete ReceiptRows from the database"
            ) from e

    @handle_dynamodb_errors("get_receipt_row")
    def get_receipt_row(
        self, receipt_id: int, image_id: str, row_id: int
    ) -> ReceiptRow:
        """
        Retrieves a single ReceiptRow by IDs.

        Parameters
        ----------
        receipt_id : int
            The receipt ID.
        image_id : str
            The image ID.
        row_id : int
            The row ID (the row's primary line id).

        Returns
        -------
        ReceiptRow
            The retrieved ReceiptRow.

        Raises
        ------
        ValueError
            If the row is not found.
        """
        result = self._get_entity(
            primary_key=f"IMAGE#{image_id}",
            sort_key=f"RECEIPT#{receipt_id:05d}#ROW#{row_id:05d}",
            entity_class=ReceiptRow,
            converter_func=item_to_receipt_row,
        )

        if result is None:
            raise EntityNotFoundError(
                f"ReceiptRow with receipt_id {receipt_id}, "
                f"image_id {image_id}, and row_id {row_id} "
                "not found"
            )

        return result

    @handle_dynamodb_errors("get_receipt_rows_from_receipt")
    def get_receipt_rows_from_receipt(
        self, image_id: str, receipt_id: int
    ) -> list[ReceiptRow]:
        """
        Retrieves all ReceiptRows for a given receipt.

        Parameters
        ----------
        image_id : str
            The image ID.
        receipt_id : int
            The receipt ID.

        Returns
        -------
        list[ReceiptRow]
            List of ReceiptRows for the receipt, in SK (row_id) order.

        Raises
        ------
        ValueError
            If parameters are invalid or query fails.
        """
        if image_id is None:
            raise EntityValidationError("image_id is required")
        if receipt_id is None:
            raise EntityValidationError("receipt_id is required")
        try:
            expected_pk = f"IMAGE#{image_id}"
            start_of_sk = f"RECEIPT#{receipt_id:05d}#ROW#"
            rows: list[ReceiptRow] = []
            last_evaluated_key = None
            while True:
                query_kwargs = {
                    "TableName": self.table_name,
                    "KeyConditionExpression": (
                        "PK = :pk and begins_with(SK, :sk)"
                    ),
                    "ExpressionAttributeValues": {
                        ":pk": {"S": expected_pk},
                        ":sk": {"S": start_of_sk},
                    },
                    # Strongly consistent for parity with
                    # get_receipt_sections_from_receipt: callers reconcile
                    # sections against rows immediately after writes.
                    "ConsistentRead": True,
                }
                if last_evaluated_key is not None:
                    query_kwargs["ExclusiveStartKey"] = last_evaluated_key
                response = self._client.query(**query_kwargs)
                rows.extend(
                    item_to_receipt_row(item) for item in response["Items"]
                )
                last_evaluated_key = response.get("LastEvaluatedKey")
                if last_evaluated_key is None:
                    return rows
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ProvisionedThroughputExceededException":
                raise EntityValidationError(
                    f"Provisioned throughput exceeded: {e}"
                ) from e

            raise EntityValidationError(
                f"Could not get ReceiptRows from DynamoDB: {e}"
            ) from e

    @handle_dynamodb_errors("list_receipt_rows")
    def list_receipt_rows(
        self,
        limit: int | None = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[ReceiptRow], dict | None]:
        """
        Returns all ReceiptRows from the table with optional pagination.

        Parameters
        ----------
        limit : int, optional
            Maximum number of items to return.
        last_evaluated_key : dict, optional
            Key to continue pagination from.

        Returns
        -------
        tuple[list[ReceiptRow], dict | None]
            List of ReceiptRows and last evaluated key for pagination.

        Raises
        ------
        ValueError
            If parameters are invalid.
        """
        if limit is not None and not isinstance(limit, int):
            raise EntityValidationError("limit must be an integer or None.")
        if last_evaluated_key is not None and not isinstance(
            last_evaluated_key, dict
        ):
            raise EntityValidationError(
                "last_evaluated_key must be a dictionary or None."
            )

        return self._query_by_type(
            entity_type="RECEIPT_ROW",
            converter_func=item_to_receipt_row,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )
