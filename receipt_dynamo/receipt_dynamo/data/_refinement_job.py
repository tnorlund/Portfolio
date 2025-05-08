from receipt_dynamo.entities.refinement_job import (
    RefinementJob,
    itemToRefinementJob,
)
from receipt_dynamo.entities.util import assert_valid_uuid
from receipt_dynamo.constants import RefinementStatus
from botocore.exceptions import ClientError


class _RefinementJob:
    def addRefinementJob(self, refinement_job: RefinementJob):
        """Adds a refinement job to the database

        Args:
            refinement_job (RefinementJob): The refinement job to add to the database

        Raises:
            ValueError: When a refinement job with the same ID already exists
        """
        if refinement_job is None:
            raise ValueError(
                "refinement_job parameter is required and cannot be None."
            )
        if not isinstance(refinement_job, RefinementJob):
            raise ValueError(
                "refinement_job must be an instance of the RefinementJob class."
            )
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=refinement_job.to_item(),
                ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"Refinement job for Image ID '{refinement_job.image_id}' already exists"
                ) from e
            elif error_code == "ResourceNotFoundException":
                raise Exception(
                    f"Could not add refinement job to DynamoDB: {e}"
                ) from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            else:
                raise Exception(
                    f"Could not add refinement job to DynamoDB: {e}"
                ) from e

    def addRefinementJobs(self, refinement_jobs: list[RefinementJob]):
        """Adds a list of refinement jobs to the database

        Args:
            refinement_jobs (list[RefinementJob]): The list of refinement jobs to add to the database

        Raises:
            ValueError: When a refinement job with the same ID already exists
        """
        if refinement_jobs is None:
            raise ValueError(
                "refinement_jobs parameter is required and cannot be None."
            )
        if not isinstance(refinement_jobs, list):
            raise ValueError(
                "refinement_jobs must be a list of RefinementJob instances."
            )
        if not all(isinstance(job, RefinementJob) for job in refinement_jobs):
            raise ValueError(
                "All refinement jobs must be instances of the RefinementJob class."
            )
        for i in range(0, len(refinement_jobs), 25):
            chunk = refinement_jobs[i : i + 25]
            request_items = [
                {"PutRequest": {"Item": job.to_item()}} for job in chunk
            ]
            try:
                response = self._client.batch_write_item(
                    RequestItems={self.table_name: request_items}
                )
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code", "")
                if error_code == "ProvisionedThroughputExceededException":
                    raise Exception(
                        f"Provisioned throughput exceeded: {e}"
                    ) from e
                elif error_code == "InternalServerError":
                    raise Exception(f"Internal server error: {e}") from e
            unprocessed = response.get("UnprocessedItems", {})
            while unprocessed.get(self.table_name):
                try:
                    response = self._client.batch_write_item(
                        RequestItems=unprocessed
                    )
                    unprocessed = response.get("UnprocessedItems", {})
                except ClientError as e:
                    error_code = e.response.get("Error", {}).get("Code", "")
                    if error_code == "ProvisionedThroughputExceededException":
                        raise Exception(
                            f"Provisioned throughput exceeded: {e}"
                        ) from e
                    elif error_code == "InternalServerError":
                        raise Exception(f"Internal server error: {e}") from e
                    else:
                        raise Exception(
                            f"Could not add refinement jobs to DynamoDB: {e}"
                        ) from e

    def getRefinementJob(self, image_id: str, job_id: str) -> RefinementJob:
        """Gets a refinement job from the database

        Args:
            image_id (str): The image ID of the refinement job
            job_id (str): The job ID of the refinement job

        Returns:
            RefinementJob: The refinement job

        Raises:
            ValueError: When the refinement job is not found
        """
        if image_id is None:
            raise ValueError("Image ID is required and cannot be None.")
        if job_id is None:
            raise ValueError("Job ID is required and cannot be None.")
        assert_valid_uuid(image_id)
        assert_valid_uuid(job_id)
        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"IMAGE#{image_id}"},
                    "SK": {"S": f"REFINEMENT_JOB#{job_id}"},
                },
            )
            if "Item" in response:
                return itemToRefinementJob(response["Item"])
            else:
                raise ValueError(
                    f"Refinement job for Image ID '{image_id}' and Job ID '{job_id}' does not exist."
                )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            elif error_code == "AccessDeniedException":
                raise Exception(f"Access denied: {e}") from e
            else:
                raise Exception(f"Error getting refinement job: {e}") from e

    def listRefinementJobs(
        self, limit: int = None, lastEvaluatedKey: dict | None = None
    ) -> tuple[list[RefinementJob], dict | None]:
        """Lists all refinement jobs from the database

        Args:
            limit (int, optional): The maximum number of refinement jobs to return. Defaults to None.
            lastEvaluatedKey (dict | None, optional): The last evaluated key from the previous query. Defaults to None.

        Returns:
            tuple[list[RefinementJob], dict | None]: A tuple containing a list of refinement jobs and the last evaluated key
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
                "ExpressionAttributeValues": {":val": {"S": "REFINEMENT_JOB"}},
            }
            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey

            while True:
                if limit is not None:
                    remaining = limit - len(jobs)
                    query_params["Limit"] = remaining

                response = self._client.query(**query_params)
                jobs.extend(
                    [itemToRefinementJob(item) for item in response["Items"]]
                )

                if limit is not None and len(jobs) >= limit:
                    jobs = jobs[:limit]
                    last_evaluated_key = response.get("LastEvaluatedKey", None)
                    break

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
            if error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            elif error_code == "AccessDeniedException":
                raise Exception(f"Access denied: {e}") from e
            else:
                raise Exception(f"Error listing refinement jobs: {e}") from e

    def getRefinementJobsByStatus(
        self,
        status: RefinementStatus,
        limit: int = None,
        lastEvaluatedKey: dict | None = None,
    ) -> tuple[list[RefinementJob], dict | None]:
        """Gets refinement jobs by status from the database

        Args:
            status (RefinementStatus): The status of the refinement jobs to get
            limit (int, optional): The maximum number of refinement jobs to return. Defaults to None.
            lastEvaluatedKey (dict | None, optional): The last evaluated key from the previous query. Defaults to None.

        Returns:
            tuple[list[RefinementJob], dict | None]: A tuple containing a list of refinement jobs and the last evaluated key
        """
        if status is None:
            raise ValueError("Status is required and cannot be None.")
        if not isinstance(status, RefinementStatus):
            raise ValueError("Status must be a RefinementStatus instance.")
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
                "IndexName": "GSI1",
                "KeyConditionExpression": "#t = :val",
                "ExpressionAttributeNames": {"#t": "GSI1PK"},
                "ExpressionAttributeValues": {
                    ":val": {"S": f"REFINEMENT_JOB_STATUS#{status.value}"}
                },
            }
            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey

            while True:
                if limit is not None:
                    remaining = limit - len(jobs)
                    query_params["Limit"] = remaining

                response = self._client.query(**query_params)
                jobs.extend(
                    [itemToRefinementJob(item) for item in response["Items"]]
                )

                if limit is not None and len(jobs) >= limit:
                    jobs = jobs[:limit]
                    last_evaluated_key = response.get("LastEvaluatedKey", None)
                    break

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
            if error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            elif error_code == "AccessDeniedException":
                raise Exception(f"Access denied: {e}") from e
            else:
                raise Exception(
                    f"Error getting refinement jobs by status: {e}"
                ) from e
