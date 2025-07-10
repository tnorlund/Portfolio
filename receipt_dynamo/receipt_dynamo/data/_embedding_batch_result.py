from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple
from uuid import uuid4

from botocore.exceptions import ClientError

from receipt_dynamo.constants import EmbeddingStatus
from receipt_dynamo.data._base import DynamoClientProtocol

if TYPE_CHECKING:
    from receipt_dynamo.data._base import (
        DeleteTypeDef,
        PutRequestTypeDef,
        PutTypeDef,
        QueryInputTypeDef,
        TransactWriteItemTypeDef,
        WriteRequestTypeDef,
    )

# These are used at runtime, not just for type checking
from receipt_dynamo.data._base import (
    DeleteTypeDef,
    PutRequestTypeDef,
    PutTypeDef,
    TransactWriteItemTypeDef,
    WriteRequestTypeDef,
)
from receipt_dynamo.data.shared_exceptions import (
    BatchOperationError,
    DynamoDBError,
    OperationError,
)
from receipt_dynamo.entities.embedding_batch_result import (
    EmbeddingBatchResult,
    item_to_embedding_batch_result,
)
from receipt_dynamo.entities.util import assert_valid_uuid


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


class _EmbeddingBatchResult(DynamoClientProtocol):
    """DynamoDB accessor for EmbeddingBatchResult items."""

    def add_embedding_batch_result(
        self, embedding_batch_result: EmbeddingBatchResult
    ):
        """
        Adds an EmbeddingBatchResult to the database.

        Raises ValueError on invalid input or if item already exists.
        """
        if embedding_batch_result is None:
            raise ValueError(
                "EmbeddingBatchResult parameter is required and cannot be None."
            )
        if not isinstance(embedding_batch_result, EmbeddingBatchResult):
            raise ValueError(
                "embedding_batch_result must be an instance of EmbeddingBatchResult."
            )

        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=embedding_batch_result.to_item(),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"Embedding batch result for Batch ID '{embedding_batch_result.batch_id}' already exists"
                ) from e
            elif error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    f"Could not add embedding batch result to DynamoDB: {e}"
                ) from e
            else:
                raise DynamoDBError(
                    f"Could not add embedding batch result to DynamoDB: {e}"
                ) from e

    def add_embedding_batch_results(
        self, embedding_batch_results: List[EmbeddingBatchResult]
    ):
        """
        Batch add EmbeddingBatchResults to DynamoDB.
        """
        if embedding_batch_results is None:
            raise ValueError(
                "EmbeddingBatchResults parameter is required and cannot be None."
            )
        if not isinstance(embedding_batch_results, list):
            raise ValueError(
                "embedding_batch_results must be a list of EmbeddingBatchResult instances."
            )
        if not all(
            isinstance(r, EmbeddingBatchResult)
            for r in embedding_batch_results
        ):
            raise ValueError(
                "All embedding batch results must be instances of EmbeddingBatchResult."
            )

        try:
            for i in range(0, len(embedding_batch_results), 25):
                chunk = embedding_batch_results[i : i + 25]
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
        except ClientError as e:
            raise Exception(
                f"Error adding embedding batch results: {e}"
            ) from e

    def update_embedding_batch_result(
        self, embedding_batch_result: EmbeddingBatchResult
    ):
        """
        Updates an EmbeddingBatchResult in DynamoDB. Raises if it does not exist.
        """
        if embedding_batch_result is None:
            raise ValueError(
                "EmbeddingBatchResult parameter is required and cannot be None."
            )
        if not isinstance(embedding_batch_result, EmbeddingBatchResult):
            raise ValueError(
                "embedding_batch_result must be an instance of EmbeddingBatchResult."
            )

        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=embedding_batch_result.to_item(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"Embedding batch result for Batch ID '{embedding_batch_result.batch_id}' does not exist"
                ) from e
            else:
                raise BatchOperationError(
                    f"Error updating embedding batch result: {e}"
                ) from e

    def update_embedding_batch_results(
        self, embedding_batch_results: List[EmbeddingBatchResult]
    ):
        """
        Batch update EmbeddingBatchResults in DynamoDB.
        """
        if embedding_batch_results is None:
            raise ValueError(
                "EmbeddingBatchResults parameter is required and cannot be None."
            )
        if not isinstance(embedding_batch_results, list):
            raise ValueError(
                "embedding_batch_results must be a list of EmbeddingBatchResult instances."
            )
        if not all(
            isinstance(r, EmbeddingBatchResult)
            for r in embedding_batch_results
        ):
            raise ValueError(
                "All embedding batch results must be instances of EmbeddingBatchResult."
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
            try:
                self._client.transact_write_items(TransactItems=transact_items)
            except ClientError as e:
                raise BatchOperationError(
                    f"Error updating embedding batch results: {e}"
                ) from e

    def delete_embedding_batch_result(
        self, embedding_batch_result: EmbeddingBatchResult
    ):
        """
        Deletes an EmbeddingBatchResult from DynamoDB.
        """
        if embedding_batch_result is None:
            raise ValueError(
                "EmbeddingBatchResult parameter is required and cannot be None."
            )
        if not isinstance(embedding_batch_result, EmbeddingBatchResult):
            raise ValueError(
                "embedding_batch_result must be an instance of EmbeddingBatchResult."
            )

        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key=embedding_batch_result.key,
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"Embedding batch result for Batch ID '{embedding_batch_result.batch_id}' does not exist"
                ) from e
            else:
                raise BatchOperationError(
                    f"Error deleting embedding batch result: {e}"
                ) from e

    def delete_embedding_batch_results(
        self, embedding_batch_results: List[EmbeddingBatchResult]
    ):
        """
        Batch delete EmbeddingBatchResults from DynamoDB.
        """
        if embedding_batch_results is None:
            raise ValueError(
                "EmbeddingBatchResults parameter is required and cannot be None."
            )
        if not isinstance(embedding_batch_results, list):
            raise ValueError(
                "embedding_batch_results must be a list of EmbeddingBatchResult instances."
            )
        if not all(
            isinstance(r, EmbeddingBatchResult)
            for r in embedding_batch_results
        ):
            raise ValueError(
                "All embedding batch results must be instances of EmbeddingBatchResult."
            )

        for i in range(0, len(embedding_batch_results), 25):
            chunk = embedding_batch_results[i : i + 25]
            transact_items = [
                TransactWriteItemTypeDef(
                    Delete=DeleteTypeDef(
                        TableName=self.table_name,
                        Key=r.key,
                        ConditionExpression="attribute_exists(PK)",
                    )
                )
                for r in chunk
            ]
            try:
                self._client.transact_write_items(TransactItems=transact_items)
            except ClientError as e:
                raise BatchOperationError(
                    f"Error deleting embedding batch results: {e}"
                ) from e

    def get_embedding_batch_result(
        self,
        batch_id: str,
        image_id: str,
        receipt_id: int,
        line_id: int,
        word_id: int,
    ) -> EmbeddingBatchResult:
        """
        Gets an EmbeddingBatchResult from DynamoDB by primary key.
        """
        assert_valid_uuid(batch_id)
        assert_valid_uuid(image_id)
        if not isinstance(receipt_id, int) or receipt_id <= 0:
            raise ValueError("receipt_id must be a positive integer")
        if not isinstance(line_id, int) or line_id < 0:
            raise ValueError("line_id must be zero or positive integer")
        if not isinstance(word_id, int) or word_id < 0:
            raise ValueError("word_id must be zero or positive integer")

        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"BATCH#{batch_id}"},
                    "SK": {
                        "S": f"RESULT#IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:03d}#WORD#{word_id:03d}"
                    },
                },
            )
            if "Item" in response:
                return item_to_embedding_batch_result(response["Item"])
            else:
                raise ValueError(
                    f"Embedding batch result for Batch ID '{batch_id}', Image ID {image_id}, Receipt ID {receipt_id}, Line ID {line_id}, Word ID {word_id} does not exist."
                )
        except ClientError as e:
            raise Exception(
                f"Error getting embedding batch result: {e}"
            ) from e

    def list_embedding_batch_results(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[EmbeddingBatchResult], Optional[dict]]:
        """
        List all EmbeddingBatchResults, paginated.
        """
        if limit is not None and (not isinstance(limit, int) or limit <= 0):
            raise ValueError("Limit must be a positive integer.")
        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise ValueError("LastEvaluatedKey must be a dictionary.")
            validate_last_evaluated_key(last_evaluated_key)

        results: List[EmbeddingBatchResult] = []
        try:
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "IndexName": "GSITYPE",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "TYPE"},
                "ExpressionAttributeValues": {
                    ":val": {"S": "EMBEDDING_BATCH_RESULT"}
                },
            }
            if last_evaluated_key is not None:
                query_params["ExclusiveStartKey"] = last_evaluated_key

            while True:
                if limit is not None:
                    remaining = limit - len(results)
                    query_params["Limit"] = remaining

                response = self._client.query(**query_params)
                results.extend(
                    [
                        item_to_embedding_batch_result(item)
                        for item in response["Items"]
                    ]
                )

                if limit is not None and len(results) >= limit:
                    results = results[:limit]
                    last_evaluated_key = response.get("LastEvaluatedKey", None)
                    break

                if "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                else:
                    last_evaluated_key = None
                    break

            return results, last_evaluated_key
        except ClientError as e:
            raise Exception(
                f"Error listing embedding batch results: {e}"
            ) from e

    def get_embedding_batch_results_by_status(
        self,
        status: str,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[EmbeddingBatchResult], Optional[dict]]:
        """
        Query EmbeddingBatchResults by status using GSI2.
        """
        if not isinstance(status, str) or not status:
            raise ValueError("Status must be a non-empty string")
        if status not in [s.value for s in EmbeddingStatus]:
            raise ValueError(
                "Status must be one of: "
                + ", ".join(s.value for s in EmbeddingStatus)
            )
        if limit is not None and (not isinstance(limit, int) or limit <= 0):
            raise ValueError("Limit must be a positive integer.")
        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise ValueError("LastEvaluatedKey must be a dictionary.")
            validate_last_evaluated_key(last_evaluated_key)

        results: List[EmbeddingBatchResult] = []
        try:
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "IndexName": "GSI2",
                "KeyConditionExpression": "GSI2SK = :sk",
                "ExpressionAttributeValues": {
                    ":sk": {"S": f"STATUS#{status}"}
                },
            }
            if last_evaluated_key is not None:
                query_params["ExclusiveStartKey"] = last_evaluated_key

            while True:
                if limit is not None:
                    remaining = limit - len(results)
                    query_params["Limit"] = remaining

                response = self._client.query(**query_params)
                results.extend(
                    [
                        item_to_embedding_batch_result(item)
                        for item in response["Items"]
                    ]
                )

                if limit is not None and len(results) >= limit:
                    results = results[:limit]
                    last_evaluated_key = response.get("LastEvaluatedKey", None)
                    break

                if "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                else:
                    last_evaluated_key = None
                    break

            return results, last_evaluated_key
        except ClientError as e:
            raise Exception(
                f"Error querying embedding batch results by status: {e}"
            ) from e

    def get_embedding_batch_results_by_receipt(
        self,
        image_id: str,
        receipt_id: int,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> Tuple[List[EmbeddingBatchResult], Optional[dict]]:
        """
        Query EmbeddingBatchResults by receipt_id using GSI3.
        """
        assert_valid_uuid(image_id)
        if not isinstance(receipt_id, int) or receipt_id <= 0:
            raise ValueError("receipt_id must be a positive integer.")
        if limit is not None and (not isinstance(limit, int) or limit <= 0):
            raise ValueError("Limit must be a positive integer.")
        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise ValueError("LastEvaluatedKey must be a dictionary.")
            validate_last_evaluated_key(last_evaluated_key)

        results: List[EmbeddingBatchResult] = []
        try:
            template_embedding_batch_result = EmbeddingBatchResult(
                batch_id=str(uuid4()),
                image_id=image_id,
                receipt_id=receipt_id,
                line_id=0,
                word_id=0,
                pinecone_id="dummy",
                status="dummy",
                text="dummy",
                error_message="dummy",
            )
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "IndexName": "GSI3",
                "KeyConditionExpression": "GSI3PK = :pk",
                "ExpressionAttributeValues": {
                    ":pk": template_embedding_batch_result.gsi3_key()["GSI3PK"]
                },
            }
            if last_evaluated_key is not None:
                query_params["ExclusiveStartKey"] = last_evaluated_key

            while True:
                if limit is not None:
                    remaining = limit - len(results)
                    query_params["Limit"] = remaining

                response = self._client.query(**query_params)
                results.extend(
                    [
                        item_to_embedding_batch_result(item)
                        for item in response["Items"]
                    ]
                )

                if limit is not None and len(results) >= limit:
                    results = results[:limit]
                    last_evaluated_key = response.get("LastEvaluatedKey", None)
                    break

                if "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                else:
                    last_evaluated_key = None
                    break

            return results, last_evaluated_key
        except ClientError as e:
            raise Exception(
                f"Error querying embedding batch results by receipt: {e}"
            ) from e
