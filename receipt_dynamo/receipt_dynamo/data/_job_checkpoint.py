from typing import Optional

from botocore.exceptions import ClientError

from receipt_dynamo.data._base import DynamoClientProtocol
from receipt_dynamo.entities.job_checkpoint import (JobCheckpoint,
                                                    itemToJobCheckpoint)
from receipt_dynamo.entities.util import assert_valid_uuid


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


class _JobCheckpoint(DynamoClientProtocol):
    def addJobCheckpoint(self, job_checkpoint: JobCheckpoint):
        """Adds a job checkpoint to the database

        Args:
            job_checkpoint (JobCheckpoint): The job checkpoint to add to the database

        Raises:
            ValueError: When a job checkpoint with the same timestamp already exists
        """
        if job_checkpoint is None:
            raise ValueError(
                "JobCheckpoint parameter is required and cannot be None."
            )
        if not isinstance(job_checkpoint, JobCheckpoint):
            raise ValueError(
                "job_checkpoint must be an instance of the JobCheckpoint class."
            )
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=job_checkpoint.to_item(),
                ConditionExpression="attribute_not_exists(PK) OR attribute_not_exists(SK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"JobCheckpoint with timestamp {job_checkpoint.timestamp} for job {job_checkpoint.job_id} already exists"
                ) from e
            elif error_code == "ResourceNotFoundException":
                raise Exception(
                    f"Could not add job checkpoint to DynamoDB: {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            else:
                raise Exception(
                    f"Could not add job checkpoint to DynamoDB: {e}"
                ) from e

    def getJobCheckpoint(self, job_id: str, timestamp: str) -> JobCheckpoint:
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
            raise ValueError("Job ID is required and cannot be None.")
        assert_valid_uuid(job_id)
        if not timestamp or not isinstance(timestamp, str):
            raise ValueError(
                "Timestamp is required and must be a non-empty string."
            )

        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"JOB#{job_id}"},
                    "SK": {"S": f"CHECKPOINT#{timestamp}"},
                },
            )

            if "Item" not in response:
                raise ValueError(
                    f"No job checkpoint found with job ID {job_id} and timestamp {timestamp}"
                )

            return itemToJobCheckpoint(response["Item"])
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise Exception(f"Could not get job checkpoint: {e}") from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            else:
                raise Exception(f"Error getting job checkpoint: {e}") from e

    def updateBestCheckpoint(self, job_id: str, timestamp: str):
        """Updates the 'is_best' flag for checkpoints in a job

        Sets is_best=True for the specified checkpoint and is_best=False for all others

        Args:
            job_id (str): The ID of the job
            timestamp (str): The timestamp of the checkpoint to mark as best

        Raises:
            ValueError: If parameters are invalid or the checkpoint doesn't exist
            Exception: If the request failed due to an unknown error.
        """
        if job_id is None:
            raise ValueError("Job ID is required and cannot be None.")
        assert_valid_uuid(job_id)
        if not timestamp or not isinstance(timestamp, str):
            raise ValueError(
                "Timestamp is required and must be a non-empty string."
            )

        # First verify the checkpoint exists
        try:
            self.getJobCheckpoint(job_id, timestamp)
        except ValueError:
            raise ValueError(
                f"Cannot update best checkpoint: No checkpoint found with job ID {job_id} and timestamp {timestamp}"
            )

        try:
            # First, set all checkpoints for this job to is_best=False
            checkpoints, _ = self.listJobCheckpoints(job_id)
            for checkpoint in checkpoints:
                if checkpoint.timestamp != timestamp and checkpoint.is_best:
                    self._client.update_item(
                        TableName=self.table_name,
                        Key={
                            "PK": {"S": f"JOB#{job_id}"},
                            "SK": {"S": f"CHECKPOINT#{checkpoint.timestamp}"},
                        },
                        UpdateExpression="SET is_best = :is_best",
                        ExpressionAttributeValues={
                            ":is_best": {"BOOL": False}
                        },
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
                ConditionExpression="attribute_exists(PK) AND attribute_exists(SK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"No job checkpoint found with job ID {job_id} and timestamp {timestamp}"
                ) from e
            elif error_code == "ResourceNotFoundException":
                raise Exception(
                    f"Could not update best checkpoint: {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            else:
                raise Exception(f"Error updating best checkpoint: {e}") from e

    def listJobCheckpoints(
        self,
        job_id: str,
        limit: int = None,
        lastEvaluatedKey: dict | None = None,
    ) -> tuple[list[JobCheckpoint], dict | None]:
        """
        Retrieve checkpoints for a job from the database.

        Parameters:
            job_id (str): The ID of the job to get checkpoints for.
            limit (int, optional): The maximum number of checkpoints to return.
            lastEvaluatedKey (dict, optional): A key that marks the starting point for the query.

        Returns:
            tuple:
                - A list of JobCheckpoint objects for the specified job.
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

        checkpoints = []
        try:
            query_params = {
                "TableName": self.table_name,
                "KeyConditionExpression": "PK = :pk AND begins_with(SK, :sk)",
                "ExpressionAttributeValues": {
                    ":pk": {"S": f"JOB#{job_id}"},
                    ":sk": {"S": "CHECKPOINT#"},
                },
                # Descending order by default (most recent first)
                "ScanIndexForward": False,
            }

            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey

            while True:
                if limit is not None:
                    remaining = limit - len(checkpoints)
                    query_params["Limit"] = remaining

                response = self._client.query(**query_params)
                for item in response["Items"]:
                    if item.get("TYPE", {}).get("S") == "JOB_CHECKPOINT":
                        checkpoints.append(itemToJobCheckpoint(item))

                if limit is not None and len(checkpoints) >= limit:
                    checkpoints = checkpoints[:limit]
                    last_evaluated_key = response.get("LastEvaluatedKey", None)
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
                raise Exception(
                    f"Could not list job checkpoints from the database: {e}"
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
                raise Exception(f"Error listing job checkpoints: {e}") from e

    def getBestCheckpoint(self, job_id: str) -> Optional[JobCheckpoint]:
        """
        Retrieve the best checkpoint for a job from the database.

        Parameters:
            job_id (str): The ID of the job to get the best checkpoint for.

        Returns:
            Optional[JobCheckpoint]: The best checkpoint for the job, or None if no best checkpoint exists.

        Raises:
            ValueError: If parameters are invalid.
            Exception: If the underlying database query fails.
        """
        if job_id is None:
            raise ValueError("Job ID is required and cannot be None.")
        assert_valid_uuid(job_id)

        try:
            query_params = {
                "TableName": self.table_name,
                "KeyConditionExpression": "PK = :pk AND begins_with(SK, :sk)",
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
                    return itemToJobCheckpoint(item)

            return None
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise Exception(f"Could not get best checkpoint: {e}") from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "ValidationException":
                raise Exception(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            else:
                raise Exception(f"Error getting best checkpoint: {e}") from e

    def deleteJobCheckpoint(self, job_id: str, timestamp: str):
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
            raise ValueError("Job ID is required and cannot be None.")
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
                raise Exception(f"Could not delete job checkpoint: {e}") from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            else:
                raise Exception(f"Error deleting job checkpoint: {e}") from e

    def listAllJobCheckpoints(
        self, limit: int = None, lastEvaluatedKey: dict | None = None
    ) -> tuple[list[JobCheckpoint], dict | None]:
        """
        Retrieve all checkpoints across all jobs from the database.

        Parameters:
            limit (int, optional): The maximum number of checkpoints to return.
            lastEvaluatedKey (dict, optional): A key that marks the starting point for the query.

        Returns:
            tuple:
                - A list of JobCheckpoint objects.
                - A dict representing the LastEvaluatedKey from the final query page, or None if no further pages.

        Raises:
            ValueError: If parameters are invalid.
            Exception: If the underlying database query fails.
        """
        if limit is not None and not isinstance(limit, int):
            raise ValueError("Limit must be an integer")
        if limit is not None and limit <= 0:
            raise ValueError("Limit must be greater than 0")
        if lastEvaluatedKey is not None:
            if not isinstance(lastEvaluatedKey, dict):
                raise ValueError("LastEvaluatedKey must be a dictionary")
            validate_last_evaluated_key(lastEvaluatedKey)

        checkpoints = []
        try:
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSI1",
                "KeyConditionExpression": "GSI1PK = :pk",
                "ExpressionAttributeValues": {
                    ":pk": {"S": "CHECKPOINT"},
                },
                # Descending order by default (most recent first)
                "ScanIndexForward": False,
            }

            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey

            while True:
                if limit is not None:
                    remaining = limit - len(checkpoints)
                    query_params["Limit"] = remaining

                response = self._client.query(**query_params)
                for item in response["Items"]:
                    if item.get("TYPE", {}).get("S") == "JOB_CHECKPOINT":
                        checkpoints.append(itemToJobCheckpoint(item))

                if limit is not None and len(checkpoints) >= limit:
                    checkpoints = checkpoints[:limit]
                    last_evaluated_key = response.get("LastEvaluatedKey", None)
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
                raise Exception(
                    f"Could not list all job checkpoints from the database: {e}"
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
                    f"Error listing all job checkpoints: {e}"
                ) from e
