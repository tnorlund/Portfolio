from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from receipt_dynamo import (
    ReceiptValidationResult,
    item_to_receipt_validation_result,
)
from receipt_dynamo.data.base_operations import (
    BatchOperationsMixin,
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    TransactionalOperationsMixin,
    handle_dynamodb_errors,
)

if TYPE_CHECKING:
    from receipt_dynamo.data._base import (
        DeleteRequestTypeDef,
        PutRequestTypeDef,
        QueryInputTypeDef,
        WriteRequestTypeDef,
    )

# These are used at runtime, not just for type checking
from receipt_dynamo.data._base import (
    DeleteRequestTypeDef,
    PutRequestTypeDef,
    QueryInputTypeDef,
    WriteRequestTypeDef,
)
from receipt_dynamo.entities.util import assert_valid_uuid


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
        receipt_id: int,
        image_id: str,
        validation_type: str,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None
    ) -> tuple[list[ReceiptValidationResult], dict | None]:
        Returns ReceiptValidationResults for a specific validation type.
    """

    @handle_dynamodb_errors("add_receipt_validation_result")
    def add_receipt_validation_result(self, result: ReceiptValidationResult):
        """Adds a ReceiptValidationResult to DynamoDB.

        Args:
            result (ReceiptValidationResult): The ReceiptValidationResult to add.

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
        self._validate_entity_list(results, ReceiptValidationResult, "results")

        transact_items = [
            {
                "Put": {
                    "TableName": self.table_name,
                    "Item": result.to_item(),
                    "ConditionExpression": "attribute_exists(PK) AND attribute_exists(SK)",
                }
            }
            for result in results
        ]

        self._transact_write_with_chunking(transact_items)

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

        # Need to use direct delete since results don't have key() method
        self._client.delete_item(
            TableName=self.table_name,
            Key={
                "PK": {"S": f"IMAGE#{result.image_id}"},
                "SK": {
                    "S": f"RECEIPT#{result.receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{result.field_name}#RESULT#{result.result_index}"
                },
            },
        )

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
                DeleteRequest=DeleteRequestTypeDef(
                    Key={
                        "PK": {"S": f"IMAGE#{result.image_id}"},
                        "SK": {
                            "S": f"RECEIPT#{result.receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{result.field_name}#RESULT#{result.result_index}"
                        },
                    }
                )
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
            result_index (int): The index of the result.

        Returns:
            ReceiptValidationResult: The retrieved ReceiptValidationResult.

        Raises:
            ValueError: If the IDs are invalid.
            Exception: If the ReceiptValidationResult cannot be retrieved from
                DynamoDB.
        """
        if not isinstance(receipt_id, int):
            raise ValueError(
                f"receipt_id must be an integer, got {type(receipt_id).__name__}"
            )
        if not isinstance(image_id, str):
            raise ValueError(
                f"image_id must be a string, got {type(image_id).__name__}"
            )
        if not isinstance(field_name, str):
            raise ValueError(
                f"field_name must be a string, got {type(field_name).__name__}"
            )
        if not isinstance(result_index, int):
            raise ValueError(
                f"result_index must be an integer, got {type(result_index).__name__}"
            )

        try:
            assert_valid_uuid(image_id)
        except ValueError as e:
            raise ValueError(f"Invalid image_id format: {e}") from e

        response = self._client.get_item(
            TableName=self.table_name,
            Key={
                "PK": {"S": f"IMAGE#{image_id}"},
                "SK": {
                    "S": f"RECEIPT#{receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{field_name}#RESULT#{result_index}"
                },
            },
        )

        item = response.get("Item")
        if not item:
            raise ValueError(
                f"ReceiptValidationResult for receipt {receipt_id}, image "
                f"{image_id}, field {field_name}, and index {result_index} "
                f"does not exist"
            )

        return item_to_receipt_validation_result(item)

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
            raise ValueError("limit must be an integer or None")
        if last_evaluated_key is not None and not isinstance(
            last_evaluated_key, dict
        ):
            raise ValueError("last_evaluated_key must be a dictionary or None")

        query_params: QueryInputTypeDef = {
            "TableName": self.table_name,
            "IndexName": "GSITYPE",
            "KeyConditionExpression": "#t = :val",
            "ExpressionAttributeNames": {"#t": "TYPE"},
            "ExpressionAttributeValues": {
                ":val": {"S": "RECEIPT_VALIDATION_RESULT"}
            },
        }

        if last_evaluated_key is not None:
            query_params["ExclusiveStartKey"] = last_evaluated_key
        if limit is not None:
            query_params["Limit"] = limit

        results = []
        response = self._client.query(**query_params)
        results.extend(
            [
                item_to_receipt_validation_result(item)
                for item in response.get("Items", [])
            ]
        )

        if limit is None:
            # Paginate through all results
            while "LastEvaluatedKey" in response:
                query_params["ExclusiveStartKey"] = response[
                    "LastEvaluatedKey"
                ]
                response = self._client.query(**query_params)
                results.extend(
                    [
                        item_to_receipt_validation_result(item)
                        for item in response.get("Items", [])
                    ]
                )
            last_evaluated_key = None
        else:
            last_evaluated_key = response.get("LastEvaluatedKey")

        return results, last_evaluated_key

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
        if not isinstance(receipt_id, int):
            raise ValueError(
                f"receipt_id must be an integer, got {type(receipt_id).__name__}"
            )
        if not isinstance(image_id, str):
            raise ValueError(
                f"image_id must be a string, got {type(image_id).__name__}"
            )
        if not isinstance(field_name, str):
            raise ValueError(
                f"field_name must be a string, got {type(field_name).__name__}"
            )
        if limit is not None and not isinstance(limit, int):
            raise ValueError("limit must be an integer or None")
        if last_evaluated_key is not None and not isinstance(
            last_evaluated_key, dict
        ):
            raise ValueError("last_evaluated_key must be a dictionary or None")

        try:
            assert_valid_uuid(image_id)
        except ValueError as e:
            raise ValueError(f"Invalid image_id format: {e}") from e

        query_params: QueryInputTypeDef = {
            "TableName": self.table_name,
            "KeyConditionExpression": "#pk = :pk AND begins_with(#sk, :sk_prefix)",
            "ExpressionAttributeNames": {
                "#pk": "PK",
                "#sk": "SK",
            },
            "ExpressionAttributeValues": {
                ":pk": {"S": f"IMAGE#{image_id}"},
                ":sk_prefix": {
                    "S": f"RECEIPT#{receipt_id:05d}#ANALYSIS#VALIDATION#CATEGORY#{field_name}#RESULT#"
                },
            },
        }

        if last_evaluated_key is not None:
            query_params["ExclusiveStartKey"] = last_evaluated_key
        if limit is not None:
            query_params["Limit"] = limit

        results = []
        response = self._client.query(**query_params)
        results.extend(
            [
                item_to_receipt_validation_result(item)
                for item in response.get("Items", [])
            ]
        )

        if limit is None:
            # Paginate through all results
            while "LastEvaluatedKey" in response:
                query_params["ExclusiveStartKey"] = response[
                    "LastEvaluatedKey"
                ]
                response = self._client.query(**query_params)
                results.extend(
                    [
                        item_to_receipt_validation_result(item)
                        for item in response.get("Items", [])
                    ]
                )
            last_evaluated_key = None
        else:
            last_evaluated_key = response.get("LastEvaluatedKey")

        return results, last_evaluated_key

    @handle_dynamodb_errors("list_receipt_validation_results_by_type")
    def list_receipt_validation_results_by_type(
        self,
        receipt_id: int,
        image_id: str,
        validation_type: str,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict] = None,
    ) -> Tuple[List[ReceiptValidationResult], Optional[Dict]]:
        """Returns ReceiptValidationResults for a specific validation type.

        Args:
            receipt_id (int): The Receipt ID to query.
            image_id (str): The Image ID to query.
            validation_type (str): The validation type to filter by.
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
        if not isinstance(receipt_id, int):
            raise ValueError(
                f"receipt_id must be an integer, got {type(receipt_id).__name__}"
            )
        if not isinstance(image_id, str):
            raise ValueError(
                f"image_id must be a string, got {type(image_id).__name__}"
            )
        if not isinstance(validation_type, str):
            raise ValueError(
                f"validation_type must be a string, got {type(validation_type).__name__}"
            )
        if limit is not None and not isinstance(limit, int):
            raise ValueError("limit must be an integer or None")
        if last_evaluated_key is not None and not isinstance(
            last_evaluated_key, dict
        ):
            raise ValueError("last_evaluated_key must be a dictionary or None")

        try:
            assert_valid_uuid(image_id)
        except ValueError as e:
            raise ValueError(f"Invalid image_id format: {e}") from e

        # Query using GSI1 for validation type
        query_params: QueryInputTypeDef = {
            "TableName": self.table_name,
            "IndexName": "GSI1",
            "KeyConditionExpression": "#gsi1pk = :pk AND begins_with(#gsi1sk, :sk_prefix)",
            "ExpressionAttributeNames": {
                "#gsi1pk": "GSI1PK",
                "#gsi1sk": "GSI1SK",
            },
            "ExpressionAttributeValues": {
                ":pk": {"S": f"VALIDATION_TYPE#{validation_type}"},
                ":sk_prefix": {
                    "S": f"RECEIPT#{receipt_id:05d}#IMAGE#{image_id}"
                },
            },
        }

        if last_evaluated_key is not None:
            query_params["ExclusiveStartKey"] = last_evaluated_key
        if limit is not None:
            query_params["Limit"] = limit

        results = []
        response = self._client.query(**query_params)
        results.extend(
            [
                item_to_receipt_validation_result(item)
                for item in response.get("Items", [])
            ]
        )

        if limit is None:
            # Paginate through all results
            while "LastEvaluatedKey" in response:
                query_params["ExclusiveStartKey"] = response[
                    "LastEvaluatedKey"
                ]
                response = self._client.query(**query_params)
                results.extend(
                    [
                        item_to_receipt_validation_result(item)
                        for item in response.get("Items", [])
                    ]
                )
            last_evaluated_key = None
        else:
            last_evaluated_key = response.get("LastEvaluatedKey")

        return results, last_evaluated_key
