from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from botocore.exceptions import ClientError

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.data.base_operations import (
    CommonValidationMixin,
    DynamoDBBaseOperations,
    FlattenedStandardMixin,
    PutRequestTypeDef,
    TransactionalOperationsMixin,
    WriteRequestTypeDef,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    BatchOperationError,
    EntityNotFoundError,
    EntityValidationError,
    OperationError,
)
from receipt_dynamo.entities.completion_batch_result import (
    CompletionBatchResult,
    item_to_completion_batch_result,
)

if TYPE_CHECKING:
    from receipt_dynamo.data.base_operations import (
        QueryInputTypeDef,
    )


def validate_last_evaluated_key(lek: Dict[str, Any]) -> None:
    """Validate that a LastEvaluatedKey has the required DynamoDB format.

    Args:
        lek: The LastEvaluatedKey dictionary to validate.

    Raises:
        ValueError: If the key format is invalid or missing required fields.
    """
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


class _CompletionBatchResult(
    DynamoDBBaseOperations,
    FlattenedStandardMixin,
):
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
        self, results: List[CompletionBatchResult]
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
        label: str,
    ) -> CompletionBatchResult:
        result = self._get_entity(
            primary_key=f"BATCH#{batch_id}",
            sort_key=(
                f"RESULT#RECEIPT#{receipt_id}#LINE#{line_id}"
                f"#WORD#{word_id}#LABEL#{label}"
            ),
            entity_class=CompletionBatchResult,
            converter_func=item_to_completion_batch_result,
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
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[CompletionBatchResult], Optional[dict]]:
        if limit is not None and (not isinstance(limit, int) or limit <= 0):
            raise EntityValidationError("limit must be a positive integer.")
        return self._query_by_type(
            entity_type="COMPLETION_BATCH_RESULT",
            converter_func=item_to_completion_batch_result,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("get_completion_batch_results_by_status")
    def get_completion_batch_results_by_status(
        self,
        status: str,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[CompletionBatchResult], Optional[dict]]:
        if status not in [s.value for s in ValidationStatus]:
            raise EntityValidationError("Invalid status.")
        if last_evaluated_key:
            validate_last_evaluated_key(last_evaluated_key)

        return self._query_entities(
            index_name="GSI2",
            key_condition_expression="GSI2SK = :val",
            expression_attribute_names=None,
            expression_attribute_values={":val": {"S": f"STATUS#{status}"}},
            converter_func=item_to_completion_batch_result,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("get_completion_batch_results_by_label_target")
    def get_completion_batch_results_by_label_target(
        self,
        label_target: str,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[CompletionBatchResult], Optional[dict]]:
        if not isinstance(label_target, str):
            raise EntityValidationError("label_target must be a string.")
        if last_evaluated_key:
            validate_last_evaluated_key(last_evaluated_key)

        return self._query_entities(
            index_name="GSI1",
            key_condition_expression="GSI1PK = :pk",
            expression_attribute_names=None,
            expression_attribute_values={
                ":pk": {"S": f"LABEL_TARGET#{label_target}"}
            },
            converter_func=item_to_completion_batch_result,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("get_completion_batch_results_by_receipt")
    def get_completion_batch_results_by_receipt(
        self,
        receipt_id: int,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[CompletionBatchResult], Optional[dict]]:
        if not isinstance(receipt_id, int) or receipt_id <= 0:
            raise EntityValidationError(
                "receipt_id must be a positive integer"
            )
        if last_evaluated_key:
            validate_last_evaluated_key(last_evaluated_key)

        return self._query_entities(
            index_name="GSI3",
            key_condition_expression="GSI3PK = :pk",
            expression_attribute_names=None,
            expression_attribute_values={
                ":pk": {"S": f"RECEIPT#{receipt_id}"}
            },
            converter_func=item_to_completion_batch_result,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )
