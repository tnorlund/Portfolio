from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from receipt_dynamo.data.base_operations import (
    DeleteTypeDef,
    DynamoDBBaseOperations,
    FlattenedStandardMixin,
    PutRequestTypeDef,
    TransactWriteItemTypeDef,
    WriteRequestTypeDef,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    EntityValidationError,
)
from receipt_dynamo.entities.job import Job, item_to_job
from receipt_dynamo.entities.job_status import JobStatus, item_to_job_status
from receipt_dynamo.entities.util import assert_valid_uuid

if TYPE_CHECKING:
    from receipt_dynamo.data.base_operations import (
        QueryInputTypeDef,
    )


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


class _Job(
    DynamoDBBaseOperations,
    FlattenedStandardMixin,
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
        self._update_entities(jobs, Job, "jobs")

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
            raise EntityValidationError("job_id cannot be None")

        # Validate job_id as a UUID
        assert_valid_uuid(job_id)

        result = self._get_entity(
            primary_key=f"JOB#{job_id}",
            sort_key="JOB",
            entity_class=Job,
            converter_func=item_to_job,
        )

        if result is None:
            raise EntityNotFoundError(
                f"Job with job id {job_id} does not exist"
            )

        return result

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
            raise EntityValidationError("job_id cannot be None")

        # Validate job_id as a UUID
        assert_valid_uuid(job_id)

        # Get the job first
        job = self.get_job(job_id)

        # Get the job status updates using base operations
        statuses, _ = self._query_entities(
            index_name=None,  # Main table query
            key_condition_expression="PK = :pk AND begins_with(SK, :sk)",
            expression_attribute_names=None,
            expression_attribute_values={
                ":pk": {"S": f"JOB#{job_id}"},
                ":sk": {"S": "STATUS#"},
            },
            converter_func=item_to_job_status,
            limit=None,
            last_evaluated_key=None,
        )

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
        return self._query_by_type(
            entity_type="JOB",
            converter_func=item_to_job,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

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
            raise EntityValidationError(
                f"status must be one of {valid_statuses}"
            )

        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise EntityValidationError(
                    "LastEvaluatedKey must be a dictionary"
                )
            # Validate the LastEvaluatedKey structure specific to GSI1
            if not all(
                k in last_evaluated_key
                for k in ["PK", "SK", "GSI1PK", "GSI1SK"]
            ):
                raise EntityValidationError(
                    "LastEvaluatedKey must contain PK, SK, GSI1PK, and GSI1SK"
                    " keys"
                )

        return self._query_entities(
            index_name="GSI1",
            key_condition_expression="GSI1PK = :status",
            expression_attribute_names={"#type": "TYPE"},
            expression_attribute_values={
                ":status": {"S": f"STATUS#{status.lower()}"},
                ":job_type": {"S": "JOB"},
            },
            converter_func=item_to_job,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
            filter_expression="#type = :job_type",
        )

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
            raise EntityValidationError("user_id must be a non-empty string")

        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise EntityValidationError(
                    "LastEvaluatedKey must be a dictionary"
                )
            # Validate the LastEvaluatedKey structure specific to GSI2
            if not all(
                k in last_evaluated_key
                for k in ["PK", "SK", "GSI2PK", "GSI2SK"]
            ):
                raise EntityValidationError(
                    "LastEvaluatedKey must contain PK, SK, GSI2PK, and GSI2SK"
                    " keys"
                )

        return self._query_entities(
            index_name="GSI2",
            key_condition_expression="GSI2PK = :user",
            expression_attribute_names={"#type": "TYPE"},
            expression_attribute_values={
                ":user": {"S": f"USER#{user_id}"},
                ":job_type": {"S": "JOB"},
            },
            converter_func=item_to_job,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
            filter_expression="#type = :job_type",
        )
