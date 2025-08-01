from typing import TYPE_CHECKING, Dict, List, Optional, Tuple

from botocore.exceptions import ClientError

from receipt_dynamo.data.base_operations import (
    FlattenedStandardMixin,
    PutRequestTypeDef,
    WriteRequestTypeDef,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    EntityValidationError,
)
from receipt_dynamo.entities.job_log import JobLog, item_to_job_log

if TYPE_CHECKING:
    from receipt_dynamo.data.base_operations import (
        QueryInputTypeDef,
    )


class _JobLog(FlattenedStandardMixin):
    """
    Provides methods for accessing job log data in DynamoDB.

    This class offers methods to add, get, delete, and list job logs.
    Methods
    -------
    add_job_log(job_log: JobLog)
        Adds a job log entry to the database.
    add_job_logs(job_logs: List[JobLog])
        Adds multiple job log entries to the database.
    get_job_log(job_id: str, log_id: str) -> JobLog
        Gets a specific job log entry.
    list_job_logs(job_id: str) -> List[JobLog]
        Lists all log entries for a specific job.
    delete_job_log(job_log: JobLog)
        Deletes a job log entry from the database.
    """

    @handle_dynamodb_errors("add_job_log")
    def add_job_log(self, job_log: JobLog):
        """Adds a job log entry to the DynamoDB table.

        Args:
            job_log (JobLog): The job log to add.

        Raises:
            ValueError: If job_log is None or not a JobLog instance.
            ClientError: If a DynamoDB error occurs.
        """
        self._validate_entity(job_log, JobLog, "job_log")
        self._add_entity(
            job_log,
            condition_expression=(
                "attribute_not_exists(PK) AND attribute_not_exists(SK)"
            ),
        )

    @handle_dynamodb_errors("add_job_logs")
    def add_job_logs(self, job_logs: List[JobLog]):
        """Adds multiple job logs to the DynamoDB table in a batch.

        Args:
            job_logs (List[JobLog]): The job logs to add.

        Raises:
            ValueError: If job_logs is None, not a list, or contains
                non-JobLog items.
            ClientError: If a DynamoDB error occurs.
        """
        if job_logs is None:
            raise EntityValidationError("job_logs cannot be None")
        if not isinstance(job_logs, list):
            raise EntityValidationError(
                f"job_logs must be a list, got {type(job_logs)}"
            )
        if not all(isinstance(log, JobLog) for log in job_logs):
            raise EntityValidationError(
                "All items in job_logs must be JobLog instances"
            )

        if not job_logs:
            return  # Nothing to do

        # Convert to WriteRequestTypeDef format and use mixin method
        request_items = [
            WriteRequestTypeDef(
                PutRequest=PutRequestTypeDef(Item=log.to_item())
            )
            for log in job_logs
        ]

        self._batch_write_with_retry(request_items)

    @handle_dynamodb_errors("get_job_log")
    def get_job_log(self, job_id: str, timestamp: str) -> JobLog:
        """Gets a job log entry from the DynamoDB table.

        Args:
            job_id (str): The ID of the job.
            timestamp (str): The timestamp of the log entry.

        Returns:
            JobLog: The job log from the DynamoDB table.

        Raises:
            ValueError: If job_id or timestamp is None, or the job log is
                not found.
            ClientError: If a DynamoDB error occurs.
        """
        if job_id is None:
            raise EntityValidationError("job_id cannot be None")
        if timestamp is None:
            raise EntityValidationError("timestamp cannot be None")

        result = self._get_entity(
            primary_key=f"JOB#{job_id}",
            sort_key=f"LOG#{timestamp}",
            entity_class=JobLog,
            converter_func=item_to_job_log,
        )

        if result is None:
            raise EntityNotFoundError(
                f"Job log with job_id {job_id} and timestamp {timestamp} "
                f"not found"
            )

        return result

    @handle_dynamodb_errors("list_job_logs")
    def list_job_logs(
        self,
        job_id: str,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict] = None,
    ) -> Tuple[List[JobLog], Optional[Dict]]:
        """Lists all log entries for a specific job.

        Args:
            job_id (str): The ID of the job.
            limit (int, optional): The maximum number of items to return.
            last_evaluated_key (Dict, optional): The key to start pagination
                from.

        Returns:
            Tuple[List[JobLog], Optional[Dict]]: A tuple containing the list
                of job logs and the last evaluated key.

        Raises:
            ValueError: If job_id is None.
            ClientError: If a DynamoDB error occurs.
        """
        if job_id is None:
            raise EntityValidationError("job_id cannot be None")

        return self._query_entities(
            index_name=None,
            key_condition_expression=(
                "PK = :pk AND begins_with(SK, :sk_prefix)"
            ),
            expression_attribute_names=None,
            expression_attribute_values={
                ":pk": {"S": f"JOB#{job_id}"},
                ":sk_prefix": {"S": "LOG#"},
            },
            converter_func=item_to_job_log,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("delete_job_log")
    def delete_job_log(self, job_log: JobLog):
        """Deletes a job log entry from the DynamoDB table.

        Args:
            job_log (JobLog): The job log to delete.

        Raises:
            ValueError: If job_log is None or not a JobLog instance.
            ClientError: If a DynamoDB error occurs.
        """
        if job_log is None:
            raise EntityValidationError("job_log cannot be None")
        if not isinstance(job_log, JobLog):
            raise EntityValidationError(
                f"job_log must be a JobLog instance, got {type(job_log)}"
            )

        self._delete_entity(
            job_log,
            condition_expression=(
                "attribute_exists(PK) AND attribute_exists(SK)"
            ),
        )
