"""
This module provides the _BatchSummary class for managing BatchSummary
records in DynamoDB. It includes operations for inserting, updating,
deleting, and querying batch summary data, including support for pagination
and GSI lookups by status.
"""

from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from receipt_dynamo.constants import BatchStatus, BatchType
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
from receipt_dynamo.entities.batch_summary import (
    BatchSummary,
    item_to_batch_summary,
)
from receipt_dynamo.entities.util import assert_valid_uuid

if TYPE_CHECKING:
    from receipt_dynamo.data.base_operations import (
        QueryInputTypeDef,
    )


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


class _BatchSummary(
    DynamoDBBaseOperations,
    FlattenedStandardMixin,
):

    @handle_dynamodb_errors("add_batch_summary")
    def add_batch_summary(self, batch_summary: BatchSummary) -> None:
        """
        Adds a single BatchSummary record to DynamoDB.

        Args:
            batch_summary (BatchSummary): The BatchSummary instance to add.

        Raises:
            ValueError: If batch_summary is None, not a BatchSummary,
            or if DynamoDB conditions fail.
        """
        self._validate_entity(batch_summary, BatchSummary, "batch_summary")
        self._add_entity(
            batch_summary,
            condition_expression=(
                "attribute_not_exists(PK) AND attribute_not_exists(SK)"
            ),
        )

    @handle_dynamodb_errors("add_batch_summaries")
    def add_batch_summaries(
        self,
        batch_summaries: List[BatchSummary],
    ) -> None:
        """
        Adds multiple BatchSummary records to DynamoDB in batches.

        Args:
            batch_summaries (List[BatchSummary]):
                A list of BatchSummary instances to add.

        Raises:
            ValueError: If batch_summaries is None, not a list, or
            contains invalid BatchSummary objects.
        """
        self._validate_entity_list(
            batch_summaries, BatchSummary, "batch_summaries"
        )
        request_items = [
            WriteRequestTypeDef(
                PutRequest=PutRequestTypeDef(Item=batch_summary.to_item())
            )
            for batch_summary in batch_summaries
        ]
        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("update_batch_summary")
    def update_batch_summary(self, batch_summary: BatchSummary) -> None:
        """
        Updates an existing BatchSummary record in DynamoDB.

        Args:
            batch_summary (BatchSummary): The BatchSummary instance to update.

        Raises:
            ValueError: If batch_summary is None, not a BatchSummary,
            or if the record does not exist.
        """
        self._validate_entity(batch_summary, BatchSummary, "batch_summary")
        self._update_entity(
            batch_summary,
            condition_expression=(
                "attribute_exists(PK) AND attribute_exists(SK)"
            ),
        )

    @handle_dynamodb_errors("update_batch_summaries")
    def update_batch_summaries(
        self, batch_summaries: List[BatchSummary]
    ) -> None:
        """
        Updates multiple BatchSummary records in DynamoDB using transactions.

        Args:
            batch_summaries (List[BatchSummary]):
                A list of BatchSummary instances to update.

        Raises:
            ValueError: If batch_summaries is None, not a list, or
            contains invalid BatchSummary objects.
        """
        self._validate_entity_list(
            batch_summaries, BatchSummary, "batch_summaries"
        )
        # Create transactional update items
        transact_items = [
            TransactWriteItemTypeDef(
                Put=PutTypeDef(
                    TableName=self.table_name,
                    Item=item.to_item(),
                    ConditionExpression=(
                        "attribute_exists(PK) AND attribute_exists(SK)"
                    ),
                )
            )
            for item in batch_summaries
        ]
        self._transact_write_with_chunking(transact_items)

    @handle_dynamodb_errors("delete_batch_summary")
    def delete_batch_summary(self, batch_summary: BatchSummary) -> None:
        """
        Deletes a single BatchSummary record from DynamoDB.

        Args:
            batch_summary (BatchSummary): The BatchSummary instance to delete.

        Raises:
            ValueError: If batch_summary is None, not a BatchSummary,
            or if the record does not exist.
        """
        self._validate_entity(batch_summary, BatchSummary, "batch_summary")
        self._delete_entity(
            batch_summary,
            condition_expression=(
                "attribute_exists(PK) AND attribute_exists(SK)"
            ),
        )

    @handle_dynamodb_errors("delete_batch_summaries")
    def delete_batch_summaries(
        self, batch_summaries: List[BatchSummary]
    ) -> None:
        """Deletes multiple BatchSummary records from DynamoDB using
        transactions.

        Args:
            batch_summaries (List[BatchSummary]):
                A list of BatchSummary instances to delete.

        Raises:
            ValueError: If batch_summaries is None, not a list, or
            contains invalid BatchSummary objects.
        """
        self._validate_entity_list(
            batch_summaries, BatchSummary, "batch_summaries"
        )
        # Create transactional delete items
        transact_items = [
            TransactWriteItemTypeDef(
                Delete=DeleteTypeDef(
                    TableName=self.table_name,
                    Key=item.key,
                    ConditionExpression=(
                        "attribute_exists(PK) AND attribute_exists(SK)"
                    ),
                )
            )
            for item in batch_summaries
        ]
        self._transact_write_with_chunking(transact_items)

    @handle_dynamodb_errors("get_batch_summary")
    def get_batch_summary(self, batch_id: str) -> BatchSummary:
        """
        Retrieves a BatchSummary record from DynamoDB by batch_id.

        Args:
            batch_id (str): The unique identifier for the batch.

        Returns:
            BatchSummary: The corresponding BatchSummary instance.

        Raises:
            ValueError: If batch_id is not a valid string, UUID, or if the
            record does not exist.
        """
        if not isinstance(batch_id, str):
            raise EntityValidationError("batch_id must be a string")
        assert_valid_uuid(batch_id)

        result = self._get_entity(
            primary_key=f"BATCH#{batch_id}",
            sort_key="STATUS",
            entity_class=BatchSummary,
            converter_func=item_to_batch_summary,
        )

        if result is None:
            raise EntityNotFoundError(
                f"BatchSummary with ID {batch_id} does not exist"
            )

        return result

    @handle_dynamodb_errors("list_batch_summaries")
    def list_batch_summaries(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> Tuple[
        List[BatchSummary],
        dict | None,
    ]:
        """
        Lists BatchSummary records from DynamoDB with optional pagination.

        Args:
            limit (int, optional): Maximum number of records to retrieve.
            last_evaluated_key (dict, optional):
                The key to start pagination from.

        Returns:
            Tuple[List[BatchSummary], dict | None]:
                A tuple containing the list of BatchSummary records and
                the last evaluated key.

        Raises:
            ValueError: If limit or last_evaluated_key are invalid.
        """
        if limit is not None and not isinstance(limit, int):
            raise EntityValidationError("limit must be an integer")
        if limit is not None and limit <= 0:
            raise EntityValidationError("limit must be greater than 0")
        return self._query_by_type(
            entity_type="BATCH_SUMMARY",
            converter_func=item_to_batch_summary,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("get_batch_summaries_by_status")
    def get_batch_summaries_by_status(
        self,
        status: str | BatchStatus,
        batch_type: str | BatchType = "EMBEDDING",
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> Tuple[
        List[BatchSummary],
        dict | None,
    ]:
        """
        Retrieves BatchSummary records filtered by status with optional
        pagination.

        Args:
            status (str): The status to filter by.
            limit (int, optional): Maximum number of records to retrieve.
            last_evaluated_key (dict, optional):
                The key to start pagination from.

        Returns:
            Tuple[List[BatchSummary], dict | None]:
                A tuple containing the list of BatchSummary records and
                the last evaluated key.

        Raises:
            ValueError: If status is invalid or if pagination parameters are
            invalid.
        """
        if isinstance(status, BatchStatus):
            status_str = status.value
        elif isinstance(status, str):
            status_str = status
        else:
            raise EntityValidationError(
                "status must be either a BatchStatus enum or a string; got"
                f" {type(status).__name__}"
            )
        valid_statuses = [s.value for s in BatchStatus]
        if status_str not in valid_statuses:
            raise EntityValidationError(
                "Invalid status: "
                f"{status_str} must be one of {', '.join(valid_statuses)}"
            )

        if isinstance(batch_type, BatchType):
            batch_type_str = batch_type.value
        elif isinstance(batch_type, str):
            batch_type_str = batch_type
        else:
            raise EntityValidationError(
                "batch_type must be either a BatchType enum or a string; got"
                f" {type(batch_type).__name__}"
            )

        # Validate batch_type_str against allowed values
        valid_types = [t.value for t in BatchType]
        if batch_type_str not in valid_types:
            raise EntityValidationError(
                "Invalid batch type: "
                f"{batch_type_str} must be one of {', '.join(valid_types)}"
            )

        if limit is not None and (not isinstance(limit, int) or limit <= 0):
            raise EntityValidationError("Limit must be a positive integer")
        return self._query_entities(
            index_name="GSI1",
            key_condition_expression=(
                "GSI1PK = :pk AND begins_with(GSI1SK, :prefix)"
            ),
            expression_attribute_names={"#batch_type": "batch_type"},
            expression_attribute_values={
                ":pk": {"S": f"STATUS#{status_str}"},
                ":prefix": {"S": f"BATCH_TYPE#{batch_type_str}"},
                ":batch_type_filter": {"S": batch_type_str},
            },
            converter_func=item_to_batch_summary,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
            filter_expression="#batch_type = :batch_type_filter",
        )
