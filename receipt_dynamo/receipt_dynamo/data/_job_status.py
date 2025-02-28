from typing import Any, List, Optional, Tuple

from botocore.exceptions import ClientError

from receipt_dynamo.entities.job_status import JobStatus, itemToJobStatus
from receipt_dynamo.entities.util import assert_valid_uuid


def validate_last_evaluated_key(lek: dict) -> None:
    required_keys = {"PK", "SK"}
    if not required_keys.issubset(lek.keys()):
        raise ValueError(f"LastEvaluatedKey must contain keys: {required_keys}")
    for key in required_keys:
        if not isinstance(lek[key], dict) or "S" not in lek[key]:
            raise ValueError(
                f"LastEvaluatedKey[{key}] must be a dict containing a key 'S'"
            )


class _JobStatus:
    def addJobStatus(self, job_status: JobStatus):
        """Adds a job status update to the database

        Args:
            job_status (JobStatus): The job status to add to the database

        Raises:
            ValueError: When a job status with the same timestamp already exists
        """
        if job_status is None:
            raise ValueError("JobStatus parameter is required and cannot be None.")
        if not isinstance(job_status, JobStatus):
            raise ValueError("job_status must be an instance of the JobStatus class.")
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=job_status.to_item(),
                ConditionExpression="attribute_not_exists(PK) OR attribute_not_exists(SK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"JobStatus with timestamp {job_status.updated_at} for job {job_status.job_id} already exists"
                ) from e
            elif error_code == "ResourceNotFoundException":
                raise Exception(f"Could not add job status to DynamoDB: {e}") from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            else:
                raise Exception(f"Could not add job status to DynamoDB: {e}") from e

    def getLatestJobStatus(self, job_id: str) -> JobStatus:
        """Gets the latest status for a job

        Args:
            job_id (str): The ID of the job to get the latest status for

        Returns:
            JobStatus: The latest job status

        Raises:
            ValueError: If the job does not exist or has no status updates
        """
        if job_id is None:
            raise ValueError("Job ID is required and cannot be None.")
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
                raise ValueError(f"No status updates found for job with ID {job_id}")

            return itemToJobStatus(response["Items"][0])
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise Exception(f"Could not get latest job status: {e}") from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "ValidationException":
                raise Exception(f"Validation error: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            else:
                raise Exception(f"Error getting latest job status: {e}") from e

    def listJobStatuses(
        self,
        job_id: str,
        limit: int = None,
        lastEvaluatedKey: dict | None = None,
    ) -> tuple[list[JobStatus], dict | None]:
        """
        Retrieve status updates for a job from the database.

        Parameters:
            job_id (str): The ID of the job to get status updates for.
            limit (int, optional): The maximum number of status updates to return.
            lastEvaluatedKey (dict, optional): A key that marks the starting point for the query.

        Returns:
            tuple:
                - A list of JobStatus objects for the specified job.
                - A dict representing the LastEvaluatedKey from the final query page, or None if no further pages.

        Raises:
            ValueError: If parameters are invalid.
            Exception: If the underlying database query fails.
        """
        if job_id is None:
            raise ValueError("Job ID is required and cannot be None.")
        assert_valid_uuid(job_id)

        if limit is not None and not isinstance(limit, int):
            raise ValueError("Limit must be an integer")
        if limit is not None and limit <= 0:
            raise ValueError("Limit must be greater than 0")
        if lastEvaluatedKey is not None:
            if not isinstance(lastEvaluatedKey, dict):
                raise ValueError("LastEvaluatedKey must be a dictionary")
            validate_last_evaluated_key(lastEvaluatedKey)

        statuses = []
        try:
            query_params = {
                "TableName": self.table_name,
                "KeyConditionExpression": "PK = :pk AND begins_with(SK, :sk)",
                "ExpressionAttributeValues": {
                    ":pk": {"S": f"JOB#{job_id}"},
                    ":sk": {"S": "STATUS#"},
                },
                "ScanIndexForward": True,
            }
            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey

            while True:
                if limit is not None:
                    remaining = limit - len(statuses)
                    query_params["Limit"] = remaining

                response = self._client.query(**query_params)
                statuses.extend([itemToJobStatus(item) for item in response["Items"]])

                if limit is not None and len(statuses) >= limit:
                    statuses = statuses[:limit]
                    last_evaluated_key = response.get("LastEvaluatedKey", None)
                    break

                if "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response["LastEvaluatedKey"]
                else:
                    last_evaluated_key = None
                    break

            return statuses, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise Exception(
                    f"Could not list job statuses from the database: {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "ValidationException":
                raise Exception(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            else:
                raise Exception(
                    f"Could not list job statuses from the database: {e}"
                ) from e

    def _getJobWithStatus(self, job_id: str) -> Tuple[Optional[Any], List[JobStatus]]:
        """Get a job with all its status updates

        Args:
            job_id (str): The ID of the job to get

        Returns:
            Tuple[Optional[Any], List[JobStatus]]: A tuple containing the job and a list of its status updates.
            The job will be None if no job was found, and the job will need to be converted to the proper type
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
            statuses = []

            for item in response["Items"]:
                if item["TYPE"]["S"] == "JOB":
                    job = item  # Return the raw item to be converted by the caller
                elif item["TYPE"]["S"] == "JOB_STATUS":
                    statuses.append(itemToJobStatus(item))

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
                        job = item  # Return the raw item to be converted by the caller
                    elif item["TYPE"]["S"] == "JOB_STATUS":
                        statuses.append(itemToJobStatus(item))

            # Sort statuses by updated_at timestamp
            statuses.sort(key=lambda s: s.updated_at)

            return job, statuses

        except ClientError as e:
            raise ValueError(f"Error getting job with status: {e}")
