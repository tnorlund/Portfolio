from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from receipt_dynamo.data.base_operations import (
    BatchOperationsMixin,
    DeleteRequestTypeDef,
    DynamoDBBaseOperations,
    PutRequestTypeDef,
    SingleEntityCRUDMixin,
    TransactionalOperationsMixin,
    WriteRequestTypeDef,
    handle_dynamodb_errors,
)
from receipt_dynamo.entities import item_to_receipt_validation_result
from receipt_dynamo.entities.receipt_validation_result import (
    ReceiptValidationResult,
)
from receipt_dynamo.entities.util import assert_valid_uuid
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    EntityValidationError,
)

if TYPE_CHECKING:
    from receipt_dynamo.data.base_operations import QueryInputTypeDef


class _ReceiptValidationResult(
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    BatchOperationsMixin,
    TransactionalOperationsMixin,
):
    """
    A class used to access receipt validation results in DynamoDB.

    Methods
    -------
    add_receipt_validation_result(result: ReceiptValidationResult)
        Adds a ReceiptValidationResult to DynamoDB.
    add_receipt_validation_results(results: list[ReceiptValidationResult])
        Adds multiple ReceiptValidationResults to DynamoDB in batches.
    update_receipt_validation_result(result: ReceiptValidationResult)
        Updates an existing ReceiptValidationResult in the database.
    update_receipt_validation_results(results: list[ReceiptValidationResult])
        Updates multiple ReceiptValidationResults in the database.
    delete_receipt_validation_result(result: ReceiptValidationResult)
        Deletes a single ReceiptValidationResult.
    delete_receipt_validation_results(results: list[ReceiptValidationResult])
        Deletes multiple ReceiptValidationResults in batch.
    get_receipt_validation_result(
        receipt_id: int,
        image_id: str,
        field_name: str,
        result_index: int
    ) -> ReceiptValidationResult:
        Retrieves a single ReceiptValidationResult by IDs.
    list_receipt_validation_results(
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None
    ) -> tuple[list[ReceiptValidationResult], dict | None]:
        Returns ReceiptValidationResults and the last evaluated key.
    list_receipt_validation_results_for_field(
        receipt_id: int,
        image_id: str,
        field_name: str,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None
    ) -> tuple[list[ReceiptValidationResult], dict | None]:
        Returns ReceiptValidationResults for a specific field.
    list_receipt_validation_results_by_type(
        result_type: str,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None
    ) -> tuple[list[ReceiptValidationResult], dict | None]:
        Returns ReceiptValidationResults with a specific type.
    """

    @handle_dynamodb_errors("add_receipt_validation_result")
    def add_receipt_validation_result(self, result: ReceiptValidationResult):
        """Adds a ReceiptValidationResult to DynamoDB.

        Args:
            result (ReceiptValidationResult): The ReceiptValidationResult to
                add.

        Raises:
            ValueError: If the result is None or not an instance of
                ReceiptValidationResult.
            Exception: If the result cannot be added to DynamoDB.
        """
        self._validate_entity(result, ReceiptValidationResult, "result")
        self._add_entity(
            result,
            condition_expression=(
                "attribute_not_exists(PK) AND attribute_not_exists(SK)"
            ),
        )

    @handle_dynamodb_errors("add_receipt_validation_results")
    def add_receipt_validation_results(
        self, results: List[ReceiptValidationResult]
    ):
        """Adds multiple ReceiptValidationResults to DynamoDB in batches.

        Args:
            results (list[ReceiptValidationResult]): The
                ReceiptValidationResults to add.

        Raises:
            ValueError: If the results are None or not a list.
            Exception: If the results cannot be added to DynamoDB.
        """
        self._validate_entity_list(results, ReceiptValidationResult, "results")

        request_items = [
            WriteRequestTypeDef(
                PutRequest=PutRequestTypeDef(Item=result.to_item())
            )
            for result in results
        ]

        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("update_receipt_validation_result")
    def update_receipt_validation_result(
        self, result: ReceiptValidationResult
    ):
        """Updates an existing ReceiptValidationResult in the database.

        Args:
            result (ReceiptValidationResult): The ReceiptValidationResult to
                update.

        Raises:
            ValueError: If the result is None or not an instance of
                ReceiptValidationResult.
            Exception: If the result cannot be updated in DynamoDB.
        """
        self._validate_entity(result, ReceiptValidationResult, "result")
        self._update_entity(
            result,
            condition_expression=(
                "attribute_exists(PK) AND attribute_exists(SK)"
            ),
        )

    @handle_dynamodb_errors("update_receipt_validation_results")
    def update_receipt_validation_results(
        self, results: List[ReceiptValidationResult]
    ):
        """Updates multiple ReceiptValidationResults in the database.

        Args:
            results (list[ReceiptValidationResult]): The
                ReceiptValidationResults to update.

        Raises:
            ValueError: If the results are None or not a list.
            Exception: If the results cannot be updated in DynamoDB.
        """
        self._update_entities(results, ReceiptValidationResult, "results")

    @handle_dynamodb_errors("delete_receipt_validation_result")
    def delete_receipt_validation_result(
        self, result: ReceiptValidationResult
    ):
        """Deletes a single ReceiptValidationResult.

        Args:
            result (ReceiptValidationResult): The ReceiptValidationResult to
                delete.

        Raises:
            ValueError: If the result is None or not an instance of
                ReceiptValidationResult.
            Exception: If the result cannot be deleted from DynamoDB.
        """
        self._validate_entity(result, ReceiptValidationResult, "result")
        self._delete_entity(result)

    @handle_dynamodb_errors("delete_receipt_validation_results")
    def delete_receipt_validation_results(
        self, results: List[ReceiptValidationResult]
    ):
        """Deletes multiple ReceiptValidationResults in batch.

        Args:
            results (list[ReceiptValidationResult]): The
                ReceiptValidationResults to delete.

        Raises:
            ValueError: If the results are None or not a list.
            Exception: If the results cannot be deleted from DynamoDB.
        """
        self._validate_entity_list(results, ReceiptValidationResult, "results")

        request_items = [
            WriteRequestTypeDef(
                DeleteRequest=DeleteRequestTypeDef(Key=result.key)
            )
            for result in results
        ]

        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("get_receipt_validation_result")
    def get_receipt_validation_result(
        self,
        receipt_id: int,
        image_id: str,
        field_name: str,
        result_index: int,
    ) -> ReceiptValidationResult:
        """Retrieves a single ReceiptValidationResult by IDs.

        Args:
            receipt_id (int): The Receipt ID to query.
            image_id (str): The Image ID to query.
            field_name (str): The field name of the result.
            result_index (int): The index of this result within the field.

        Returns:
            ReceiptValidationResult: The retrieved ReceiptValidationResult.

        Raises:
            ValueError: If the IDs are invalid.
            Exception: If the ReceiptValidationResult cannot be retrieved from
                DynamoDB.
        """
        # Custom parameter validation for backward compatibility
        if receipt_id is None:
            raise EntityValidationError("receipt_id cannot be None")
        if image_id is None:
            raise EntityValidationError("image_id cannot be None")
        if field_name is None:
            raise EntityValidationError("field_name cannot be None")
        if result_index is None:
            raise EntityValidationError("result_index cannot be None")

        if not isinstance(receipt_id, int):
            raise EntityValidationError(
                f"receipt_id must be an integer, got "
                f"{type(receipt_id).__name__}"
            )
        if not isinstance(image_id, str):
            raise EntityValidationError(
                f"image_id must be a string, got {type(image_id).__name__}"
            )
        if not isinstance(field_name, str):
            raise EntityValidationError(
                f"field_name must be a string, got {type(field_name).__name__}"
            )
        if not field_name:
            raise EntityValidationError("field_name must not be empty.")

        if not isinstance(result_index, int):
            raise EntityValidationError(
                f"result_index must be an integer, got "
                f"{type(result_index).__name__}"
            )
        if result_index < 0:
            raise EntityValidationError("result_index must be non-negative.")

        try:
            assert_valid_uuid(image_id)
        except ValueError as e:
            raise EntityValidationError(f"Invalid image_id format: {e}") from e

        result = self._get_entity(
            primary_key=f"IMAGE#{image_id}",
            sort_key=f"RECEIPT#{receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{field_name}#RESULT#{result_index}",
            entity_class=ReceiptValidationResult,
            converter_func=item_to_receipt_validation_result
        )
        
        if result is None:
            raise EntityNotFoundError(
                f"ReceiptValidationResult with field {field_name} and "
                f"index {result_index} not found"
            )
        
        return result

    @handle_dynamodb_errors("list_receipt_validation_results")
    def list_receipt_validation_results(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict] = None,
    ) -> Tuple[List[ReceiptValidationResult], Optional[Dict]]:
        """Returns ReceiptValidationResults and the last evaluated key.

        Args:
            limit (Optional[int], optional): The maximum number of items to
                return. Defaults to None.
            last_evaluated_key (Optional[Dict], optional): The key to start
                from for pagination. Defaults to None.

        Returns:
            tuple[list[ReceiptValidationResult], dict | None]: A tuple
                containing the list of ReceiptValidationResults and the last
                evaluated key for pagination.

        Raises:
            ValueError: If the limit or last_evaluated_key are invalid.
            Exception: If the ReceiptValidationResults cannot be retrieved
                from DynamoDB.
        """
        if limit is not None and not isinstance(limit, int):
            raise EntityValidationError("limit must be an integer or None")
        if last_evaluated_key is not None and not isinstance(
            last_evaluated_key, dict
        ):
            raise EntityValidationError("last_evaluated_key must be a dictionary or None")

        return self._query_entities(
            index_name="GSITYPE",
            key_condition_expression="#t = :val",
            expression_attribute_names={"#t": "TYPE"},
            expression_attribute_values={
                ":val": {"S": "RECEIPT_VALIDATION_RESULT"}
            },
            converter_func=item_to_receipt_validation_result,
            limit=limit,
            last_evaluated_key=last_evaluated_key
        )

    @handle_dynamodb_errors("list_receipt_validation_results_for_field")
    def list_receipt_validation_results_for_field(
        self,
        receipt_id: int,
        image_id: str,
        field_name: str,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict] = None,
    ) -> Tuple[List[ReceiptValidationResult], Optional[Dict]]:
        """Returns ReceiptValidationResults for a specific field.

        Args:
            receipt_id (int): The Receipt ID to query.
            image_id (str): The Image ID to query.
            field_name (str): The field name to filter by.
            limit (Optional[int], optional): The maximum number of items to
                return. Defaults to None.
            last_evaluated_key (Optional[Dict], optional): The key to start
                from for pagination. Defaults to None.

        Returns:
            tuple[list[ReceiptValidationResult], dict | None]: A tuple
                containing the list of ReceiptValidationResults and the last
                evaluated key for pagination.

        Raises:
            ValueError: If the parameters are invalid.
            Exception: If the ReceiptValidationResults cannot be retrieved
                from DynamoDB.
        """
        if receipt_id is None:
            raise EntityValidationError("receipt_id cannot be None")
        if not isinstance(receipt_id, int):
            raise EntityValidationError(
                f"receipt_id must be an integer, got "
                f"{type(receipt_id).__name__}"
            )
        if image_id is None:
            raise EntityValidationError("image_id cannot be None")
        if not isinstance(image_id, str):
            raise EntityValidationError(
                f"image_id must be a string, got {type(image_id).__name__}"
            )
        if field_name is None:
            raise EntityValidationError("field_name cannot be None")
        if not isinstance(field_name, str):
            raise EntityValidationError(
                f"field_name must be a string, got {type(field_name).__name__}"
            )
        if not field_name:
            raise EntityValidationError("field_name must not be empty.")

        if limit is not None and not isinstance(limit, int):
            raise EntityValidationError("limit must be an integer or None")
        if last_evaluated_key is not None and not isinstance(
            last_evaluated_key, dict
        ):
            raise EntityValidationError("last_evaluated_key must be a dictionary or None")

        try:
            assert_valid_uuid(image_id)
        except ValueError as e:
            raise EntityValidationError(f"Invalid image_id format: {e}") from e

        return self._query_entities(
            index_name=None,
            key_condition_expression="#pk = :pk AND begins_with(#sk, :sk_prefix)",
            expression_attribute_names={
                "#pk": "PK",
                "#sk": "SK",
            },
            expression_attribute_values={
                ":pk": {"S": f"IMAGE#{image_id}"},
                ":sk_prefix": {
                    "S": (
                        f"RECEIPT#{receipt_id:05d}#ANALYSIS#VALIDATION#"
                        f"CATEGORY#{field_name}#RESULT#"
                    )
                },
            },
            converter_func=item_to_receipt_validation_result,
            limit=limit,
            last_evaluated_key=last_evaluated_key
        )

    @handle_dynamodb_errors("list_receipt_validation_results_by_type")
    def list_receipt_validation_results_by_type(
        self,
        result_type: str,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict] = None,
    ) -> Tuple[List[ReceiptValidationResult], Optional[Dict]]:
        """Returns ReceiptValidationResults with a specific type.

        Args:
            result_type (str): The result type to filter by.
            limit (Optional[int], optional): The maximum number of items to
                return. Defaults to None.
            last_evaluated_key (Optional[Dict], optional): The key to start
                from for pagination. Defaults to None.

        Returns:
            tuple[list[ReceiptValidationResult], dict | None]: A tuple
                containing the list of ReceiptValidationResults and the last
                evaluated key for pagination.

        Raises:
            ValueError: If the parameters are invalid.
            Exception: If the ReceiptValidationResults cannot be retrieved
                from DynamoDB.
        """
        # Custom validation for backward compatibility
        if result_type is None:
            raise EntityValidationError("result_type parameter is required")

        if not isinstance(result_type, str):
            raise EntityValidationError(
                f"result_type must be a string, got "
                f"{type(result_type).__name__}"
            )
        if not result_type:
            raise EntityValidationError("result_type must not be empty")
        if limit is not None and not isinstance(limit, int):
            raise EntityValidationError("limit must be an integer or None")
        if last_evaluated_key is not None and not isinstance(
            last_evaluated_key, dict
        ):
            raise EntityValidationError("last_evaluated_key must be a dictionary or None")

        return self._query_entities(
            index_name="GSI1",
            key_condition_expression="#gsi1pk = :pk",
            expression_attribute_names={"#gsi1pk": "GSI1PK"},
            expression_attribute_values={
                ":pk": {"S": f"RESULT_TYPE#{result_type}"}
            },
            converter_func=item_to_receipt_validation_result,
            limit=limit,
            last_evaluated_key=last_evaluated_key
        )
