from typing import List, Tuple

from botocore.exceptions import ClientError

from receipt_dynamo.entities.job import Job, itemToJob
from receipt_dynamo.entities.job_status import JobStatus
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


class _Job:
    def addJob(self, job: Job):
        """Adds a job to the database

        Args:
            job (Job): The job to add to the database

        Raises:
            ValueError: When a job with the same ID already exists
        """
        if job is None:
            raise ValueError("Job parameter is required and cannot be None.")
        if not isinstance(job, Job):
            raise ValueError("job must be an instance of the Job class.")
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=job.to_item(),
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"Job with ID {
                        job.job_id} already exists"
                ) from e
            elif error_code == "ResourceNotFoundException":
                raise Exception(f"Could not add job to DynamoDB: {e}") from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            else:
                raise Exception(f"Could not add job to DynamoDB: {e}") from e

    def addJobs(self, jobs: list[Job]):
        """Adds a list of jobs to the database

        Args:
            jobs (list[Job]): The jobs to add to the database

        Raises:
            ValueError: When a job with the same ID already exists
        """
        if jobs is None:
            raise ValueError("Jobs parameter is required and cannot be None.")
        if not isinstance(jobs, list):
            raise ValueError("jobs must be a list of Job instances.")
        if not all(isinstance(job, Job) for job in jobs):
            raise ValueError("All jobs must be instances of the Job class.")
        try:
            for i in range(0, len(jobs), 25):
                chunk = jobs[i : i + 25]
                request_items = [
                    {"PutRequest": {"Item": job.to_item()}} for job in chunk
                ]
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
                # Handle unprocessed items if they exist
                unprocessed = response.get("UnprocessedItems", {})
                while unprocessed.get(self.table_name):
                    # If there are unprocessed items, retry them
                    response = self._client.batch_write_item(
                        RequestItems=unprocessed
                    )
                    unprocessed = response.get("UnprocessedItems", {})
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            elif error_code == "ValidationException":
                raise Exception(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "AccessDeniedException":
                raise Exception(f"Access denied: {e}") from e
            else:
                raise ValueError(f"Error adding jobs: {e}")

    def updateJob(self, job: Job):
        """Updates a job in the database

        Args:
            job (Job): The job to update in the database

        Raises:
            ValueError: When the job does not exist
        """
        if job is None:
            raise ValueError("Job parameter is required and cannot be None.")
        if not isinstance(job, Job):
            raise ValueError("job must be an instance of the Job class.")

        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=job.to_item(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(f"Job with ID {job.job_id} does not exist")
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            elif error_code == "ValidationException":
                raise Exception(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "AccessDeniedException":
                raise Exception(f"Access denied: {e}") from e
            else:
                raise ValueError(f"Error updating job: {e}")

    def updateJobs(self, jobs: list[Job]):
        """
        Updates a list of jobs in the database using transactions.
        Each job update is conditional upon the job already existing.

        Since DynamoDB's transact_write_items supports a maximum of 25 operations per call,
        the list of jobs is split into chunks of 25 items or less. Each chunk is updated
        in a separate transaction.

        Args:
            jobs (list[Job]): The jobs to update in the database.

        Raises:
            ValueError: When given a bad parameter.
            Exception: For underlying DynamoDB errors.
        """
        if jobs is None:
            raise ValueError("Jobs parameter is required and cannot be None.")
        if not isinstance(jobs, list):
            raise ValueError("jobs must be a list of Job instances.")
        if not all(isinstance(job, Job) for job in jobs):
            raise ValueError("All jobs must be instances of the Job class.")

        # Process jobs in chunks of 25 because transact_write_items supports a
        # maximum of 25 operations.
        for i in range(0, len(jobs), 25):
            chunk = jobs[i : i + 25]
            transact_items = []
            for job in chunk:
                transact_items.append(
                    {
                        "Put": {
                            "TableName": self.table_name,
                            "Item": job.to_item(),
                            "ConditionExpression": "attribute_exists(PK)",
                        }
                    }
                )
            try:
                self._client.transact_write_items(TransactItems=transact_items)
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code == "ConditionalCheckFailedException":
                    raise ValueError("One or more jobs do not exist") from e
                elif error_code == "ProvisionedThroughputExceededException":
                    raise Exception(
                        f"Provisioned throughput exceeded: {e}"
                    ) from e
                elif error_code == "InternalServerError":
                    raise Exception(f"Internal server error: {e}") from e
                elif error_code == "ValidationException":
                    raise Exception(
                        f"One or more parameters given were invalid: {e}"
                    ) from e
                elif error_code == "AccessDeniedException":
                    raise Exception(f"Access denied: {e}") from e
                else:
                    raise ValueError(f"Error updating jobs: {e}") from e

    def deleteJob(self, job: Job):
        """Deletes a job from the database

        Args:
            job (Job): The job to delete from the database

        Raises:
            ValueError: When the job does not exist
        """
        if job is None:
            raise ValueError("Job parameter is required and cannot be None.")
        if not isinstance(job, Job):
            raise ValueError("job must be an instance of the Job class.")
        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key=job.key(),
                ConditionExpression="attribute_exists(PK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(f"Job with ID {job.job_id} does not exist")
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            elif error_code == "ValidationException":
                raise Exception(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "AccessDeniedException":
                raise Exception(f"Access denied: {e}") from e
            else:
                raise ValueError(f"Error deleting job: {e}") from e

    def deleteJobs(self, jobs: list[Job]):
        """
        Deletes a list of jobs from the database using transactions.
        Each delete operation is conditional upon the job existing.

        Args:
            jobs (list[Job]): The jobs to delete from the database.

        Raises:
            ValueError: When a job does not exist or if another error occurs.
        """
        if jobs is None:
            raise ValueError("Jobs parameter is required and cannot be None.")
        if not isinstance(jobs, list):
            raise ValueError("jobs must be a list of Job instances.")
        if not all(isinstance(job, Job) for job in jobs):
            raise ValueError("All jobs must be instances of the Job class.")

        try:
            # Process jobs in chunks of 25 items (the maximum allowed per
            # transaction)
            for i in range(0, len(jobs), 25):
                chunk = jobs[i : i + 25]
                transact_items = []
                for job in chunk:
                    transact_items.append(
                        {
                            "Delete": {
                                "TableName": self.table_name,
                                "Key": job.key(),
                                "ConditionExpression": "attribute_exists(PK)",
                            }
                        }
                    )
                # Execute the transaction for this chunk.
                self._client.transact_write_items(TransactItems=transact_items)
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError("One or more jobs do not exist") from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            elif error_code == "ValidationException":
                raise Exception(
                    f"One or more parameters given were invalid: {e}"
                ) from e
            elif error_code == "AccessDeniedException":
                raise Exception(f"Access denied: {e}") from e
            else:
                raise ValueError(f"Error deleting jobs: {e}") from e

    def getJob(self, job_id: str) -> Job:
        """
        Retrieves a job from the database.

        Args:
            job_id (str): The ID of the job to retrieve.

        Returns:
            Job: The job object.

        Raises:
            ValueError: If input parameters are invalid or if the job does not exist.
            Exception: For underlying DynamoDB errors.
        """
        if job_id is None:
            raise ValueError("Job ID is required and cannot be None.")

        # Validate job_id as a UUID
        assert_valid_uuid(job_id)

        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"JOB#{job_id}"},
                    "SK": {"S": "JOB"},
                },
            )
            if "Item" in response:
                return itemToJob(response["Item"])
            else:
                raise ValueError(f"Job with ID {job_id} does not exist.")
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "ValidationException":
                raise Exception(f"Validation error: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            elif error_code == "AccessDeniedException":
                raise Exception(f"Access denied: {e}") from e
            else:
                raise Exception(f"Error getting job: {e}") from e

    def getJobWithStatus(self, job_id: str) -> Tuple[Job, List[JobStatus]]:
        """Get a job with all its status updates

        Args:
            job_id (str): The ID of the job to get

        Returns:
            Tuple[Job, List[JobStatus]]: A tuple containing the job and a list of its status updates
        """
        # Use the protected method instead of trying to access a private method
        job_item, statuses = self._getJobWithStatus(job_id)

        if job_item is None:
            raise ValueError(f"Job with ID {job_id} does not exist.")

        job = itemToJob(job_item)

        return job, statuses

    def listJobs(
        self, limit: int = None, lastEvaluatedKey: dict | None = None
    ) -> tuple[list[Job], dict | None]:
        """
        Retrieve job records from the database with support for pagination.

        Parameters:
            limit (int, optional): The maximum number of job items to return. If None, all jobs are fetched.
            lastEvaluatedKey (dict, optional): A key that marks the starting point for the query.

        Returns:
            tuple:
                - A list of Job objects, containing up to 'limit' items if specified.
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

        jobs = []
        try:
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSITYPE",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "TYPE"},
                "ExpressionAttributeValues": {":val": {"S": "JOB"}},
            }
            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey

            while True:
                # If a limit is provided, adjust the query's Limit to only
                # fetch what is needed.
                if limit is not None:
                    remaining = limit - len(jobs)
                    query_params["Limit"] = remaining

                response = self._client.query(**query_params)
                jobs.extend([itemToJob(item) for item in response["Items"]])

                # If we have reached or exceeded the limit, trim the list and
                # break.
                if limit is not None and len(jobs) >= limit:
                    jobs = jobs[:limit]  # ensure we return exactly the limit
                    last_evaluated_key = response.get("LastEvaluatedKey", None)
                    break

                # Continue paginating if there's more data; otherwise, we're
                # done.
                if "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                else:
                    last_evaluated_key = None
                    break

            return jobs, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise Exception(
                    f"Could not list jobs from the database: {e}"
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
                    f"Could not list jobs from the database: {e}"
                ) from e

    def listJobsByStatus(
        self,
        status: str,
        limit: int = None,
        lastEvaluatedKey: dict | None = None,
    ) -> tuple[list[Job], dict | None]:
        """
        Retrieve job records filtered by status from the database.

        Parameters:
            status (str): The status to filter by.
            limit (int, optional): The maximum number of job items to return.
            lastEvaluatedKey (dict, optional): A key that marks the starting point for the query.

        Returns:
            tuple:
                - A list of Job objects with the specified status.
                - A dict representing the LastEvaluatedKey from the final query page, or None if no further pages.

        Raises:
            ValueError: If parameters are invalid.
            Exception: If the underlying database query fails.
        """
        valid_statuses = [
            "pending",
            "running",
            "succeeded",
            "failed",
            "cancelled",
            "interrupted",
        ]
        if not isinstance(status, str) or status.lower() not in valid_statuses:
            raise ValueError(f"status must be one of {valid_statuses}")

        if limit is not None and not isinstance(limit, int):
            raise ValueError("Limit must be an integer")
        if limit is not None and limit <= 0:
            raise ValueError("Limit must be greater than 0")
        if lastEvaluatedKey is not None:
            if not isinstance(lastEvaluatedKey, dict):
                raise ValueError("LastEvaluatedKey must be a dictionary")
            # Validate the LastEvaluatedKey structure specific to GSI1
            if not all(
                k in lastEvaluatedKey for k in ["PK", "SK", "GSI1PK", "GSI1SK"]
            ):
                raise ValueError(
                    "LastEvaluatedKey must contain PK, SK, GSI1PK, and GSI1SK keys"
                )

        jobs = []
        try:
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSI1",
                "KeyConditionExpression": "GSI1PK = :status",
                "ExpressionAttributeValues": {
                    ":status": {"S": f"STATUS#{status.lower()}"},
                },
                "FilterExpression": "#type = :job_type",
                "ExpressionAttributeNames": {"#type": "TYPE"},
                "ExpressionAttributeValues": {
                    ":status": {"S": f"STATUS#{status.lower()}"},
                    ":job_type": {"S": "JOB"},
                },
            }
            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey

            while True:
                # If a limit is provided, adjust the query's Limit to only
                # fetch what is needed.
                if limit is not None:
                    remaining = limit - len(jobs)
                    query_params["Limit"] = remaining

                response = self._client.query(**query_params)
                jobs.extend([itemToJob(item) for item in response["Items"]])

                # If we have reached or exceeded the limit, trim the list and
                # break.
                if limit is not None and len(jobs) >= limit:
                    jobs = jobs[:limit]  # ensure we return exactly the limit
                    last_evaluated_key = response.get("LastEvaluatedKey", None)
                    break

                # Continue paginating if there's more data; otherwise, we're
                # done.
                if "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                else:
                    last_evaluated_key = None
                    break

            return jobs, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise Exception(
                    f"Could not list jobs by status from the database: {e}"
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
                    f"Could not list jobs by status from the database: {e}"
                ) from e

    def listJobsByUser(
        self,
        user_id: str,
        limit: int = None,
        lastEvaluatedKey: dict | None = None,
    ) -> tuple[list[Job], dict | None]:
        """
        Retrieve job records created by a specific user from the database.

        Parameters:
            user_id (str): The ID of the user who created the jobs.
            limit (int, optional): The maximum number of job items to return.
            lastEvaluatedKey (dict, optional): A key that marks the starting point for the query.

        Returns:
            tuple:
                - A list of Job objects created by the specified user.
                - A dict representing the LastEvaluatedKey from the final query page, or None if no further pages.

        Raises:
            ValueError: If parameters are invalid.
            Exception: If the underlying database query fails.
        """
        if not isinstance(user_id, str) or not user_id:
            raise ValueError("user_id must be a non-empty string")

        if limit is not None and not isinstance(limit, int):
            raise ValueError("Limit must be an integer")
        if limit is not None and limit <= 0:
            raise ValueError("Limit must be greater than 0")
        if lastEvaluatedKey is not None:
            if not isinstance(lastEvaluatedKey, dict):
                raise ValueError("LastEvaluatedKey must be a dictionary")
            # Validate the LastEvaluatedKey structure specific to GSI2
            if not all(
                k in lastEvaluatedKey for k in ["PK", "SK", "GSI2PK", "GSI2SK"]
            ):
                raise ValueError(
                    "LastEvaluatedKey must contain PK, SK, GSI2PK, and GSI2SK keys"
                )

        jobs = []
        try:
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSI2",
                "KeyConditionExpression": "GSI2PK = :user",
                "ExpressionAttributeValues": {
                    ":user": {"S": f"USER#{user_id}"},
                },
                "FilterExpression": "#type = :job_type",
                "ExpressionAttributeNames": {"#type": "TYPE"},
                "ExpressionAttributeValues": {
                    ":user": {"S": f"USER#{user_id}"},
                    ":job_type": {"S": "JOB"},
                },
            }
            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey

            while True:
                # If a limit is provided, adjust the query's Limit to only
                # fetch what is needed.
                if limit is not None:
                    remaining = limit - len(jobs)
                    query_params["Limit"] = remaining

                response = self._client.query(**query_params)
                jobs.extend([itemToJob(item) for item in response["Items"]])

                # If we have reached or exceeded the limit, trim the list and
                # break.
                if limit is not None and len(jobs) >= limit:
                    jobs = jobs[:limit]  # ensure we return exactly the limit
                    last_evaluated_key = response.get("LastEvaluatedKey", None)
                    break

                # Continue paginating if there's more data; otherwise, we're
                # done.
                if "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response[
                        "LastEvaluatedKey"
                    ]
                else:
                    last_evaluated_key = None
                    break

            return jobs, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise Exception(
                    f"Could not list jobs by user from the database: {e}"
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
                    f"Could not list jobs by user from the database: {e}"
                ) from e
