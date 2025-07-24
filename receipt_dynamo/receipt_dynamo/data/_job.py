from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from receipt_dynamo.data.base_operations import (
    BatchOperationsMixin,
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    TransactionalOperationsMixin,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
)
from receipt_dynamo.entities.job import Job, item_to_job
from receipt_dynamo.entities.job_status import JobStatus, item_to_job_status
from receipt_dynamo.entities.util import assert_valid_uuid

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


class _Job(
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    BatchOperationsMixin,
    TransactionalOperationsMixin,
):
    """
    A class used to represent a Job in the database.

    Methods
    -------
    add_job(job: Job)
        Adds a job to the database.
    add_jobs(jobs: list[Job])
        Adds multiple jobs to the database.
    update_job(job: Job)
        Updates a job in the database.
    update_jobs(jobs: list[Job])
        Updates multiple jobs in the database.
    delete_job(job: Job)
        Deletes a job from the database.
    delete_jobs(jobs: list[Job])
        Deletes multiple jobs from the database.
    get_job(job_id: str) -> Job
        Gets a job from the database.
    get_job_with_status(job_id: str) -> Tuple[Job, List[JobStatus]]
        Gets a job with all its status updates.
    list_jobs(
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[Job], dict | None]
        Lists all jobs from the database.
    list_jobs_by_status(
        status: str,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[Job], dict | None]
        Lists jobs filtered by status.
    list_jobs_by_user(
        user_id: str,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[Job], dict | None]
        Lists jobs created by a specific user.
    """

    @handle_dynamodb_errors("add_job")
    def add_job(self, job: Job):
        """Adds a job to the database

        Args:
            job (Job): The job to add to the database

        Raises:
            ValueError: When a job with the same ID already exists
        """
        self._validate_entity(job, Job, "job")
        self._add_entity(job)

    @handle_dynamodb_errors("add_jobs")
    def add_jobs(self, jobs: list[Job]):
        """Adds a list of jobs to the database

        Args:
            jobs (list[Job]): The jobs to add to the database

        Raises:
            ValueError: When validation fails or jobs cannot be added
        """
        self._validate_entity_list(jobs, Job, "jobs")

        request_items = [
            WriteRequestTypeDef(
                PutRequest=PutRequestTypeDef(Item=job.to_item())
            )
            for job in jobs
        ]
        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("update_job")
    def update_job(self, job: Job):
        """Updates a job in the database

        Args:
            job (Job): The job to update in the database

        Raises:
            ValueError: When the job does not exist
        """
        self._validate_entity(job, Job, "job")
        self._update_entity(job)

    @handle_dynamodb_errors("update_jobs")
    def update_jobs(self, jobs: list[Job]):
        """
        Updates a list of jobs in the database using transactions.
        Each job update is conditional upon the job already existing.

        Since DynamoDB's transact_write_items supports a maximum of 25
        operations per call, the list of jobs is split into chunks of 25 items
        or less. Each chunk is updated in a separate transaction.

        Args:
            jobs (list[Job]): The jobs to update in the database.

        Raises:
            ValueError: When given a bad parameter.
            Exception: For underlying DynamoDB errors.
        """
        self._validate_entity_list(jobs, Job, "jobs")

        transact_items = [
            TransactWriteItemTypeDef(
                Put=PutTypeDef(
                    TableName=self.table_name,
                    Item=job.to_item(),
                    ConditionExpression="attribute_exists(PK)",
                )
            )
            for job in jobs
        ]
        self._transact_write_with_chunking(transact_items)

    @handle_dynamodb_errors("delete_job")
    def delete_job(self, job: Job):
        """Deletes a job from the database

        Args:
            job (Job): The job to delete from the database

        Raises:
            ValueError: When the job does not exist
        """
        self._validate_entity(job, Job, "job")
        self._delete_entity(job)

    @handle_dynamodb_errors("delete_jobs")
    def delete_jobs(self, jobs: list[Job]):
        """
        Deletes a list of jobs from the database using transactions.
        Each delete operation is conditional upon the job existing.

        Args:
            jobs (list[Job]): The jobs to delete from the database.

        Raises:
            ValueError: When a job does not exist or if another error occurs.
        """
        self._validate_entity_list(jobs, Job, "jobs")

        transact_items = [
            TransactWriteItemTypeDef(
                Delete=DeleteTypeDef(
                    TableName=self.table_name,
                    Key=job.key,
                    ConditionExpression="attribute_exists(PK)",
                )
            )
            for job in jobs
        ]
        self._transact_write_with_chunking(transact_items)

    @handle_dynamodb_errors("get_job")
    def get_job(self, job_id: str) -> Job:
        """
        Retrieves a job from the database.

        Args:
            job_id (str): The ID of the job to retrieve.

        Returns:
            Job: The job object.

        Raises:
            ValueError: If input parameters are invalid or if the job does not
                exist.
            Exception: For underlying DynamoDB errors.
        """
        if job_id is None:
            raise ValueError("job_id cannot be None")

        # Validate job_id as a UUID
        assert_valid_uuid(job_id)

        response = self._client.get_item(
            TableName=self.table_name,
            Key={
                "PK": {"S": f"JOB#{job_id}"},
                "SK": {"S": "JOB"},
            },
        )
        if "Item" in response:
            return item_to_job(response["Item"])
        else:
            raise EntityNotFoundError(f"Job with ID {job_id} does not exist.")

    @handle_dynamodb_errors("get_job_with_status")
    def get_job_with_status(self, job_id: str) -> Tuple[Job, List[JobStatus]]:
        """Get a job with all its status updates

        Args:
            job_id (str): The ID of the job to get

        Returns:
            Tuple[Job, List[JobStatus]]: A tuple containing the job and a list
                of its status updates
        """
        if job_id is None:
            raise ValueError("job_id cannot be None")

        # Validate job_id as a UUID
        assert_valid_uuid(job_id)

        # Get the job first
        job = self.get_job(job_id)

        # Get the job status updates
        status_response = self._client.query(
            TableName=self.table_name,
            KeyConditionExpression="PK = :pk AND begins_with(SK, :sk)",
            ExpressionAttributeValues={
                ":pk": {"S": f"JOB#{job_id}"},
                ":sk": {"S": "STATUS#"},
            },
        )

        statuses = []
        if "Items" in status_response:
            # Convert DynamoDB items to JobStatus objects
            for item in status_response["Items"]:
                statuses.append(item_to_job_status(item))

        return job, statuses

    @handle_dynamodb_errors("list_jobs")
    def list_jobs(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[Job], dict | None]:
        """
        Retrieve job records from the database with support for pagination.

        Parameters:
            limit (int, optional): The maximum number of job items to return.
                If None, all jobs are fetched.
            last_evaluated_key (dict, optional): A key that marks the starting
                point for the query.

        Returns:
            tuple:
                - A list of Job objects, containing up to 'limit' items if
                    specified.
                - A dict representing the LastEvaluatedKey from the final query
                    page, or None if no further pages.

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

        jobs: List[Job] = []
        query_params: QueryInputTypeDef = {
            "TableName": self.table_name,
            "IndexName": "GSITYPE",
            "KeyConditionExpression": "#t = :val",
            "ExpressionAttributeNames": {"#t": "TYPE"},
            "ExpressionAttributeValues": {":val": {"S": "JOB"}},
        }
        if last_evaluated_key is not None:
            query_params["ExclusiveStartKey"] = last_evaluated_key

        while True:
            # If a limit is provided, adjust the query's Limit to only
            # fetch what is needed.
            if limit is not None:
                remaining = limit - len(jobs)
                query_params["Limit"] = remaining

            response = self._client.query(**query_params)
            jobs.extend([item_to_job(item) for item in response["Items"]])

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

    @handle_dynamodb_errors("list_jobs_by_status")
    def list_jobs_by_status(
        self,
        status: str,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[Job], dict | None]:
        """
        Retrieve job records filtered by status from the database.

        Parameters:
            status (str): The status to filter by.
            limit (int, optional): The maximum number of job items to return.
            last_evaluated_key (dict, optional): A key that marks the starting
                point for the query.

        Returns:
            tuple:
                - A list of Job objects with the specified status.
                - A dict representing the LastEvaluatedKey from the final query
                    page, or None if no further pages.

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
        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise ValueError("LastEvaluatedKey must be a dictionary")
            # Validate the LastEvaluatedKey structure specific to GSI1
            if not all(
                k in last_evaluated_key
                for k in ["PK", "SK", "GSI1PK", "GSI1SK"]
            ):
                raise ValueError(
                    "LastEvaluatedKey must contain PK, SK, GSI1PK, and GSI1SK"
                    " keys"
                )

        jobs: List[Job] = []
        query_params: QueryInputTypeDef = {
            "TableName": self.table_name,
            "IndexName": "GSI1",
            "KeyConditionExpression": "GSI1PK = :status",
            "ExpressionAttributeValues": {
                ":status": {"S": f"STATUS#{status.lower()}"},
                ":job_type": {"S": "JOB"},
            },
            "FilterExpression": "#type = :job_type",
            "ExpressionAttributeNames": {"#type": "TYPE"},
        }
        if last_evaluated_key is not None:
            query_params["ExclusiveStartKey"] = last_evaluated_key

        while True:
            # If a limit is provided, adjust the query's Limit to only
            # fetch what is needed.
            if limit is not None:
                remaining = limit - len(jobs)
                query_params["Limit"] = remaining

            response = self._client.query(**query_params)
            jobs.extend([item_to_job(item) for item in response["Items"]])

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

    @handle_dynamodb_errors("list_jobs_by_user")
    def list_jobs_by_user(
        self,
        user_id: str,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[Job], dict | None]:
        """
        Retrieve job records created by a specific user from the database.

        Parameters:
            user_id (str): The ID of the user who created the jobs.
            limit (int, optional): The maximum number of job items to return.
            last_evaluated_key (dict, optional): A key that marks the starting
                point for the query.

        Returns:
            tuple:
                - A list of Job objects created by the specified user.
                - A dict representing the LastEvaluatedKey from the final query
                    page, or None if no further pages.

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
        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise ValueError("LastEvaluatedKey must be a dictionary")
            # Validate the LastEvaluatedKey structure specific to GSI2
            if not all(
                k in last_evaluated_key
                for k in ["PK", "SK", "GSI2PK", "GSI2SK"]
            ):
                raise ValueError(
                    "LastEvaluatedKey must contain PK, SK, GSI2PK, and GSI2SK"
                    " keys"
                )

        jobs: List[Job] = []
        query_params: QueryInputTypeDef = {
            "TableName": self.table_name,
            "IndexName": "GSI2",
            "KeyConditionExpression": "GSI2PK = :user",
            "ExpressionAttributeValues": {
                ":user": {"S": f"USER#{user_id}"},
                ":job_type": {"S": "JOB"},
            },
            "FilterExpression": "#type = :job_type",
            "ExpressionAttributeNames": {"#type": "TYPE"},
        }
        if last_evaluated_key is not None:
            query_params["ExclusiveStartKey"] = last_evaluated_key

        while True:
            # If a limit is provided, adjust the query's Limit to only
            # fetch what is needed.
            if limit is not None:
                remaining = limit - len(jobs)
                query_params["Limit"] = remaining

            response = self._client.query(**query_params)
            jobs.extend([item_to_job(item) for item in response["Items"]])

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
