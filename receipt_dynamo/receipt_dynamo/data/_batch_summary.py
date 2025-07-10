from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple, Union

from botocore.exceptions import ClientError

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
    DynamoDBAccessError,
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    DynamoDBValidationError,
)

"""
This module provides the _BatchSummary class for managing BatchSummary
records in DynamoDB. It includes operations for inserting, updating,
deleting, and querying batch summary data, including support for pagination
and GSI lookups by status.
"""

from receipt_dynamo.constants import BatchStatus, BatchType
from receipt_dynamo.entities.batch_summary import (
    BatchSummary,
    item_to_batch_summary,
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


class _BatchSummary(DynamoClientProtocol):

    def add_batch_summary(self, batch_summary: BatchSummary) -> None:
        """
        Adds a single BatchSummary record to DynamoDB.

        Args:
            batch_summary (BatchSummary): The BatchSummary instance to add.

        Raises:
            ValueError: If batch_summary is None, not a BatchSummary, or if DynamoDB conditions fail.
        """
        if batch_summary is None:
            raise ValueError("batch_summary cannot be None")
        if not isinstance(batch_summary, BatchSummary):
            raise ValueError("batch_summary must be a BatchSummary")

        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=batch_summary.to_item(),
                ConditionExpression="attribute_not_exists(PK) and attribute_not_exists(SK)",
            )
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "ConditionalCheckFailedException":
                raise ValueError("batch_summary already exists") from e
            elif error_code == "ValidationException":
                raise ValueError("batch_summary is invalid") from e
            elif error_code == "InternalServerError":
                raise ValueError("internal server error") from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise ValueError("provisioned throughput exceeded") from e
            elif error_code == "ResourceNotFoundException":
                raise ValueError("table not found") from e
            else:
                raise ValueError(f"Error adding batch summary: {e}") from e

    def add_batch_summaries(self, batch_summaries: List[BatchSummary]) -> None:
        """
        Adds multiple BatchSummary records to DynamoDB in batches.

        Args:
            batch_summaries (List[BatchSummary]): A list of BatchSummary instances to add.

        Raises:
            ValueError: If batch_summaries is None, not a list, or contains invalid BatchSummary objects.
        """
        if batch_summaries is None:
            raise ValueError("batch_summaries cannot be None")
        if not isinstance(batch_summaries, list):
            raise ValueError("batch_summaries must be a list")
        if not all(isinstance(item, BatchSummary) for item in batch_summaries):
            raise ValueError("batch_summaries must be a list of BatchSummary")

        try:
            for i in range(0, len(batch_summaries), 25):
                chunk = batch_summaries[i : i + 25]
                request_items = [
                    WriteRequestTypeDef(
                        PutRequest=PutRequestTypeDef(Item=item.to_item())
                    )
                    for item in chunk
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
            error_code = e.response["Error"]["Code"]
            if error_code == "ConditionalCheckFailedException":
                raise ValueError("batch_summary already exists") from e
            elif error_code == "ValidationException":
                raise ValueError("batch_summary is invalid") from e
            elif error_code == "InternalServerError":
                raise ValueError("internal server error") from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise ValueError("provisioned throughput exceeded") from e
            elif error_code == "ResourceNotFoundException":
                raise ValueError("table not found") from e
            else:
                raise ValueError(f"Error adding batch summaries: {e}") from e

    def update_batch_summary(self, batch_summary: BatchSummary) -> None:
        """
        Updates an existing BatchSummary record in DynamoDB.

        Args:
            batch_summary (BatchSummary): The BatchSummary instance to update.

        Raises:
            ValueError: If batch_summary is None, not a BatchSummary, or if the record does not exist.
        """
        if batch_summary is None:
            raise ValueError("batch_summary cannot be None")
        if not isinstance(batch_summary, BatchSummary):
            raise ValueError("batch_summary must be a BatchSummary")

        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=batch_summary.to_item(),
                ConditionExpression="attribute_exists(PK) and attribute_exists(SK)",
            )
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "ConditionalCheckFailedException":
                raise ValueError("batch_summary does not exist") from e
            elif error_code == "ValidationException":
                raise ValueError("batch_summary is invalid") from e
            elif error_code == "InternalServerError":
                raise ValueError("internal server error") from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise ValueError("provisioned throughput exceeded") from e
            elif error_code == "ResourceNotFoundException":
                raise ValueError("table not found") from e
            else:
                raise ValueError(f"Error updating batch summary: {e}") from e

    def update_batch_summaries(
        self, batch_summaries: List[BatchSummary]
    ) -> None:
        """
        Updates multiple BatchSummary records in DynamoDB using transactions.

        Args:
            batch_summaries (List[BatchSummary]): A list of BatchSummary instances to update.

        Raises:
            ValueError: If batch_summaries is None, not a list, or contains invalid BatchSummary objects.
        """
        if batch_summaries is None:
            raise ValueError("batch_summaries cannot be None")
        if not isinstance(batch_summaries, list):
            raise ValueError("batch_summaries must be a list")
        if not all(isinstance(item, BatchSummary) for item in batch_summaries):
            raise ValueError("batch_summaries must be a list of BatchSummary")

        for i in range(0, len(batch_summaries), 25):
            chunk = batch_summaries[i : i + 25]
            transact_items = []
            for item in chunk:
                transact_items.append(
                    TransactWriteItemTypeDef(
                        Put=PutTypeDef(
                            TableName=self.table_name,
                            Item=item.to_item(),
                            ConditionExpression="attribute_exists(PK) and attribute_exists(SK)",
                        )
                    )
                )
            try:
                self._client.transact_write_items(TransactItems=transact_items)
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code == "ConditionalCheckFailedException":
                    raise ValueError(
                        "One or more batch summaries do not exist"
                    ) from e
                elif error_code == "TransactionCanceledException":
                    if "ConditionalCheckFailed" in str(e):
                        raise ValueError(
                            "One or more batch summaries do not exist"
                        ) from e
                    else:
                        raise DynamoDBError(
                            f"Transaction canceled: {e}"
                        ) from e
                elif error_code == "ProvisionedThroughputExceededException":
                    raise DynamoDBThroughputError(
                        f"Provisioned throughput exceeded: {e}"
                    ) from e
                elif error_code == "InternalServerError":
                    raise DynamoDBServerError(
                        f"Internal server error: {e}"
                    ) from e
                elif error_code == "ValidationException":
                    raise DynamoDBValidationError(
                        f"One or more parameters given were invalid: {e}"
                    ) from e
                elif error_code == "AccessDeniedException":
                    raise DynamoDBAccessError(f"Access denied: {e}") from e
                elif error_code == "ResourceNotFoundException":
                    raise DynamoDBError(f"Resource not found: {e}") from e
                else:
                    raise DynamoDBError(
                        f"Error updating batch summaries: {e}"
                    ) from e

    def delete_batch_summary(self, batch_summary: BatchSummary) -> None:
        """
        Deletes a single BatchSummary record from DynamoDB.

        Args:
            batch_summary (BatchSummary): The BatchSummary instance to delete.

        Raises:
            ValueError: If batch_summary is None, not a BatchSummary, or if the record does not exist.
        """
        if batch_summary is None:
            raise ValueError("batch_summary cannot be None")
        if not isinstance(batch_summary, BatchSummary):
            raise ValueError("batch_summary must be a BatchSummary")

        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key=batch_summary.key,
                ConditionExpression="attribute_exists(PK) and attribute_exists(SK)",
            )
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "ConditionalCheckFailedException":
                raise ValueError("batch_summary does not exist") from e
            elif error_code == "ValidationException":
                raise ValueError("batch_summary is invalid") from e
            elif error_code == "InternalServerError":
                raise ValueError("internal server error") from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise ValueError("provisioned throughput exceeded") from e
            elif error_code == "ResourceNotFoundException":
                raise ValueError("table not found") from e
            else:
                raise ValueError(f"Error deleting batch summary: {e}") from e

    def delete_batch_summaries(
        self, batch_summaries: List[BatchSummary]
    ) -> None:
        """
        Deletes multiple BatchSummary records from DynamoDB using transactions.

        Args:
            batch_summaries (List[BatchSummary]): A list of BatchSummary instances to delete.

        Raises:
            ValueError: If batch_summaries is None, not a list, or contains invalid BatchSummary objects.
        """
        if batch_summaries is None:
            raise ValueError("batch_summaries cannot be None")
        if not isinstance(batch_summaries, list):
            raise ValueError("batch_summaries must be a list")
        if not all(isinstance(item, BatchSummary) for item in batch_summaries):
            raise ValueError("batch_summaries must be a list of BatchSummary")

        for i in range(0, len(batch_summaries), 25):
            chunk = batch_summaries[i : i + 25]
            transact_items = []
            for item in chunk:
                transact_items.append(
                    TransactWriteItemTypeDef(
                        Delete=DeleteTypeDef(
                            TableName=self.table_name,
                            Key=item.key,
                            ConditionExpression="attribute_exists(PK) and attribute_exists(SK)",
                        )
                    )
                )
            try:
                self._client.transact_write_items(TransactItems=transact_items)
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code == "ConditionalCheckFailedException":
                    raise ValueError(
                        "One or more batch summaries do not exist"
                    ) from e
                elif error_code == "TransactionCanceledException":
                    if "ConditionalCheckFailed" in str(e):
                        raise ValueError(
                            "One or more batch summaries do not exist"
                        ) from e
                    else:
                        raise DynamoDBError(
                            f"Transaction canceled: {e}"
                        ) from e
                elif error_code == "ProvisionedThroughputExceededException":
                    raise DynamoDBThroughputError(
                        f"Provisioned throughput exceeded: {e}"
                    ) from e
                elif error_code == "InternalServerError":
                    raise DynamoDBServerError(
                        f"Internal server error: {e}"
                    ) from e
                elif error_code == "ValidationException":
                    raise DynamoDBValidationError(
                        f"One or more parameters given were invalid: {e}"
                    ) from e
                elif error_code == "AccessDeniedException":
                    raise DynamoDBAccessError(f"Access denied: {e}") from e
                elif error_code == "ResourceNotFoundException":
                    raise DynamoDBError(f"Resource not found: {e}") from e
                else:
                    raise DynamoDBError(
                        f"Error deleting batch summaries: {e}"
                    ) from e

    def get_batch_summary(self, batch_id: str) -> BatchSummary:
        """
        Retrieves a BatchSummary record from DynamoDB by batch_id.

        Args:
            batch_id (str): The unique identifier for the batch.

        Returns:
            BatchSummary: The corresponding BatchSummary instance.

        Raises:
            ValueError: If batch_id is not a valid string, UUID, or if the record does not exist.
        """
        if not isinstance(batch_id, str):
            raise ValueError("batch_id must be a string")
        assert_valid_uuid(batch_id)

        # The BatchSummary is templated with dummy values
        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"BATCH#{batch_id}"},
                    "SK": {"S": "STATUS"},
                },
            )
            if "Item" in response:
                return item_to_batch_summary(response["Item"])
            else:
                raise ValueError("batch_summary does not exist")
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "ResourceNotFoundException":
                raise ValueError("table not found") from e
            elif error_code == "ValidationException":
                raise ValueError("one or more parameters given were invalid")
            else:
                raise ValueError(f"Error getting batch summary: {e}") from e

    def list_batch_summaries(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> Tuple[List[BatchSummary], dict | None]:
        """
        Lists BatchSummary records from DynamoDB with optional pagination.

        Args:
            limit (int, optional): Maximum number of records to retrieve.
            last_evaluated_key (dict, optional): The key to start pagination from.

        Returns:
            Tuple[List[BatchSummary], dict | None]: A tuple containing the list of BatchSummary records and the last evaluated key.

        Raises:
            ValueError: If limit or last_evaluated_key are invalid.
        """
        if limit is not None and not isinstance(limit, int):
            raise ValueError("limit must be an integer")
        if limit is not None and limit <= 0:
            raise ValueError("limit must be greater than 0")
        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise ValueError("last_evaluated_key must be a dictionary")
            validate_last_evaluated_key(last_evaluated_key)

        summaries: List[BatchSummary] = []
        try:
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "IndexName": "GSITYPE",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "TYPE"},
                "ExpressionAttributeValues": {":val": {"S": "BATCH_SUMMARY"}},
            }
            if last_evaluated_key is not None:
                query_params["ExclusiveStartKey"] = last_evaluated_key

            while True:
                if limit is not None:
                    remaining = limit - len(summaries)
                    query_params["Limit"] = remaining

                response = self._client.query(**query_params)
                summaries.extend(
                    [item_to_batch_summary(item) for item in response["Items"]]
                )

                if limit is not None and len(summaries) >= limit:
                    summaries = summaries[:limit]
                    last_evaluated_key = response.get("LastEvaluatedKey", None)
                    break

                if "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                else:
                    last_evaluated_key = None
                    break

            return summaries, last_evaluated_key
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "ResourceNotFoundException":
                raise ValueError("table not found") from e
            elif error_code == "ValidationException":
                raise ValueError("one or more parameters given were invalid")
            elif error_code == "InternalServerError":
                raise ValueError("internal server error") from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise ValueError("provisioned throughput exceeded") from e
            else:
                raise ValueError(f"Error listing batch summaries: {e}") from e

    def get_batch_summaries_by_status(
        self,
        status: str | BatchStatus,
        batch_type: str | BatchType = "EMBEDDING",
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> Tuple[List[BatchSummary], dict | None]:
        """
        Retrieves BatchSummary records filtered by status with optional pagination.

        Args:
            status (str): The status to filter by.
            limit (int, optional): Maximum number of records to retrieve.
            last_evaluated_key (dict, optional): The key to start pagination from.

        Returns:
            Tuple[List[BatchSummary], dict | None]: A tuple containing the list of BatchSummary records and the last evaluated key.

        Raises:
            ValueError: If status is invalid or if pagination parameters are invalid.
        """
        if isinstance(status, BatchStatus):
            status_str = status.value
        elif isinstance(status, str):
            status_str = status
        else:
            raise ValueError(
                f"status must be either a BatchStatus enum or a string; got {type(status).__name__}"
            )
        valid_statuses = [s.value for s in BatchStatus]
        if status_str not in valid_statuses:
            raise ValueError(
                f"Invalid status: {status_str} must be one of {', '.join(valid_statuses)}"
            )

        if isinstance(batch_type, BatchType):
            batch_type_str = batch_type.value
        elif isinstance(batch_type, str):
            batch_type_str = batch_type
        else:
            raise ValueError(
                f"batch_type must be either a BatchType enum or a string; got {type(batch_type).__name__}"
            )

        # Validate batch_type_str against allowed values
        valid_types = [t.value for t in BatchType]
        if batch_type_str not in valid_types:
            raise ValueError(
                f"Invalid batch type: {batch_type_str} must be one of {', '.join(valid_types)}"
            )

        if limit is not None and (not isinstance(limit, int) or limit <= 0):
            raise ValueError("Limit must be a positive integer")
        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise ValueError("LastEvaluatedKey must be a dictionary")
            validate_last_evaluated_key(last_evaluated_key)

        summaries: List[BatchSummary] = []
        try:
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "IndexName": "GSI1",
                "KeyConditionExpression": "GSI1PK = :pk AND begins_with(GSI1SK, :prefix)",
                "ExpressionAttributeValues": {
                    ":pk": {"S": f"STATUS#{status_str}"},
                    ":prefix": {"S": f"BATCH_TYPE#{batch_type_str}"},
                },
            }
            if last_evaluated_key is not None:
                query_params["ExclusiveStartKey"] = last_evaluated_key

            while True:
                if limit is not None:
                    remaining = limit - len(summaries)
                    query_params["Limit"] = remaining

                response = self._client.query(**query_params)
                summaries.extend(
                    [item_to_batch_summary(item) for item in response["Items"]]
                )

                if limit is not None and len(summaries) >= limit:
                    summaries = summaries[:limit]
                    last_evaluated_key = response.get("LastEvaluatedKey", None)
                    break

                if "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                else:
                    last_evaluated_key = None
                    break

            summaries = [
                summary
                for summary in summaries
                if summary.batch_type == batch_type
            ]
            return summaries, last_evaluated_key
        except ClientError as e:
            error_code = e.response["Error"]["Code"]
            if error_code == "ResourceNotFoundException":
                raise ValueError("table not found") from e
            elif error_code == "ValidationException":
                raise ValueError("one or more parameters given were invalid")
            elif error_code == "InternalServerError":
                raise ValueError("internal server error") from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise ValueError("provisioned throughput exceeded") from e
            else:
                raise ValueError(
                    f"Error retrieving batch summaries by status: {e}"
                )
