from typing import List, Optional, Tuple

from botocore.exceptions import ClientError

from receipt_dynamo.constants import ValidationStatus
from receipt_dynamo.data._base import DynamoClientProtocol
from receipt_dynamo.entities.completion_batch_result import (
    CompletionBatchResult,
    itemToCompletionBatchResult,
)


def validate_last_evaluated_key(lek: dict) -> None:
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
    def addCompletionBatchResult(self, result: CompletionBatchResult):
        if result is None or not isinstance(result, CompletionBatchResult):
            raise ValueError("Must provide a CompletionBatchResult instance.")
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=result.to_item(),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as e:
            raise Exception(f"Could not add completion batch result: {e}")

    def addCompletionBatchResults(self, results: List[CompletionBatchResult]):
        if not isinstance(results, list) or not all(
            isinstance(r, CompletionBatchResult) for r in results
        ):
            raise ValueError(
                "Must provide a list of CompletionBatchResult instances."
            )
        for i in range(0, len(results), 25):
            chunk = results[i : i + 25]
            request_items = [
                {"PutRequest": {"Item": r.to_item()}} for r in chunk
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

    def updateCompletionBatchResult(self, result: CompletionBatchResult):
        if result is None or not isinstance(result, CompletionBatchResult):
            raise ValueError("Must provide a CompletionBatchResult instance.")
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=result.to_item(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            raise Exception(f"Could not update completion batch result: {e}")

    def deleteCompletionBatchResult(self, result: CompletionBatchResult):
        if result is None or not isinstance(result, CompletionBatchResult):
            raise ValueError("Must provide a CompletionBatchResult instance.")
        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key=result.key(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            raise Exception(f"Could not delete completion batch result: {e}")

    def getCompletionBatchResult(
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
            return itemToCompletionBatchResult(response["Item"])
        except ClientError as e:
            raise Exception(f"Could not retrieve completion batch result: {e}")

    def listCompletionBatchResults(
        self, limit: int = None, lastEvaluatedKey: dict = None
    ) -> Tuple[List[CompletionBatchResult], Optional[dict]]:
        if limit is not None and (not isinstance(limit, int) or limit <= 0):
            raise ValueError("limit must be a positive integer.")
        if lastEvaluatedKey is not None:
            validate_last_evaluated_key(lastEvaluatedKey)

        results = []
        try:
            query_params = {
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
                    itemToCompletionBatchResult(item)
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
            raise Exception(f"Error listing completion batch results: {e}")

    def getCompletionBatchResultsByStatus(
        self, status: str, limit: int = None, lastEvaluatedKey: dict = None
    ) -> Tuple[List[CompletionBatchResult], Optional[dict]]:
        if status not in [s.value for s in ValidationStatus]:
            raise ValueError("Invalid status.")
        if lastEvaluatedKey:
            validate_last_evaluated_key(lastEvaluatedKey)

        results = []
        query_params = {
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
                itemToCompletionBatchResult(item) for item in response["Items"]
            )

            if limit and len(results) >= limit:
                return results[:limit], response.get("LastEvaluatedKey")
            if "LastEvaluatedKey" in response:
                query_params["ExclusiveStartKey"] = response[
                    "LastEvaluatedKey"
                ]
            else:
                return results, None

    def getCompletionBatchResultsByLabelTarget(
        self,
        label_target: str,
        limit: int = None,
        lastEvaluatedKey: dict = None,
    ) -> Tuple[List[CompletionBatchResult], Optional[dict]]:
        if not isinstance(label_target, str):
            raise ValueError("label_target must be a string.")
        if lastEvaluatedKey:
            validate_last_evaluated_key(lastEvaluatedKey)

        results = []
        query_params = {
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
                itemToCompletionBatchResult(item) for item in response["Items"]
            )
            if limit and len(results) >= limit:
                return results[:limit], response.get("LastEvaluatedKey")
            if "LastEvaluatedKey" in response:
                query_params["ExclusiveStartKey"] = response[
                    "LastEvaluatedKey"
                ]
            else:
                return results, None

    def getCompletionBatchResultsByReceipt(
        self, receipt_id: int, limit: int = None, lastEvaluatedKey: dict = None
    ) -> Tuple[List[CompletionBatchResult], Optional[dict]]:
        if not isinstance(receipt_id, int) or receipt_id <= 0:
            raise ValueError("receipt_id must be a positive integer")
        if lastEvaluatedKey:
            validate_last_evaluated_key(lastEvaluatedKey)

        results = []
        query_params = {
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
                itemToCompletionBatchResult(item) for item in response["Items"]
            )
            if limit and len(results) >= limit:
                return results[:limit], response.get("LastEvaluatedKey")
            if "LastEvaluatedKey" in response:
                query_params["ExclusiveStartKey"] = response[
                    "LastEvaluatedKey"
                ]
            else:
                return results, None
