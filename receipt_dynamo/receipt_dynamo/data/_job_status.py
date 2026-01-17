from typing import TYPE_CHECKING, Any

from botocore.exceptions import ClientError

from receipt_dynamo.data.base_operations import (
    FlattenedStandardMixin,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    EntityValidationError,
)
from receipt_dynamo.entities.job_status import JobStatus, item_to_job_status

if TYPE_CHECKING:
    pass


class _JobStatus(FlattenedStandardMixin):
    """Mixin for job status operations in DynamoDB."""

    @handle_dynamodb_errors("add_job_status")
    def add_job_status(self, job_status: JobStatus) -> None:
        """Adds a job status update to the database.

        Args:
            job_status: The job status to add to the database.

        Raises:
            EntityValidationError: When validation fails.
            EntityAlreadyExistsError: When a job status with the same
                timestamp already exists.
        """
        self._validate_entity(job_status, JobStatus, "job_status")
        self._add_entity(
            job_status,
            condition_expression=(
                "attribute_not_exists(PK) OR attribute_not_exists(SK)"
            ),
        )

    @handle_dynamodb_errors("get_latest_job_status")
    def get_latest_job_status(self, job_id: str) -> JobStatus:
        """Gets the latest status for a job.

        Args:
            job_id: The ID of the job to get the latest status for.

        Returns:
            The latest job status.

        Raises:
            EntityValidationError: If job_id is invalid.
            EntityNotFoundError: If the job has no status updates.
        """
        self._validate_job_id(job_id)

        results, _ = self._query_entities(
            index_name=None,
            key_condition_expression="PK = :pk AND begins_with(SK, :sk)",
            expression_attribute_names=None,
            expression_attribute_values={
                ":pk": {"S": f"JOB#{job_id}"},
                ":sk": {"S": "STATUS#"},
            },
            converter_func=item_to_job_status,
            limit=1,
            scan_index_forward=False,  # Descending order for latest
        )

        if not results:
            raise EntityNotFoundError(
                f"No status updates found for job with ID {job_id}"
            )

        return results[0]

    @handle_dynamodb_errors("list_job_statuses")
    def list_job_statuses(
        self,
        job_id: str,
        limit: int | None = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[JobStatus], dict | None]:
        """Retrieve status updates for a job from the database.

        Args:
            job_id: The ID of the job to get status updates for.
            limit: The maximum number of status updates to return.
            last_evaluated_key: A key that marks the starting point for
                the query.

        Returns:
            A tuple containing:
                - A list of JobStatus objects for the specified job.
                - A dict representing the LastEvaluatedKey, or None if
                  no further pages.

        Raises:
            EntityValidationError: If parameters are invalid.
        """
        self._validate_job_id(job_id)
        self._validate_pagination_params(
            limit, last_evaluated_key, validate_attribute_format=True
        )

        return self._query_by_parent(
            parent_key_prefix=f"JOB#{job_id}",
            child_key_prefix="STATUS#",
            converter_func=item_to_job_status,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    def _get_job_with_status(
        self, job_id: str
    ) -> tuple[Any | None, list[JobStatus]]:
        """Get a job with all its status updates.

        Args:
            job_id: The ID of the job to get.

        Returns:
            A tuple containing the job (as raw item) and a list of its
            status updates. The job will be None if no job was found.
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
            statuses: list[JobStatus] = []

            for item in response["Items"]:
                if item["TYPE"]["S"] == "JOB":
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
