from typing import TYPE_CHECKING, Any, Dict, List, Optional

from botocore.exceptions import ClientError

from receipt_dynamo.data.base_operations import (
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    DynamoDBError,
    DynamoDBServerError,
    DynamoDBThroughputError,
    DynamoDBValidationError,
    OperationError,
    ReceiptDynamoError,
)
from receipt_dynamo.entities.job_checkpoint import (
    JobCheckpoint,
    item_to_job_checkpoint,
)
from receipt_dynamo.entities.util import assert_valid_uuid

if TYPE_CHECKING:
    from receipt_dynamo.data._base import QueryInputTypeDef


def validate_last_evaluated_key(lek: Dict[str, Any]) -> None:
    required_keys = {"PK", "SK"}
    if not required_keys.issubset(lek.keys()):
        raise ValueError(
            f"LastEvaluatedKey must contain keys: {required_keys}"
        )
    for key in required_keys:
        if not isinstance(lek[key], dict) or "S" not in lek[key]:
            raise ValueError(
                f"LastEvaluatedKey[{key}] must be a dict "
                "containing a key 'S'"
            )


class _JobCheckpoint(
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
):
    """
    A class used to represent a JobCheckpoint in the database.

    Methods
    -------
    add_job_checkpoint(job_checkpoint: JobCheckpoint)
        Adds a job checkpoint to the database.
    get_job_checkpoint(job_id: str, timestamp: str) -> JobCheckpoint
        Gets a specific job checkpoint by job ID and timestamp.
    update_best_checkpoint(job_id: str, timestamp: str)
        Updates the 'is_best' flag for checkpoints in a job.
    list_job_checkpoints(job_id: str, limit: Optional[int] = None,
                         last_evaluated_key: Optional[Dict] = None)
        Lists all checkpoints for a specific job.
    get_best_checkpoint(job_id: str) -> Optional[JobCheckpoint]
        Gets the checkpoint marked as best for a job.
    delete_job_checkpoint(job_id: str, timestamp: str)
        Deletes a specific job checkpoint.
    list_all_job_checkpoints(limit: Optional[int] = None,
                             last_evaluated_key: Optional[Dict] = None)
        Lists all job checkpoints across all jobs.
    """

    @handle_dynamodb_errors("add_job_checkpoint")
    def add_job_checkpoint(self, job_checkpoint: JobCheckpoint):
        """Adds a job checkpoint to the database

        Args:
            job_checkpoint (JobCheckpoint):
                The job checkpoint to add to the database

        Raises:
            ValueError: When a job checkpoint with the same timestamp
                already exists
        """
        self._validate_entity(
            job_checkpoint,
            JobCheckpoint,
            "job_checkpoint",
        )
        self._add_entity(
            job_checkpoint,
            condition_expression=(
                "attribute_not_exists(PK) OR attribute_not_exists(SK)"
            ),
        )

    @handle_dynamodb_errors("get_job_checkpoint")
    def get_job_checkpoint(
        self,
        job_id: str,
        timestamp: str,
    ) -> JobCheckpoint:
        """Gets a specific job checkpoint by job ID and timestamp

        Args:
            job_id (str): The ID of the job
            timestamp (str): The timestamp of the checkpoint

        Returns:
            JobCheckpoint: The requested job checkpoint

        Raises:
            ValueError: If the job checkpoint does not exist
            Exception: If the request failed due to an unknown error.
        """
        if job_id is None:
            raise ValueError("job_id cannot be None")
        assert_valid_uuid(job_id)
        if not timestamp or not isinstance(timestamp, str):
            raise ValueError(
                "Timestamp is required and must be a non-empty string."
            )

        response = self._client.get_item(
            TableName=self.table_name,
            Key={
                "PK": {"S": f"JOB#{job_id}"},
                "SK": {"S": f"CHECKPOINT#{timestamp}"},
            },
        )

        if "Item" not in response:
            raise ValueError(
                "No job checkpoint found with job ID "
                f"{job_id} and timestamp {timestamp}"
            )

        return item_to_job_checkpoint(response["Item"])

    def update_best_checkpoint(self, job_id: str, timestamp: str):
        """Updates the 'is_best' flag for checkpoints in a job

        Sets is_best=True for the specified checkpoint and
        is_best=False for all others

        Args:
            job_id (str): The ID of the job
            timestamp (str):
                The timestamp of the checkpoint to mark as best

        Raises:
            ValueError:
                If parameters are invalid or the checkpoint doesn't exist
            Exception: If the request failed due to an unknown error.
        """
        if job_id is None:
            raise ValueError("job_id cannot be None")
        assert_valid_uuid(job_id)
        if not timestamp or not isinstance(timestamp, str):
            raise ValueError(
                "Timestamp is required and must be a non-empty string."
            )

        # First verify the checkpoint exists
        try:
            self.get_job_checkpoint(job_id, timestamp)
        except ValueError:
            raise ValueError(
                "Cannot update best checkpoint: "
                "No checkpoint found with job "
                f"ID {job_id} and timestamp {timestamp}"
            )

        # First, set all checkpoints for this job to is_best=False
        checkpoints, _ = self.list_job_checkpoints(job_id)
        for checkpoint in checkpoints:
            if checkpoint.timestamp != timestamp and checkpoint.is_best:
                self._client.update_item(
                    TableName=self.table_name,
                    Key={
                        "PK": {"S": f"JOB#{job_id}"},
                        "SK": {"S": f"CHECKPOINT#{checkpoint.timestamp}"},
                    },
                    UpdateExpression="SET is_best = :is_best",
                    ExpressionAttributeValues={":is_best": {"BOOL": False}},
                )

        # Then set the specified checkpoint to is_best=True
        self._client.update_item(
            TableName=self.table_name,
            Key={
                "PK": {"S": f"JOB#{job_id}"},
                "SK": {"S": f"CHECKPOINT#{timestamp}"},
            },
            UpdateExpression="SET is_best = :is_best",
            ExpressionAttributeValues={":is_best": {"BOOL": True}},
            ConditionExpression=(
                "attribute_exists(PK) AND attribute_exists(SK)"
            ),
        )

    @handle_dynamodb_errors("list_job_checkpoints")
    def list_job_checkpoints(
        self,
        job_id: str,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[JobCheckpoint], dict | None]:
        """
        Retrieve checkpoints for a job from the database.

        Parameters:
            job_id (str): The ID of the job to get checkpoints for.
            limit (int, optional):
                The maximum number of checkpoints to return.
            last_evaluated_key (dict, optional):
                A key that marks the starting point for the query.

        Returns:
            tuple:
                - A list of JobCheckpoint objects for the specified job.
                - A dict representing the LastEvaluatedKey from the final
                  query page, or None if no further pages.

        Raises:
            ValueError: If parameters are invalid.
            Exception: If the underlying database query fails.
        """
        if job_id is None:
            raise ValueError("job_id cannot be None")
        assert_valid_uuid(job_id)

        if limit is not None and not isinstance(limit, int):
            raise ValueError("Limit must be an integer")
        if limit is not None and limit <= 0:
            raise ValueError("Limit must be greater than 0")
        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise ValueError("LastEvaluatedKey must be a dictionary")
            validate_last_evaluated_key(last_evaluated_key)

        checkpoints: List[JobCheckpoint] = []
        try:
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "KeyConditionExpression": (
                    "PK = :pk AND begins_with(SK, :sk)"
                ),
                "ExpressionAttributeValues": {
                    ":pk": {"S": f"JOB#{job_id}"},
                    ":sk": {"S": "CHECKPOINT#"},
                },
                # Descending order by default (most recent first)
                "ScanIndexForward": False,
            }

            if last_evaluated_key is not None:
                query_params["ExclusiveStartKey"] = last_evaluated_key

            while True:
                if limit is not None:
                    remaining = limit - len(checkpoints)
                    query_params["Limit"] = remaining

                response = self._client.query(**query_params)
                for item in response["Items"]:
                    if item.get("TYPE", {}).get("S") == "JOB_CHECKPOINT":
                        checkpoints.append(item_to_job_checkpoint(item))

                if limit is not None and len(checkpoints) >= limit:
                    checkpoints = checkpoints[:limit]
                    last_evaluated_key = response.get(
                        "LastEvaluatedKey",
                        None,
                    )
                    break

                if "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                else:
                    last_evaluated_key = None
                    break

            return checkpoints, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    "Could not list job checkpoints from the database: " f"{e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                ) from e
            elif error_code == "ValidationException":
                raise DynamoDBValidationError(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}") from e
            else:
                raise OperationError(
                    f"Error listing job checkpoints: {e}"
                ) from e

    @handle_dynamodb_errors("get_best_checkpoint")
    def get_best_checkpoint(self, job_id: str) -> Optional[JobCheckpoint]:
        """
        Retrieve the best checkpoint for a job from the database.

        Parameters:
            job_id (str):
                The ID of the job to get the best checkpoint for.

        Returns:
            Optional[JobCheckpoint]:
                The best checkpoint for the job, or None if no best
                checkpoint exists.

        Raises:
            ValueError: If parameters are invalid.
            Exception: If the underlying database query fails.
        """
        if job_id is None:
            raise ValueError("job_id cannot be None")
        assert_valid_uuid(job_id)

        try:
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "KeyConditionExpression": (
                    "PK = :pk AND begins_with(SK, :sk)"
                ),
                "FilterExpression": "is_best = :is_best",
                "ExpressionAttributeValues": {
                    ":pk": {"S": f"JOB#{job_id}"},
                    ":sk": {"S": "CHECKPOINT#"},
                    ":is_best": {"BOOL": True},
                },
            }

            response = self._client.query(**query_params)

            for item in response["Items"]:
                if item.get("TYPE", {}).get("S") == "JOB_CHECKPOINT":
                    return item_to_job_checkpoint(item)

            return None
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise ReceiptDynamoError(
                    f"Could not get best checkpoint: {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                ) from e
            elif error_code == "ValidationException":
                raise DynamoDBValidationError(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}") from e
            else:
                raise OperationError(
                    f"Error getting best checkpoint: {e}"
                ) from e

    @handle_dynamodb_errors("delete_job_checkpoint")
    def delete_job_checkpoint(self, job_id: str, timestamp: str):
        """
        Delete a job checkpoint from the database.

        Parameters:
            job_id (str): The ID of the job the checkpoint belongs to.
            timestamp (str): The timestamp of the checkpoint to delete.

        Raises:
            ValueError: If parameters are invalid.
            Exception: If the underlying database query fails.
        """
        if job_id is None:
            raise ValueError("job_id cannot be None")
        assert_valid_uuid(job_id)
        if not timestamp or not isinstance(timestamp, str):
            raise ValueError(
                "Timestamp is required and must be a non-empty string."
            )

        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"JOB#{job_id}"},
                    "SK": {"S": f"CHECKPOINT#{timestamp}"},
                },
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise ReceiptDynamoError(
                    f"Could not delete job checkpoint: {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}") from e
            else:
                raise OperationError(
                    f"Error deleting job checkpoint: {e}"
                ) from e

    @handle_dynamodb_errors("list_all_job_checkpoints")
    def list_all_job_checkpoints(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[JobCheckpoint], dict | None]:
        """
        Retrieve all checkpoints across all jobs from the database.

        Parameters:
            limit (int, optional):
                The maximum number of checkpoints to return.
            last_evaluated_key (dict, optional):
                A key that marks the starting point for the query.

        Returns:
            tuple:
                - A list of JobCheckpoint objects.
                - A dict representing the LastEvaluatedKey from the final
                  query page, or None if no further pages.

        Raises:
            ValueError: If parameters are invalid.
            Exception: If the underlying database query fails.
        """
        if limit is not None and not isinstance(limit, int):
            raise ValueError("Limit must be an integer")
        if limit is not None and limit <= 0:
            raise ValueError("Limit must be greater than 0")
        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise ValueError("LastEvaluatedKey must be a dictionary")
            validate_last_evaluated_key(last_evaluated_key)

        checkpoints: List[JobCheckpoint] = []
        try:
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "IndexName": "GSI1",
                "KeyConditionExpression": "GSI1PK = :pk",
                "ExpressionAttributeValues": {
                    ":pk": {"S": "CHECKPOINT"},
                },
                # Descending order by default (most recent first)
                "ScanIndexForward": False,
            }

            if last_evaluated_key is not None:
                query_params["ExclusiveStartKey"] = last_evaluated_key

            while True:
                if limit is not None:
                    remaining = limit - len(checkpoints)
                    query_params["Limit"] = remaining

                response = self._client.query(**query_params)
                for item in response["Items"]:
                    if item.get("TYPE", {}).get("S") == "JOB_CHECKPOINT":
                        checkpoints.append(item_to_job_checkpoint(item))

                if limit is not None and len(checkpoints) >= limit:
                    checkpoints = checkpoints[:limit]
                    last_evaluated_key = response.get(
                        "LastEvaluatedKey",
                        None,
                    )
                    break

                if "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                else:
                    last_evaluated_key = None
                    break

            return checkpoints, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    "Could not list all job checkpoints from the database: "
                    f"{e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                ) from e
            elif error_code == "ValidationException":
                raise DynamoDBValidationError(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}") from e
            else:
                raise OperationError(
                    f"Error listing all job checkpoints: {e}"
                ) from e
