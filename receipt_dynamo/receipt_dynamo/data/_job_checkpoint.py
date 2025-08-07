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
    EntityNotFoundError,
    EntityValidationError,
    OperationError,
    ReceiptDynamoError,
)
from receipt_dynamo.entities.job_checkpoint import (
    JobCheckpoint,
    item_to_job_checkpoint,
)
from receipt_dynamo.entities.util import assert_valid_uuid

if TYPE_CHECKING:
    from receipt_dynamo.data.base_operations import QueryInputTypeDef


def validate_last_evaluated_key(lek: Dict[str, Any]) -> None:
    required_keys = {"PK", "SK"}
    if not required_keys.issubset(lek.keys()):
        raise EntityValidationError(
            f"LastEvaluatedKey must contain keys: {required_keys}"
        )
    for key in required_keys:
        if not isinstance(lek[key], dict) or "S" not in lek[key]:
            raise EntityValidationError(
                f"LastEvaluatedKey[{key}] must be a dict "
                "containing a key 'S'"
            )


class _JobCheckpoint(
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
            raise EntityValidationError("job_id cannot be None")
        assert_valid_uuid(job_id)
        if not timestamp or not isinstance(timestamp, str):
            raise EntityValidationError(
                "Timestamp is required and must be a non-empty string."
            )

        result = self._get_entity(
            primary_key=f"JOB#{job_id}",
            sort_key=f"CHECKPOINT#{timestamp}",
            entity_class=JobCheckpoint,
            converter_func=item_to_job_checkpoint,
        )

        if result is None:
            raise EntityNotFoundError(
                "No job checkpoint found with job ID "
                f"{job_id} and timestamp {timestamp}"
            )

        return result

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
            raise EntityValidationError("job_id cannot be None")
        assert_valid_uuid(job_id)
        if not timestamp or not isinstance(timestamp, str):
            raise EntityValidationError(
                "Timestamp is required and must be a non-empty string."
            )

        # First verify the checkpoint exists
        try:
            self.get_job_checkpoint(job_id, timestamp)
        except ValueError as e:
            raise EntityNotFoundError(
                "Cannot update best checkpoint: "
                "No checkpoint found with job "
                f"ID {job_id} and timestamp {timestamp}"
            ) from e

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
            raise EntityValidationError("job_id cannot be None")
        assert_valid_uuid(job_id)

        if limit is not None and not isinstance(limit, int):
            raise EntityValidationError("Limit must be an integer")
        if limit is not None and limit <= 0:
            raise EntityValidationError("Limit must be greater than 0")
        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise EntityValidationError(
                    "LastEvaluatedKey must be a dictionary"
                )
            validate_last_evaluated_key(last_evaluated_key)

        return self._query_entities(
            index_name=None,
            key_condition_expression="PK = :pk AND begins_with(SK, :sk)",
            expression_attribute_names=None,
            expression_attribute_values={
                ":pk": {"S": f"JOB#{job_id}"},
                ":sk": {"S": "CHECKPOINT#"},
            },
            converter_func=item_to_job_checkpoint,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
            scan_index_forward=False,  # Descending order by default
            # (most recent first)
        )

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
            raise EntityValidationError("job_id cannot be None")
        assert_valid_uuid(job_id)

        results, _ = self._query_entities(
            index_name=None,
            key_condition_expression="PK = :pk AND begins_with(SK, :sk)",
            expression_attribute_names=None,
            expression_attribute_values={
                ":pk": {"S": f"JOB#{job_id}"},
                ":sk": {"S": "CHECKPOINT#"},
                ":is_best": {"BOOL": True},
            },
            converter_func=item_to_job_checkpoint,
            filter_expression="is_best = :is_best",
        )

        return results[0] if results else None

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
            raise EntityValidationError("job_id cannot be None")
        assert_valid_uuid(job_id)
        if not timestamp or not isinstance(timestamp, str):
            raise EntityValidationError(
                "Timestamp is required and must be a non-empty string."
            )

        # Create a checkpoint entity for deletion
        checkpoint = JobCheckpoint(
            job_id=job_id,
            timestamp=timestamp,
            s3_bucket="dummy",
            s3_key="dummy",
            size_bytes=0,
            step=0,
            epoch=0,
            model_state=True,
            optimizer_state=True,
            metrics={},
            is_best=False,
        )
        self._delete_entity(checkpoint)

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
            raise EntityValidationError("Limit must be an integer")
        if limit is not None and limit <= 0:
            raise EntityValidationError("Limit must be greater than 0")
        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise EntityValidationError(
                    "LastEvaluatedKey must be a dictionary"
                )
            validate_last_evaluated_key(last_evaluated_key)

        return self._query_entities(
            index_name="GSI1",
            key_condition_expression="GSI1PK = :pk",
            expression_attribute_names={"#type": "TYPE"},
            expression_attribute_values={
                ":pk": {"S": "CHECKPOINT"},
                ":type": {"S": "JOB_CHECKPOINT"},
            },
            converter_func=item_to_job_checkpoint,
            filter_expression="#type = :type",
            limit=limit,
            last_evaluated_key=last_evaluated_key,
            scan_index_forward=False,  # Descending order by default
        )
