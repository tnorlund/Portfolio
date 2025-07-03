from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from botocore.exceptions import ClientError

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.data._base import DynamoClientProtocol

if TYPE_CHECKING:
    from receipt_dynamo.data._base import (
        PutRequestTypeDef,
        QueryInputTypeDef,
        WriteRequestTypeDef,
    )

# These are used at runtime, not just for type checking
from receipt_dynamo.data._base import (
    PutRequestTypeDef,
    WriteRequestTypeDef,
)
from receipt_dynamo.data.shared_exceptions import (
    BatchOperationError,
    OperationError,
)
from receipt_dynamo.entities.completion_batch_result import (
    CompletionBatchResult,
    item_to_completion_batch_result,
)


def validate_last_evaluated_key(lek: Dict[str, Any]) -> None:
    required_keys = {"PK", "SK"}
    if not required_keys.issubset(lek.keys()):
        raise ValueError(
            f"LastEvaluatedKey must contain keys: {required_keys}"
        )
    for key in required_keys:
        if not isinstance(lek[key], dict) or "S" not in lek[key]:
            raise ValueError(
                f"LastEvaluatedKey[{key}] must be a dict containing a key 'S'"
            )


class _CompletionBatchResult(DynamoClientProtocol):
    def add_completion_batch_result(self, result: CompletionBatchResult):
        if result is None or not isinstance(result, CompletionBatchResult):
            raise ValueError("Must provide a CompletionBatchResult instance.")
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=result.to_item(),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as e:
            raise OperationError(
                f"Could not add completion batch result: {e}"
            ) from e

    def add_completion_batch_results(
        self, results: List[CompletionBatchResult]
    ):
        if not isinstance(results, list) or not all(
            isinstance(r, CompletionBatchResult) for r in results
        ):
            raise ValueError(
                "Must provide a list of CompletionBatchResult instances."
            )
        for i in range(0, len(results), 25):
            chunk = results[i : i + 25]
            request_items = [
                WriteRequestTypeDef(
                    PutRequest=PutRequestTypeDef(Item=r.to_item())
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

    def update_completion_batch_result(self, result: CompletionBatchResult):
        if result is None or not isinstance(result, CompletionBatchResult):
            raise ValueError("Must provide a CompletionBatchResult instance.")
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=result.to_item(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            raise OperationError(
                f"Could not update completion batch result: {e}"
            ) from e

    def delete_completion_batch_result(self, result: CompletionBatchResult):
        if result is None or not isinstance(result, CompletionBatchResult):
            raise ValueError("Must provide a CompletionBatchResult instance.")
        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key=result.key(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            raise OperationError(
                f"Could not delete completion batch result: {e}"
            ) from e

    def get_completion_batch_result(
        self,
        batch_id: str,
        receipt_id: int,
        line_id: int,
        word_id: int,
        label: str,
    ) -> CompletionBatchResult:
        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"BATCH#{batch_id}"},
                    "SK": {
                        "S": f"RESULT#RECEIPT#{receipt_id}#LINE#{line_id}#WORD#{word_id}#LABEL#{label}"
                    },
                },
            )
            if "Item" not in response:
                raise ValueError("Completion batch result not found.")
            return item_to_completion_batch_result(response["Item"])
        except ClientError as e:
            raise OperationError(
                f"Could not retrieve completion batch result: {e}"
            ) from e

    def list_completion_batch_results(
        self,
        limit: Optional[int] = None,
        lastEvaluatedKey: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[CompletionBatchResult], Optional[dict]]:
        if limit is not None and (not isinstance(limit, int) or limit <= 0):
            raise ValueError("limit must be a positive integer.")
        if lastEvaluatedKey is not None:
            validate_last_evaluated_key(lastEvaluatedKey)

        results: List[CompletionBatchResult] = []
        try:
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "IndexName": "GSITYPE",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "TYPE"},
                "ExpressionAttributeValues": {
                    ":val": {"S": "COMPLETION_BATCH_RESULT"}
                },
            }
            if lastEvaluatedKey:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey

            while True:
                if limit is not None:
                    query_params["Limit"] = limit - len(results)

                response = self._client.query(**query_params)
                results.extend(
                    item_to_completion_batch_result(item)
                    for item in response["Items"]
                )

                if limit and len(results) >= limit:
                    return results[:limit], response.get("LastEvaluatedKey")
                if "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                else:
                    return results, None
        except ClientError as e:
            raise BatchOperationError(
                f"Error listing completion batch results: {e}"
            ) from e

    def get_completion_batch_results_by_status(
        self,
        status: str,
        limit: Optional[int] = None,
        lastEvaluatedKey: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[CompletionBatchResult], Optional[dict]]:
        if status not in [s.value for s in ValidationStatus]:
            raise ValueError("Invalid status.")
        if lastEvaluatedKey:
            validate_last_evaluated_key(lastEvaluatedKey)

        results: List[CompletionBatchResult] = []
        query_params: QueryInputTypeDef = {
            "TableName": self.table_name,
            "IndexName": "GSI2",
            "KeyConditionExpression": "GSI2SK = :val",
            "ExpressionAttributeValues": {":val": {"S": f"STATUS#{status}"}},
        }
        if lastEvaluatedKey:
            query_params["ExclusiveStartKey"] = lastEvaluatedKey

        while True:
            if limit:
                query_params["Limit"] = limit - len(results)

            response = self._client.query(**query_params)
            results.extend(
                item_to_completion_batch_result(item)
                for item in response["Items"]
            )

            if limit and len(results) >= limit:
                return results[:limit], response.get("LastEvaluatedKey")
            if "LastEvaluatedKey" in response:
                query_params["ExclusiveStartKey"] = response[
                    "LastEvaluatedKey"
                ]
            else:
                return results, None

    def get_completion_batch_results_by_label_target(
        self,
        label_target: str,
        limit: Optional[int] = None,
        lastEvaluatedKey: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[CompletionBatchResult], Optional[dict]]:
        if not isinstance(label_target, str):
            raise ValueError("label_target must be a string.")
        if lastEvaluatedKey:
            validate_last_evaluated_key(lastEvaluatedKey)

        results: List[CompletionBatchResult] = []
        query_params: QueryInputTypeDef = {
            "TableName": self.table_name,
            "IndexName": "GSI1",
            "KeyConditionExpression": "GSI1PK = :pk",
            "ExpressionAttributeValues": {
                ":pk": {"S": f"LABEL_TARGET#{label_target}"}
            },
        }
        if lastEvaluatedKey:
            query_params["ExclusiveStartKey"] = lastEvaluatedKey

        while True:
            if limit:
                query_params["Limit"] = limit - len(results)
            response = self._client.query(**query_params)
            results.extend(
                item_to_completion_batch_result(item)
                for item in response["Items"]
            )
            if limit and len(results) >= limit:
                return results[:limit], response.get("LastEvaluatedKey")
            if "LastEvaluatedKey" in response:
                query_params["ExclusiveStartKey"] = response[
                    "LastEvaluatedKey"
                ]
            else:
                return results, None

    def get_completion_batch_results_by_receipt(
        self,
        receipt_id: int,
        limit: Optional[int] = None,
        lastEvaluatedKey: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[CompletionBatchResult], Optional[dict]]:
        if not isinstance(receipt_id, int) or receipt_id <= 0:
            raise ValueError("receipt_id must be a positive integer")
        if lastEvaluatedKey:
            validate_last_evaluated_key(lastEvaluatedKey)

        results: List[CompletionBatchResult] = []
        query_params: QueryInputTypeDef = {
            "TableName": self.table_name,
            "IndexName": "GSI3",
            "KeyConditionExpression": "GSI3PK = :pk",
            "ExpressionAttributeValues": {
                ":pk": {"S": f"RECEIPT#{receipt_id}"}
            },
        }
        if lastEvaluatedKey:
            query_params["ExclusiveStartKey"] = lastEvaluatedKey

        while True:
            if limit:
                query_params["Limit"] = limit - len(results)
            response = self._client.query(**query_params)
            results.extend(
                item_to_completion_batch_result(item)
                for item in response["Items"]
            )
            if limit and len(results) >= limit:
                return results[:limit], response.get("LastEvaluatedKey")
            if "LastEvaluatedKey" in response:
                query_params["ExclusiveStartKey"] = response[
                    "LastEvaluatedKey"
                ]
            else:
                return results, None
