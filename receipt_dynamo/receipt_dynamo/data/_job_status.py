from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple

from receipt_dynamo.data.base_operations import (
    DynamoDBBaseOperations,
    QueryByParentMixin,
    SingleEntityCRUDMixin,
)
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    EntityValidationError,
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
    QueryByParentMixin,
    SingleEntityCRUDMixin,
):
    def add_job_status(self, job_status: JobStatus):
        """Adds a job status update to the database

        Args:
            job_status (JobStatus): The job status to add to the database

        Raises:
            ValueError: When a job status with the same timestamp already
                exists
        """
        self._add_entity(
            job_status,
            JobStatus,
            "job_status",
            condition_expression=(
                "attribute_not_exists(PK) OR attribute_not_exists(SK)"
            ),
        )

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
                raise EntityValidationError("LastEvaluatedKey must be a dictionary")
            validate_last_evaluated_key(last_evaluated_key)

        return self._query_by_parent(
            parent_pk=f"JOB#{job_id}",
            child_sk_prefix="STATUS#",
            converter_func=item_to_job_status,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    def _get_job_with_status(
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
            raise EntityValidationError(f"Error getting job with status: {e}") from e
