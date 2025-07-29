from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from receipt_dynamo.data.base_operations import (
    BatchOperationsMixin,
    DynamoDBBaseOperations,
    PutRequestTypeDef,
    QueryInputTypeDef,
    SingleEntityCRUDMixin,
    WriteRequestTypeDef,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    EntityValidationError,
)
from receipt_dynamo.entities import item_to_receipt_validation_summary
from receipt_dynamo.entities.receipt_validation_summary import (
    ReceiptValidationSummary,
)
from receipt_dynamo.entities.util import assert_valid_uuid

if TYPE_CHECKING:
    pass


class _ReceiptValidationSummary(
    DynamoDBBaseOperations, SingleEntityCRUDMixin, BatchOperationsMixin
):
    """
    A class used to access receipt validation summaries in DynamoDB.

    Methods
    -------
    add_receipt_validation_summary(summary: ReceiptValidationSummary)
        Adds a ReceiptValidationSummary to DynamoDB.
    update_receipt_validation_summary(summary: ReceiptValidationSummary)
        Updates an existing ReceiptValidationSummary in the database.
    update_receipt_validation_summaries(summaries:
            list[ReceiptValidationSummary])
        Updates multiple ReceiptValidationSummaries in the database.
    delete_receipt_validation_summary(summary: ReceiptValidationSummary)
        Deletes a single ReceiptValidationSummary.
    get_receipt_validation_summary(
        receipt_id: int,
        image_id: str
    ) -> ReceiptValidationSummary:
        Retrieves a single ReceiptValidationSummary by IDs.
    list_receipt_validation_summaries(
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None
    ) -> tuple[list[ReceiptValidationSummary], dict | None]:
        Returns ReceiptValidationSummaries and the last evaluated key.
    list_receipt_validation_summaries_by_status(
        status: str,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None
    ) -> tuple[list[ReceiptValidationSummary], dict | None]:
        Returns ReceiptValidationSummaries with a specific status.
    """

    @handle_dynamodb_errors("add_receipt_validation_summary")
    def add_receipt_validation_summary(
        self, summary: ReceiptValidationSummary
    ):
        """Adds a ReceiptValidationSummary to DynamoDB.

        Args:
            summary (ReceiptValidationSummary): The ReceiptValidationSummary
                to add.

        Raises:
            ValueError: If the summary is None or not an instance of
                ReceiptValidationSummary.
            Exception: If the summary cannot be added to DynamoDB.
        """
        self._validate_entity(summary, ReceiptValidationSummary, "summary")
        self._add_entity(
            summary,
            condition_expression=(
                "attribute_not_exists(PK) AND attribute_not_exists(SK)"
            ),
        )

    @handle_dynamodb_errors("update_receipt_validation_summary")
    def update_receipt_validation_summary(
        self, summary: ReceiptValidationSummary
    ):
        """Updates an existing ReceiptValidationSummary in the database.

        Args:
            summary (ReceiptValidationSummary): The ReceiptValidationSummary
                to update.

        Raises:
            ValueError: If the summary is None or not an instance of
                ReceiptValidationSummary.
            Exception: If the summary cannot be updated in DynamoDB.
        """
        self._validate_entity(summary, ReceiptValidationSummary, "summary")
        self._update_entity(
            summary,
            condition_expression=(
                "attribute_exists(PK) AND attribute_exists(SK)"
            ),
        )

    @handle_dynamodb_errors("update_receipt_validation_summaries")
    def update_receipt_validation_summaries(
        self, summaries: List[ReceiptValidationSummary]
    ):
        """Updates multiple ReceiptValidationSummaries in the database.

        Args:
            summaries (list[ReceiptValidationSummary]): The
                ReceiptValidationSummaries to update.

        Raises:
            ValueError: If the summaries are None or not a list.
            Exception: If the summaries cannot be updated in DynamoDB.
        """
        self._validate_entity_list(
            summaries, ReceiptValidationSummary, "summaries"
        )

        request_items = [
            WriteRequestTypeDef(
                PutRequest=PutRequestTypeDef(Item=summary.to_item())
            )
            for summary in summaries
        ]

        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("delete_receipt_validation_summary")
    def delete_receipt_validation_summary(
        self, summary: ReceiptValidationSummary
    ):
        """Deletes a single ReceiptValidationSummary.

        Args:
            summary (ReceiptValidationSummary): The ReceiptValidationSummary
                to delete.

        Raises:
            ValueError: If the summary is None or not an instance of
                ReceiptValidationSummary.
            Exception: If the summary cannot be deleted from DynamoDB.
        """
        self._validate_entity(summary, ReceiptValidationSummary, "summary")

        self._delete_entity(summary, condition_expression=None)

    @handle_dynamodb_errors("get_receipt_validation_summary")
    def get_receipt_validation_summary(
        self, receipt_id: int, image_id: str
    ) -> ReceiptValidationSummary:
        """Retrieves a single ReceiptValidationSummary by IDs.

        Args:
            receipt_id (int): The Receipt ID to query.
            image_id (str): The Image ID to query.

        Returns:
            ReceiptValidationSummary: The retrieved ReceiptValidationSummary.

        Raises:
            ValueError: If the IDs are invalid.
            Exception: If the ReceiptValidationSummary cannot be retrieved
                from DynamoDB.
        """
        if not isinstance(receipt_id, int):
            raise EntityValidationError(
                f"receipt_id must be an integer, got "
                f"{type(receipt_id).__name__}"
            )
        if not isinstance(image_id, str):
            raise EntityValidationError(
                f"image_id must be a string, got {type(image_id).__name__}"
            )

        try:
            assert_valid_uuid(image_id)
        except ValueError as e:
            raise EntityValidationError(f"Invalid image_id format: {e}") from e

        result = self._get_entity(
            primary_key=f"IMAGE#{image_id}",
            sort_key=f"RECEIPT#{receipt_id:05d}#ANALYSIS#VALIDATION",
            entity_class=ReceiptValidationSummary,
            converter_func=item_to_receipt_validation_summary,
        )

        if result is None:
            raise EntityNotFoundError(
                f"ReceiptValidationSummary for receipt {receipt_id} and "
                f"image {image_id} does not exist"
            )

        return result

    @handle_dynamodb_errors("list_receipt_validation_summaries")
    def list_receipt_validation_summaries(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict] = None,
    ) -> Tuple[List[ReceiptValidationSummary], Optional[Dict]]:
        """Returns ReceiptValidationSummaries and the last evaluated key.

        Args:
            limit (Optional[int], optional): The maximum number of items to
                return. Defaults to None.
            last_evaluated_key (Optional[Dict], optional): The key to start
                from for pagination. Defaults to None.

        Returns:
            tuple[list[ReceiptValidationSummary], dict | None]: A tuple
                containing the list of ReceiptValidationSummaries and the
                last evaluated key for pagination.

        Raises:
            ValueError: If the limit or last_evaluated_key are invalid.
            Exception: If the ReceiptValidationSummaries cannot be retrieved
                from DynamoDB.
        """
        if limit is not None and not isinstance(limit, int):
            raise EntityValidationError("limit must be an integer or None")
        if last_evaluated_key is not None and not isinstance(
            last_evaluated_key, dict
        ):
            raise EntityValidationError(
                "last_evaluated_key must be a dictionary or None"
            )

        return self._query_entities(
            index_name="GSITYPE",
            key_condition_expression="#t = :val",
            expression_attribute_names={"#t": "TYPE"},
            expression_attribute_values={
                ":val": {"S": "RECEIPT_VALIDATION_SUMMARY"}
            },
            converter_func=item_to_receipt_validation_summary,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("list_receipt_validation_summaries_by_status")
    def list_receipt_validation_summaries_by_status(
        self,
        status: str,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict] = None,
    ) -> Tuple[List[ReceiptValidationSummary], Optional[Dict]]:
        """Returns ReceiptValidationSummaries with a specific status.

        Args:
            status (str): The status to filter by.
            limit (Optional[int], optional): The maximum number of items to
                return. Defaults to None.
            last_evaluated_key (Optional[Dict], optional): The key to start
                from for pagination. Defaults to None.

        Returns:
            tuple[list[ReceiptValidationSummary], dict | None]: A tuple
                containing the list of ReceiptValidationSummaries and the
                last evaluated key for pagination.

        Raises:
            ValueError: If the parameters are invalid.
            Exception: If the ReceiptValidationSummaries cannot be retrieved
                from DynamoDB.
        """
        if not isinstance(status, str):
            raise EntityValidationError(
                f"status must be a string, got {type(status).__name__}"
            )
        if limit is not None and not isinstance(limit, int):
            raise EntityValidationError("limit must be an integer or None")
        if last_evaluated_key is not None and not isinstance(
            last_evaluated_key, dict
        ):
            raise EntityValidationError(
                "last_evaluated_key must be a dictionary or None"
            )

        return self._query_entities(
            index_name="GSI2",
            key_condition_expression="#gsi2pk = :pk",
            expression_attribute_names={"#gsi2pk": "GSI2PK"},
            expression_attribute_values={
                ":pk": {"S": f"VALIDATION_SUMMARY_STATUS#{status}"}
            },
            converter_func=item_to_receipt_validation_summary,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )
