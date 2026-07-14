from typing import TYPE_CHECKING, Any, cast

from receipt_dynamo.constants import BatchStatus
from receipt_dynamo.data.base_operations import (
    FlattenedStandardMixin,
    PutRequestTypeDef,
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
from receipt_dynamo.entities.completion_batch_result import (
    CompletionBatchResult,
    item_to_completion_batch_result,
)
from receipt_dynamo.entities.util import assert_valid_uuid

if TYPE_CHECKING:
    pass


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


def _validate_nonnegative_id(value: int, name: str) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise EntityValidationError(f"{name} must be a non-negative integer")


def _validate_original_label(original_label: str) -> None:
    if (
        not isinstance(original_label, str)
        or not original_label
        or "#" in original_label
    ):
        raise EntityValidationError(
            "label must be a non-empty string excluding '#'"
        )


class _CompletionBatchResult(FlattenedStandardMixin):
    """
    .. deprecated::
        This class is deprecated and not used in production. Consider removing
        if no longer needed for historical data access.
    """

    @handle_dynamodb_errors("add_completion_batch_result")
    def add_completion_batch_result(
        self, result: CompletionBatchResult
    ) -> None:
        """Add a new completion batch result to DynamoDB.

        Args:
            result: The CompletionBatchResult to add.

        Raises:
            EntityAlreadyExistsError: If the result already exists
            EntityValidationError: If result parameters are invalid
        """
        self._validate_entity(result, CompletionBatchResult, "result")
        self._add_entity(
            result, condition_expression="attribute_not_exists(PK)"
        )

    @handle_dynamodb_errors("add_completion_batch_results")
    def add_completion_batch_results(
        self, results: list[CompletionBatchResult]
    ) -> None:
        """Add multiple completion batch results to DynamoDB in batches.

        Args:
            results: List of CompletionBatchResult instances to add.

        Raises:
            ValueError: If results is None, empty, or contains invalid items.
            BatchOperationError: If any batch operation fails.
        """
        self._validate_entity_list(results, CompletionBatchResult, "results")
        request_items = [
            WriteRequestTypeDef(
                PutRequest=PutRequestTypeDef(Item=result.to_item())
            )
            for result in results
        ]
        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("update_completion_batch_result")
    def update_completion_batch_result(
        self, result: CompletionBatchResult
    ) -> None:
        self._validate_entity(result, CompletionBatchResult, "result")
        self._update_entity(
            result, condition_expression="attribute_exists(PK)"
        )

    @handle_dynamodb_errors("delete_completion_batch_result")
    def delete_completion_batch_result(
        self, result: CompletionBatchResult
    ) -> None:
        self._validate_entity(result, CompletionBatchResult, "result")
        self._delete_entity(
            result, condition_expression="attribute_exists(PK)"
        )

    @handle_dynamodb_errors("get_completion_batch_result")
    def get_completion_batch_result(
        self,
        batch_id: str,
        receipt_id: int,
        line_id: int,
        word_id: int,
        *,
        label: str,
    ) -> CompletionBatchResult:
        _validate_batch_id(batch_id)
        if (
            not isinstance(receipt_id, int)
            or isinstance(receipt_id, bool)
            or receipt_id <= 0
        ):
            raise EntityValidationError(
                "receipt_id must be a positive integer"
            )
        _validate_nonnegative_id(line_id, "line_id")
        _validate_nonnegative_id(word_id, "word_id")
        _validate_original_label(label)
        result = cast(
            CompletionBatchResult | None,
            self._get_entity(
                primary_key=f"BATCH#{batch_id}",
                sort_key=(
                    f"RESULT#RECEIPT#{receipt_id}#LINE#{line_id}"
                    f"#WORD#{word_id}#LABEL#{label}"
                ),
                entity_class=CompletionBatchResult,
                converter_func=item_to_completion_batch_result,
            ),
        )

        if result is None:
            raise EntityNotFoundError(
                f"Completion batch result with batch_id={batch_id}, "
                f"receipt_id={receipt_id}, line_id={line_id}, "
                f"word_id={word_id}, label={label} not found"
            )

        return result

    @handle_dynamodb_errors("list_completion_batch_results")
    def list_completion_batch_results(
        self,
        limit: int | None = None,
        last_evaluated_key: dict[str, Any] | None = None,
    ) -> tuple[list[CompletionBatchResult], dict[str, Any] | None]:
        _validate_pagination(limit, last_evaluated_key, ("TYPE",))
        return cast(
            tuple[list[CompletionBatchResult], dict[str, Any] | None],
            self._query_by_type(
                entity_type="COMPLETION_BATCH_RESULT",
                converter_func=item_to_completion_batch_result,
                limit=limit,
                last_evaluated_key=last_evaluated_key,
            ),
        )

    @handle_dynamodb_errors("get_completion_batch_results_by_status")
    def get_completion_batch_results_by_status(
        self,
        batch_id: str,
        status: str | BatchStatus,
        limit: int | None = None,
        last_evaluated_key: dict[str, Any] | None = None,
    ) -> tuple[list[CompletionBatchResult], dict[str, Any] | None]:
        _validate_batch_id(batch_id)
        if isinstance(status, BatchStatus):
            status = status.value
        if status not in [s.value for s in BatchStatus]:
            raise EntityValidationError("Invalid status.")
        _validate_pagination(limit, last_evaluated_key, ("GSI2PK", "GSI2SK"))

        return self._query_entities(
            index_name="GSI2",
            key_condition_expression="GSI2PK = :pk AND GSI2SK = :sk",
            expression_attribute_names=None,
            expression_attribute_values={
                ":pk": {"S": f"BATCH#{batch_id}"},
                ":sk": {"S": f"STATUS#{status}"},
            },
            converter_func=item_to_completion_batch_result,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("get_completion_batch_results_by_label_target")
    def get_completion_batch_results_by_label_target(
        self,
        label_target: str,
        limit: int | None = None,
        last_evaluated_key: dict[str, Any] | None = None,
    ) -> tuple[list[CompletionBatchResult], dict[str, Any] | None]:
        # The historical method name says "label target", but the entity's
        # GSI1 partition key represents its original label.
        _validate_original_label(label_target)
        _validate_pagination(limit, last_evaluated_key, ("GSI1PK", "GSI1SK"))

        return self._query_entities(
            index_name="GSI1",
            key_condition_expression="GSI1PK = :pk",
            expression_attribute_names=None,
            expression_attribute_values={
                ":pk": {"S": f"LABEL#{label_target}"}
            },
            converter_func=item_to_completion_batch_result,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("get_completion_batch_results_by_receipt")
    def get_completion_batch_results_by_receipt(
        self,
        image_id: str,
        receipt_id: int,
        limit: int | None = None,
        last_evaluated_key: dict[str, Any] | None = None,
    ) -> tuple[list[CompletionBatchResult], dict[str, Any] | None]:
        _validate_image_id(image_id)
        if (
            not isinstance(receipt_id, int)
            or isinstance(receipt_id, bool)
            or receipt_id <= 0
        ):
            raise EntityValidationError(
                "receipt_id must be a positive integer"
            )
        _validate_pagination(limit, last_evaluated_key, ("GSI3PK", "GSI3SK"))

        return self._query_entities(
            index_name="GSI3",
            key_condition_expression="GSI3PK = :pk",
            expression_attribute_names=None,
            expression_attribute_values={
                ":pk": {"S": (f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}")}
            },
            converter_func=item_to_completion_batch_result,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )
