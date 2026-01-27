"""
Data operations for ReceiptSummaryRecord entities in DynamoDB.

ReceiptSummaryRecord stores pre-computed summary fields from
ReceiptWordLabel records for efficient querying. Queries are done by
listing all summaries via the TYPE GSI and filtering in memory.

This module provides CRUD operations for managing ReceiptSummaryRecord
records in DynamoDB.
"""

from receipt_dynamo.data.base_operations import (
    DeleteTypeDef,
    FlattenedStandardMixin,
    PutRequestTypeDef,
    TransactWriteItemTypeDef,
    WriteRequestTypeDef,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
)
from receipt_dynamo.entities.receipt_summary_record import (
    ReceiptSummaryRecord,
    item_to_receipt_summary_record,
)


class _ReceiptSummary(FlattenedStandardMixin):
    """
    Data operations for ReceiptSummaryRecord entities in DynamoDB.

    ReceiptSummaryRecord stores pre-computed fields from ReceiptWordLabel
    records including grand_total, tax, subtotal, date, and item_count. Use
    list_receipt_summaries() to get all summaries via the TYPE GSI,
    then filter in memory for flexibility.

    This class provides methods to interact with ReceiptSummaryRecord
    entities, supporting:
    - CRUD operations (add, update, delete, get)
    - Listing all summaries via TYPE GSI
    - Batch operations for efficiency

    Attributes
    ----------
    _client : boto3.client
        The Boto3 DynamoDB client (must be set externally).
    table_name : str
        The name of the DynamoDB table (must be set externally).

    Methods
    -------
    add_receipt_summary(summary: ReceiptSummaryRecord):
        Adds a single summary item to the database.
    add_receipt_summaries(summaries: list[ReceiptSummaryRecord]):
        Adds multiple summary items in batches.
    update_receipt_summary(summary: ReceiptSummaryRecord):
        Updates an existing summary item.
    delete_receipt_summary(summary: ReceiptSummaryRecord):
        Deletes a single summary item.
    delete_receipt_summaries(summaries: list[ReceiptSummaryRecord]):
        Deletes multiple summary items using transactions.
    get_receipt_summary(image_id: str, receipt_id: int):
        Retrieves a single summary item by indices.
    list_receipt_summaries(limit, last_evaluated_key):
        Lists all summary records via TYPE GSI with pagination.
    """

    @handle_dynamodb_errors("add_receipt_summary")
    def add_receipt_summary(
        self, summary: ReceiptSummaryRecord
    ) -> None:
        """
        Adds a single ReceiptSummaryRecord to DynamoDB.

        Parameters
        ----------
        summary : ReceiptSummaryRecord
            The summary instance to add.

        Raises
        ------
        ValueError
            If summary is None, not a ReceiptSummaryRecord, or if the
            record already exists.
        """
        self._validate_entity(
            summary, ReceiptSummaryRecord, "summary"
        )
        self._add_entity(
            summary,
            condition_expression=(
                "attribute_not_exists(PK) and attribute_not_exists(SK)"
            ),
        )

    @handle_dynamodb_errors("add_receipt_summaries")
    def add_receipt_summaries(
        self, summaries: list[ReceiptSummaryRecord]
    ) -> None:
        """
        Adds multiple ReceiptSummaryRecord items to DynamoDB in batches.

        Parameters
        ----------
        summaries : list[ReceiptSummaryRecord]
            A list of summary instances to add.

        Raises
        ------
        ValueError
            If summaries is invalid or if an error occurs during batch write.
        """
        self._validate_entity_list(
            summaries, ReceiptSummaryRecord, "summaries"
        )

        request_items = [
            WriteRequestTypeDef(
                PutRequest=PutRequestTypeDef(Item=item.to_item())
            )
            for item in summaries
        ]
        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("update_receipt_summary")
    def update_receipt_summary(
        self, summary: ReceiptSummaryRecord
    ) -> None:
        """
        Updates an existing ReceiptSummaryRecord in DynamoDB.

        Parameters
        ----------
        summary : ReceiptSummaryRecord
            The summary instance to update.

        Raises
        ------
        ValueError
            If summary is invalid or if the record does not exist.
        """
        self._validate_entity(
            summary, ReceiptSummaryRecord, "summary"
        )
        self._update_entity(
            summary,
            condition_expression=(
                "attribute_exists(PK) and attribute_exists(SK)"
            ),
        )

    @handle_dynamodb_errors("delete_receipt_summary")
    def delete_receipt_summary(
        self, summary: ReceiptSummaryRecord
    ) -> None:
        """
        Deletes a single ReceiptSummaryRecord from DynamoDB.

        Parameters
        ----------
        summary : ReceiptSummaryRecord
            The summary instance to delete.

        Raises
        ------
        ValueError
            If summary is invalid.
        """
        self._validate_entity(
            summary, ReceiptSummaryRecord, "summary"
        )
        self._delete_entity(
            summary, condition_expression="attribute_exists(PK)"
        )

    @handle_dynamodb_errors("delete_receipt_summaries")
    def delete_receipt_summaries(
        self, summaries: list[ReceiptSummaryRecord]
    ) -> None:
        """
        Deletes multiple ReceiptSummaryRecord items from DynamoDB.

        Parameters
        ----------
        summaries : list[ReceiptSummaryRecord]
            A list of summary instances to delete.

        Raises
        ------
        ValueError
            If summaries is invalid or if any record does not exist.
        """
        self._validate_entity_list(
            summaries, ReceiptSummaryRecord, "summaries"
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
            for item in summaries
        ]
        self._transact_write_with_chunking(transact_items)

    @handle_dynamodb_errors("get_receipt_summary")
    def get_receipt_summary(
        self, image_id: str, receipt_id: int
    ) -> ReceiptSummaryRecord:
        """
        Retrieves a single ReceiptSummaryRecord from DynamoDB.

        Parameters
        ----------
        image_id : str
            The image_id of the summary record to retrieve.
        receipt_id : int
            The receipt_id of the summary record to retrieve.

        Returns
        -------
        ReceiptSummaryRecord
            The corresponding summary instance.

        Raises
        ------
        ValueError
            If parameters are invalid or if the record does not exist.
        """
        self._validate_image_id(image_id)
        self._validate_receipt_id(receipt_id)

        result = self._get_entity(
            primary_key=f"IMAGE#{image_id}",
            sort_key=f"RECEIPT#{receipt_id:05d}#SUMMARY",
            entity_class=ReceiptSummaryRecord,
            converter_func=item_to_receipt_summary_record,
        )

        if result is None:
            raise EntityNotFoundError(
                f"ReceiptSummaryRecord with image_id={image_id}, "
                f"receipt_id={receipt_id} does not exist"
            )

        return result

    @handle_dynamodb_errors("list_receipt_summaries")
    def list_receipt_summaries(
        self,
        limit: int | None = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[ReceiptSummaryRecord], dict | None]:
        """
        Lists ReceiptSummaryRecord items from DynamoDB with pagination.

        Uses the TYPE GSI (GSI2) to efficiently list all summary records.
        Filter in memory after retrieval for date ranges, merchants, etc.

        Parameters
        ----------
        limit : int, optional
            Maximum number of records to retrieve.
        last_evaluated_key : dict, optional
            The key to start pagination from.

        Returns
        -------
        tuple[list[ReceiptSummaryRecord], dict | None]
            A tuple containing the list of summary records and the last
            evaluated key for pagination.

        Raises
        ------
        ValueError
            If parameters are invalid.
        """
        self._validate_pagination_params(limit, last_evaluated_key)

        return self._query_by_type(
            entity_type="RECEIPT_SUMMARY",
            converter_func=item_to_receipt_summary_record,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("upsert_receipt_summary")
    def upsert_receipt_summary(
        self, summary: ReceiptSummaryRecord
    ) -> None:
        """
        Upserts a ReceiptSummaryRecord to DynamoDB.

        Creates the record if it doesn't exist, or updates it if it does.
        This is useful when re-computing summaries.

        Parameters
        ----------
        summary : ReceiptSummaryRecord
            The summary instance to upsert.

        Raises
        ------
        ValueError
            If summary is invalid.
        """
        self._validate_entity(
            summary, ReceiptSummaryRecord, "summary"
        )
        # Use put_item without condition - overwrites if exists
        self._client.put_item(
            TableName=self.table_name,
            Item=summary.to_item(),
        )

    @handle_dynamodb_errors("upsert_receipt_summaries")
    def upsert_receipt_summaries(
        self, summaries: list[ReceiptSummaryRecord]
    ) -> None:
        """
        Upserts multiple ReceiptSummaryRecord items to DynamoDB.

        Creates records if they don't exist, or updates them if they do.
        Uses batch write for efficiency.

        Parameters
        ----------
        summaries : list[ReceiptSummaryRecord]
            A list of summary instances to upsert.

        Raises
        ------
        ValueError
            If summaries is invalid or if an error occurs during batch write.
        """
        self._validate_entity_list(
            summaries, ReceiptSummaryRecord, "summaries"
        )

        # batch_write_item is idempotent - overwrites if exists
        request_items = [
            WriteRequestTypeDef(
                PutRequest=PutRequestTypeDef(Item=item.to_item())
            )
            for item in summaries
        ]
        self._batch_write_with_retry(request_items)
