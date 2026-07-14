from typing import Any, cast

from receipt_dynamo.constants import EmbeddingStatus
from receipt_dynamo.data.base_operations import (
    DeleteTypeDef,
    FlattenedStandardMixin,
    PutRequestTypeDef,
    PutTypeDef,
    TransactWriteItemTypeDef,
    WriteRequestTypeDef,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.base_operations.shared_utils import (
    validate_pagination_params,
)
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    EntityValidationError,
)
from receipt_dynamo.entities.embedding_batch_result import (
    EmbeddingBatchResult,
    item_to_embedding_batch_result,
)
from receipt_dynamo.entities.util import assert_valid_uuid


def _validate_batch_id(batch_id: str) -> None:
    """Validate a batch UUID using the data-layer exception contract."""
    try:
        assert_valid_uuid(batch_id)
    except ValueError as exc:
        raise EntityValidationError("batch_id must be a valid UUIDv4") from exc


def _validate_image_id(image_id: str) -> None:
    """Validate an image UUID using the data-layer exception contract."""
    try:
        assert_valid_uuid(image_id)
    except ValueError as exc:
        raise EntityValidationError("image_id must be a valid UUIDv4") from exc


def _validate_pagination(
    limit: int | None,
    last_evaluated_key: dict[str, Any] | None,
    required_index_keys: tuple[str, ...],
) -> None:
    """Validate query pagination without accepting bool as an integer."""
    if isinstance(limit, bool):
        raise EntityValidationError("limit must be an integer")
    validate_pagination_params(
        limit,
        last_evaluated_key,
        validate_attribute_format=True,
    )
    if last_evaluated_key is not None:
        for key in required_index_keys:
            value = last_evaluated_key.get(key)
            if not isinstance(value, dict) or "S" not in value:
                raise EntityValidationError(
                    f"last_evaluated_key[{key}] must be a dict "
                    "containing a key 'S'"
                )


class _EmbeddingBatchResult(
    FlattenedStandardMixin,
):
    """DynamoDB accessor for EmbeddingBatchResult items.

    .. deprecated::
        This class is deprecated and not used in production. Consider removing
        if no longer needed for historical data access.
    """

    @handle_dynamodb_errors("add_embedding_batch_result")
    def add_embedding_batch_result(
        self, embedding_batch_result: EmbeddingBatchResult
    ):
        """
        Adds an EmbeddingBatchResult to the database.

        Raises:
            EntityAlreadyExistsError: If the embedding batch result already
                exists
            EntityValidationError: If embedding_batch_result parameters are
                invalid
        """
        self._validate_entity(
            embedding_batch_result,
            EmbeddingBatchResult,
            "embedding_batch_result",
        )
        self._add_entity(
            embedding_batch_result,
            condition_expression="attribute_not_exists(PK)",
        )

    @handle_dynamodb_errors("add_embedding_batch_results")
    def add_embedding_batch_results(
        self, embedding_batch_results: list[EmbeddingBatchResult]
    ) -> None:
        """
        Batch add EmbeddingBatchResults to DynamoDB.

        Raises:
            EntityValidationError: If embedding_batch_results parameters
                are invalid
        """
        self._validate_entity_list(
            embedding_batch_results,
            EmbeddingBatchResult,
            "embedding_batch_results",
        )
        request_items = [
            WriteRequestTypeDef(
                PutRequest=PutRequestTypeDef(Item=result.to_item())
            )
            for result in embedding_batch_results
        ]
        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("update_embedding_batch_result")
    def update_embedding_batch_result(
        self, embedding_batch_result: EmbeddingBatchResult
    ):
        """
        Updates an EmbeddingBatchResult in DynamoDB.

        Raises:
            EntityNotFoundError: If the embedding batch result does not exist
            EntityValidationError: If embedding_batch_result parameters are
                invalid
        """
        self._validate_entity(
            embedding_batch_result,
            EmbeddingBatchResult,
            "embedding_batch_result",
        )
        self._update_entity(
            embedding_batch_result, condition_expression="attribute_exists(PK)"
        )

    @handle_dynamodb_errors("update_embedding_batch_results")
    def update_embedding_batch_results(
        self, embedding_batch_results: list[EmbeddingBatchResult]
    ):
        """
        Batch update EmbeddingBatchResults in DynamoDB.
        """
        if embedding_batch_results is None:
            raise EntityValidationError(
                "EmbeddingBatchResults parameter is required and cannot be "
                "None."
            )
        if not isinstance(embedding_batch_results, list):
            raise EntityValidationError(
                "embedding_batch_results must be a list of "
                "EmbeddingBatchResult instances."
            )
        if not all(
            isinstance(r, EmbeddingBatchResult)
            for r in embedding_batch_results
        ):
            raise EntityValidationError(
                "All embedding batch results must be instances of "
                "EmbeddingBatchResult."
            )

        for i in range(0, len(embedding_batch_results), 25):
            chunk = embedding_batch_results[i : i + 25]
            transact_items = [
                TransactWriteItemTypeDef(
                    Put=PutTypeDef(
                        TableName=self.table_name,
                        Item=r.to_item(),
                        ConditionExpression="attribute_exists(PK)",
                    )
                )
                for r in chunk
            ]
            self._transact_write_with_chunking(transact_items)

    @handle_dynamodb_errors("delete_embedding_batch_result")
    def delete_embedding_batch_result(
        self, embedding_batch_result: EmbeddingBatchResult
    ):
        """
        Deletes an EmbeddingBatchResult from DynamoDB.

        Raises:
            EntityNotFoundError: If the embedding batch result does not exist
            EntityValidationError: If embedding_batch_result parameters are
                invalid
        """
        self._validate_entity(
            embedding_batch_result,
            EmbeddingBatchResult,
            "embedding_batch_result",
        )
        self._delete_entity(
            embedding_batch_result, condition_expression="attribute_exists(PK)"
        )

    @handle_dynamodb_errors("delete_embedding_batch_results")
    def delete_embedding_batch_results(
        self, embedding_batch_results: list[EmbeddingBatchResult]
    ):
        """
        Batch delete EmbeddingBatchResults from DynamoDB.

        Raises:
            EntityValidationError: If embedding_batch_results parameters
                are invalid
        """
        self._validate_entity_list(
            embedding_batch_results,
            EmbeddingBatchResult,
            "embedding_batch_results",
        )
        # Create transactional delete items
        transact_items = [
            TransactWriteItemTypeDef(
                Delete=DeleteTypeDef(
                    TableName=self.table_name,
                    Key=result.key,
                    ConditionExpression="attribute_exists(PK)",
                )
            )
            for result in embedding_batch_results
        ]
        self._transact_write_with_chunking(transact_items)

    @handle_dynamodb_errors("get_embedding_batch_result")
    def get_embedding_batch_result(
        self,
        batch_id: str,
        image_id: str,
        receipt_id: int,
        line_id: int,
        *,
        word_id: int,
    ) -> EmbeddingBatchResult:
        """
        Gets an EmbeddingBatchResult from DynamoDB by primary key.
        """
        _validate_batch_id(batch_id)
        _validate_image_id(image_id)
        if (
            not isinstance(receipt_id, int)
            or isinstance(receipt_id, bool)
            or receipt_id <= 0
        ):
            raise EntityValidationError(
                "receipt_id must be a positive integer"
            )
        if (
            not isinstance(line_id, int)
            or isinstance(line_id, bool)
            or line_id < 0
        ):
            raise EntityValidationError(
                "line_id must be zero or positive integer"
            )
        if (
            not isinstance(word_id, int)
            or isinstance(word_id, bool)
            or word_id < 0
        ):
            raise EntityValidationError(
                "word_id must be zero or positive integer"
            )

        result = cast(
            EmbeddingBatchResult | None,
            self._get_entity(
                primary_key=f"BATCH#{batch_id}",
                sort_key=(
                    f"RESULT#IMAGE#{image_id}"
                    f"#RECEIPT#{receipt_id:05d}"
                    f"#LINE#{line_id:03d}#WORD#{word_id:03d}"
                ),
                entity_class=EmbeddingBatchResult,
                converter_func=item_to_embedding_batch_result,
            ),
        )

        if result is None:
            raise EntityNotFoundError(
                "Embedding batch result for Batch ID "
                f"'{batch_id}', Image ID {image_id}, "
                f"Receipt ID {receipt_id}, Line ID {line_id}, "
                f"Word ID {word_id} does not exist."
            )

        return result

    @handle_dynamodb_errors("list_embedding_batch_results")
    def list_embedding_batch_results(
        self,
        limit: int | None = None,
        last_evaluated_key: dict[str, Any] | None = None,
    ) -> tuple[list[EmbeddingBatchResult], dict[str, Any] | None]:
        """
        List all EmbeddingBatchResults, paginated.
        """
        _validate_pagination(limit, last_evaluated_key, ("TYPE",))
        return cast(
            tuple[list[EmbeddingBatchResult], dict[str, Any] | None],
            self._query_by_type(
                entity_type="EMBEDDING_BATCH_RESULT",
                converter_func=item_to_embedding_batch_result,
                limit=limit,
                last_evaluated_key=last_evaluated_key,
            ),
        )

    @handle_dynamodb_errors("get_embedding_batch_results_by_status")
    def get_embedding_batch_results_by_status(
        self,
        batch_id: str,
        status: str | EmbeddingStatus,
        limit: int | None = None,
        last_evaluated_key: dict[str, Any] | None = None,
    ) -> tuple[list[EmbeddingBatchResult], dict[str, Any] | None]:
        """
        Query one batch's EmbeddingBatchResults by status using GSI2.

        GSI2 is partitioned by batch ID, so a status-only query is not a
        valid DynamoDB key condition.
        """
        _validate_batch_id(batch_id)
        if isinstance(status, EmbeddingStatus):
            status = status.value
        if not isinstance(status, str) or not status:
            raise EntityValidationError("Status must be a non-empty string")
        if status not in [s.value for s in EmbeddingStatus]:
            raise EntityValidationError(
                "Status must be one of: "
                + ", ".join(s.value for s in EmbeddingStatus)
            )
        _validate_pagination(limit, last_evaluated_key, ("GSI2PK", "GSI2SK"))
        return self._query_entities(
            index_name="GSI2",
            key_condition_expression="GSI2PK = :pk AND GSI2SK = :sk",
            expression_attribute_names=None,
            expression_attribute_values={
                ":pk": {"S": f"BATCH#{batch_id}"},
                ":sk": {"S": f"STATUS#{status}"},
            },
            converter_func=item_to_embedding_batch_result,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("get_embedding_batch_results_by_receipt")
    def get_embedding_batch_results_by_receipt(
        self,
        image_id: str,
        receipt_id: int,
        limit: int | None = None,
        last_evaluated_key: dict[str, Any] | None = None,
    ) -> tuple[list[EmbeddingBatchResult], dict[str, Any] | None]:
        """
        Query EmbeddingBatchResults by receipt_id using GSI3.
        """
        _validate_image_id(image_id)
        if (
            not isinstance(receipt_id, int)
            or isinstance(receipt_id, bool)
            or receipt_id <= 0
        ):
            raise EntityValidationError(
                "receipt_id must be a positive integer."
            )
        _validate_pagination(limit, last_evaluated_key, ("GSI3PK", "GSI3SK"))

        return self._query_entities(
            index_name="GSI3",
            key_condition_expression="GSI3PK = :pk",
            expression_attribute_names=None,
            expression_attribute_values={
                ":pk": {"S": (f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}")}
            },
            converter_func=item_to_embedding_batch_result,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )
