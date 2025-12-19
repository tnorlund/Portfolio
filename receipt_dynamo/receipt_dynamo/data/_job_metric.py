from typing import TYPE_CHECKING, Any, Dict, Optional

from receipt_dynamo.data.base_operations import (
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    EntityValidationError,
)
from receipt_dynamo.entities.job_metric import JobMetric, item_to_job_metric
from receipt_dynamo.entities.util import assert_valid_uuid

if TYPE_CHECKING:
    pass


def validate_last_evaluated_key(lek: Dict[str, Any]) -> None:
    required_keys = {"PK", "SK"}
    if not required_keys.issubset(lek.keys()):
        raise EntityValidationError(
            f"LastEvaluatedKey must contain keys: {required_keys}"
        )
    for key in required_keys:
        if not isinstance(lek[key], dict) or "S" not in lek[key]:
            raise EntityValidationError(
                f"LastEvaluatedKey[{key}] must be a dict containing " "a key 'S'"
            )


class _JobMetric(
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
):
    @handle_dynamodb_errors("add_job_metric")
    def add_job_metric(self, job_metric: JobMetric):
        """Adds a job metric to the database

        Args:
            job_metric (JobMetric): The job metric to add to the database

        Raises:
            ValueError: When a job metric with the same timestamp and name
                already exists
        """
        self._validate_entity(job_metric, JobMetric, "job_metric")
        self._add_entity(
            job_metric,
            condition_expression=(
                "attribute_not_exists(PK) OR attribute_not_exists(SK)"
            ),
        )

    @handle_dynamodb_errors("get_job_metric")
    def get_job_metric(
        self, job_id: str, metric_name: str, timestamp: str
    ) -> JobMetric:
        """Gets a specific job metric by job ID, metric name, and timestamp

        Args:
            job_id (str): The ID of the job
            metric_name (str): The name of the metric
            timestamp (str): The timestamp of the metric

        Returns:
            JobMetric: The requested job metric

        Raises:
            ValueError: If the job metric does not exist
        """
        if job_id is None:
            raise EntityValidationError("job_id cannot be None")
        assert_valid_uuid(job_id)
        if not metric_name or not isinstance(metric_name, str):
            raise EntityValidationError(
                "Metric name is required and must be a non-empty string."
            )
        if not timestamp or not isinstance(timestamp, str):
            raise EntityValidationError(
                "Timestamp is required and must be a non-empty string."
            )

        result = self._get_entity(
            primary_key=f"JOB#{job_id}",
            sort_key=f"METRIC#{metric_name}#{timestamp}",
            entity_class=JobMetric,
            converter_func=item_to_job_metric,
        )

        if result is None:
            raise EntityNotFoundError(
                f"No job metric found with job ID {job_id}, metric name "
                f"{metric_name}, and timestamp {timestamp}"
            )

        return result

    @handle_dynamodb_errors("list_job_metrics")
    def list_job_metrics(
        self,
        job_id: str,
        metric_name: Optional[str] = None,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[JobMetric], dict | None]:
        """
        Retrieve metrics for a job from the database.

        Parameters:
            job_id (str): The ID of the job to get metrics for.
            metric_name (str, optional): Filter by specific metric name.
            limit (int, optional): The maximum number of metrics to return.
            last_evaluated_key (dict, optional):
                A key that marks the starting point for the query.

        Returns:
            tuple:
                - A list of JobMetric objects for the specified job.
                - A dict representing the LastEvaluatedKey from the
                    final query page, or None if no further pages.

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

        # Build the expression attribute values based on whether
        # metric_name is provided
        expression_attr_values = {
            ":pk": {"S": f"JOB#{job_id}"},
        }

        if metric_name:
            expression_attr_values[":sk"] = {"S": f"METRIC#{metric_name}#"}
        else:
            expression_attr_values[":sk"] = {"S": "METRIC#"}

        return self._query_entities(
            index_name=None,
            key_condition_expression="PK = :pk AND begins_with(SK, :sk)",
            expression_attribute_names=None,
            expression_attribute_values=expression_attr_values,
            converter_func=item_to_job_metric,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
            scan_index_forward=True,  # Ascending order by default
        )

    @handle_dynamodb_errors("get_metrics_by_name")
    def get_metrics_by_name(
        self,
        metric_name: str,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[JobMetric], dict | None]:
        """
        Retrieve all metrics with a specific name across all jobs.

        Parameters:
            metric_name (str): The name of the metric to search for.
            limit (int, optional): The maximum number of metrics to return.
            last_evaluated_key (dict, optional):
                A key that marks the starting point for the query.

        Returns:
            tuple:
                - A list of JobMetric objects with the specified name.
                - A dict representing the LastEvaluatedKey from the
                    final query page, or None if no further pages.

        Raises:
            ValueError: If parameters are invalid.
            Exception: If the underlying database query fails.
        """
        if not metric_name or not isinstance(metric_name, str):
            raise EntityValidationError(
                "Metric name is required and must be a non-empty string."
            )

        if limit is not None and not isinstance(limit, int):
            raise EntityValidationError("Limit must be an integer")
        if limit is not None and limit <= 0:
            raise EntityValidationError("Limit must be greater than 0")
        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise EntityValidationError("LastEvaluatedKey must be a dictionary")
            validate_last_evaluated_key(last_evaluated_key)

        return self._query_entities(
            index_name="GSI1",
            key_condition_expression="GSI1PK = :pk",
            expression_attribute_names=None,
            expression_attribute_values={
                ":pk": {"S": f"METRIC#{metric_name}"},
            },
            converter_func=item_to_job_metric,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
            scan_index_forward=True,  # Ascending order by default
        )

    def get_metrics_by_name_across_jobs(
        self,
        metric_name: str,
        limit: Optional[int] = None,
        last_evaluated_key: dict | None = None,
    ) -> tuple[list[JobMetric], dict | None]:
        """
        Retrieve metrics with a specific name across all jobs, grouped by job.

        This method is optimized for comparing the same metric across
        different jobs. Results are automatically grouped by job_id and then
        ordered by timestamp.

        Parameters:
            metric_name (str): The name of the metric to search for.
            limit (int, optional): The maximum number of metrics to return.
            last_evaluated_key (dict, optional):
                A key that marks the starting point for the query.

        Returns:
            tuple:
                - A list of JobMetric objects with the specified name,
                    sorted by job_id and timestamp.
                - A dict representing the LastEvaluatedKey from the
                    final query page, or None if no further pages.

        Raises:
            ValueError: If parameters are invalid.
            Exception: If the underlying database query fails.
        """
        if not metric_name or not isinstance(metric_name, str):
            raise EntityValidationError(
                "Metric name is required and must be a non-empty string."
            )

        if limit is not None and not isinstance(limit, int):
            raise EntityValidationError("Limit must be an integer")
        if limit is not None and limit <= 0:
            raise EntityValidationError("Limit must be greater than 0")
        if last_evaluated_key is not None:
            if not isinstance(last_evaluated_key, dict):
                raise EntityValidationError("LastEvaluatedKey must be a dictionary")
            validate_last_evaluated_key(last_evaluated_key)

        return self._query_entities(
            index_name="GSI2",
            key_condition_expression="GSI2PK = :pk",
            expression_attribute_names=None,
            expression_attribute_values={
                ":pk": {"S": f"METRIC#{metric_name}"},
            },
            converter_func=item_to_job_metric,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
            scan_index_forward=True,  # Ascending order by default
        )
