from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

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
    EntityAlreadyExistsError,
    EntityNotFoundError,
    EntityValidationError,
    OperationError,
)
from receipt_dynamo.entities.job_status import JobStatus, item_to_job_status
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
                f"LastEvaluatedKey[{key}] must be a dict containing a key 'S'"
            )


class _JobStatus(
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
):
    @handle_dynamodb_errors("add_job_status")
    def add_job_status(self, job_status: JobStatus):
        """Adds a job status update to the database

        Args:
            job_status (JobStatus): The job status to add to the database

        Raises:
            ValueError: When a job status with the same timestamp already
                exists
        """
        if job_status is None:
            raise EntityValidationError("job_status cannot be None")
        if not isinstance(job_status, JobStatus):
            raise EntityValidationError(
                "job_status must be an instance of the JobStatus class."
            )
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=job_status.to_item(),
                ConditionExpression=(
                    "attribute_not_exists(PK) OR attribute_not_exists(SK)"
                ),
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise EntityAlreadyExistsError(
                    f"JobStatus with timestamp {job_status.updated_at} for "
                    f"job {job_status.job_id} already exists"
                ) from e
            if error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    f"Could not add job status to DynamoDB: {e}"
                ) from e
            if error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                ) from e
            if error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}") from e
            raise DynamoDBError(
                f"Could not add job status to DynamoDB: {e}"
            ) from e

    @handle_dynamodb_errors("get_latest_job_status")
    def get_latest_job_status(self, job_id: str) -> JobStatus:
        """Gets the latest status for a job

        Args:
            job_id (str): The ID of the job to get the latest status for

        Returns:
            JobStatus: The latest job status

        Raises:
            ValueError: If the job does not exist or has no status updates
        """
        if job_id is None:
            raise EntityValidationError("job_id cannot be None")
        assert_valid_uuid(job_id)

        try:
            response = self._client.query(
                TableName=self.table_name,
                KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
                ExpressionAttributeValues={
                    ":pk": {"S": f"JOB#{job_id}"},
                    ":sk": {"S": "STATUS#"},
                },
                ScanIndexForward=False,  # Descending order based on sort key
                Limit=1,  # We only want the most recent one)
            )

            if not response["Items"]:
                raise EntityNotFoundError(
                    f"No status updates found for job with ID {job_id}"
                )

            return item_to_job_status(response["Items"][0])
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    f"Could not get latest job status: {e}"
                ) from e
            if error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                ) from e
            if error_code == "ValidationException":
                raise OperationError(f"Validation error: {e}") from e
            if error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}") from e
            raise OperationError(
                f"Error getting latest job status: {e}"
            ) from e

    def list_job_statuses(
        self,
        job_id: str,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[JobStatus], dict | None]:
        """
        Retrieve status updates for a job from the database.

        Parameters:
            job_id (str): The ID of the job to get status updates for.
            limit (int, optional): The maximum number of status updates to
                return.
            last_evaluated_key (dict, optional): A key that marks the
                starting point for the query.

        Returns:
            tuple:
                - A list of JobStatus objects for the specified job.
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

        statuses: List[JobStatus] = []
        try:
            query_params: QueryInputTypeDef = {
                "TableName": self.table_name,
                "KeyConditionExpression": "PK = :pk AND begins_with(SK, :sk)",
                "ExpressionAttributeValues": {
                    ":pk": {"S": f"JOB#{job_id}"},
                    ":sk": {"S": "STATUS#"},
                },
                "ScanIndexForward": True,
            }
            if last_evaluated_key is not None:
                query_params["ExclusiveStartKey"] = last_evaluated_key

            while True:
                if limit is not None:
                    remaining = limit - len(statuses)
                    query_params["Limit"] = remaining

                response = self._client.query(**query_params)
                statuses.extend(
                    [item_to_job_status(item) for item in response["Items"]]
                )

                if limit is not None and len(statuses) >= limit:
                    statuses = statuses[:limit]
                    last_evaluated_key = response.get("LastEvaluatedKey", None)
                    break

                if "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                else:
                    last_evaluated_key = None
                    break

            return statuses, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise DynamoDBError(
                    f"Could not list job statuses from the database: {e}"
                ) from e
            if error_code == "ProvisionedThroughputExceededException":
                raise DynamoDBThroughputError(
                    f"Provisioned throughput exceeded: {e}"
                ) from e
            if error_code == "ValidationException":
                raise DynamoDBValidationError(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            if error_code == "InternalServerError":
                raise DynamoDBServerError(f"Internal server error: {e}") from e
            raise DynamoDBError(
                f"Could not list job statuses from the database: {e}"
            ) from e

    def _getJobWithStatus(
        self, job_id: str
    ) -> Tuple[Optional[Any], List[JobStatus]]:
        """Get a job with all its status updates

        Args:
            job_id (str): The ID of the job to get

        Returns:
            Tuple[Optional[Any], List[JobStatus]]: A tuple containing the job
                and a list of its status updates.
            The job will be None if no job was found, and the job will need
            to be converted to the proper type
            by the calling class.
        """
        try:
            response = self._client.query(
                TableName=self.table_name,
                KeyConditionExpression="PK = :pk",
                ExpressionAttributeValues={
                    ":pk": {"S": f"JOB#{job_id}"},
                },
            )

            job = None
            statuses: List[JobStatus] = []

            for item in response["Items"]:
                if item["TYPE"]["S"] == "JOB":
                    # Return the raw item to be converted by the caller
                    job = item
                if item["TYPE"]["S"] == "JOB_STATUS":
                    statuses.append(item_to_job_status(item))

            # Continue pagination if needed
            while "LastEvaluatedKey" in response:
                response = self._client.query(
                    TableName=self.table_name,
                    KeyConditionExpression="PK = :pk",
                    ExpressionAttributeValues={
                        ":pk": {"S": f"JOB#{job_id}"},
                    },
                    ExclusiveStartKey=response["LastEvaluatedKey"],
                )

                for item in response["Items"]:
                    if item["TYPE"]["S"] == "JOB":
                        # Return the raw item to be converted by the caller
                        job = item
                    if item["TYPE"]["S"] == "JOB_STATUS":
                        statuses.append(item_to_job_status(item))

            # Sort statuses by updated_at timestamp
            statuses.sort(key=lambda s: s.updated_at)

            return job, statuses

        except ClientError as e:
            raise EntityValidationError(
                f"Error getting job with status: {e}"
            ) from e
