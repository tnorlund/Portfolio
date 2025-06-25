from typing import Optional

from botocore.exceptions import ClientError

from receipt_dynamo.data._base import DynamoClientProtocol
from receipt_dynamo.entities.job_metric import JobMetric, itemToJobMetric
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


class _JobMetric(DynamoClientProtocol):
    def addJobMetric(self, job_metric: JobMetric):
        """Adds a job metric to the database

        Args:
            job_metric (JobMetric): The job metric to add to the database

        Raises:
            ValueError: When a job metric with the same timestamp and name already exists
        """
        if job_metric is None:
            raise ValueError("JobMetric parameter is required and cannot be None.")
        if not isinstance(job_metric, JobMetric):
            raise ValueError("job_metric must be an instance of the JobMetric class.")
        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=job_metric.to_item(),
                ConditionExpression="attribute_not_exists(PK) OR attribute_not_exists(SK)",
            )
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ConditionalCheckFailedException":
                raise ValueError(
                    f"JobMetric with name {job_metric.metric_name} and timestamp {job_metric.timestamp} for job {job_metric.job_id} already exists"
                ) from e
            elif error_code == "ResourceNotFoundException":
                raise Exception(f"Could not add job metric to DynamoDB: {e}") from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            else:
                raise Exception(f"Could not add job metric to DynamoDB: {e}") from e

    def getJobMetric(self, job_id: str, metric_name: str, timestamp: str) -> JobMetric:
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
            raise ValueError("Job ID is required and cannot be None.")
        assert_valid_uuid(job_id)
        if not metric_name or not isinstance(metric_name, str):
            raise ValueError("Metric name is required and must be a non-empty string.")
        if not timestamp or not isinstance(timestamp, str):
            raise ValueError("Timestamp is required and must be a non-empty string.")

        try:
            response = self._client.get_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"JOB#{job_id}"},
                    "SK": {"S": f"METRIC#{metric_name}#{timestamp}"},
                },
            )

            if "Item" not in response:
                raise ValueError(
                    f"No job metric found with job ID {job_id}, metric name {metric_name}, and timestamp {timestamp}"
                )

            return itemToJobMetric(response["Item"])
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise Exception(f"Could not get job metric: {e}") from e
            elif error_code == "ProvisionedThroughputExceededException":
                raise Exception(f"Provisioned throughput exceeded: {e}") from e
            elif error_code == "InternalServerError":
                raise Exception(f"Internal server error: {e}") from e
            else:
                raise Exception(f"Error getting job metric: {e}") from e

    def listJobMetrics(
        self,
        job_id: str,
        metric_name: Optional[str] = None,
        limit: int = None,
        lastEvaluatedKey: dict | None = None,
    ) -> tuple[list[JobMetric], dict | None]:
        """
        Retrieve metrics for a job from the database.

        Parameters:
            job_id (str): The ID of the job to get metrics for.
            metric_name (str, optional): Filter by specific metric name.
            limit (int, optional): The maximum number of metrics to return.
            lastEvaluatedKey (dict, optional): A key that marks the starting point for the query.

        Returns:
            tuple:
                - A list of JobMetric objects for the specified job.
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

        metrics = []
        try:
            query_params = {
                "TableName": self.table_name,
                "KeyConditionExpression": "PK = :pk",
                "ExpressionAttributeValues": {
                    ":pk": {"S": f"JOB#{job_id}"},
                },
                "ScanIndexForward": True,  # Ascending order by default
            }

            # Add filter for metric name if provided
            if metric_name:
                query_params["KeyConditionExpression"] += " AND begins_with(SK, :sk)"
                query_params["ExpressionAttributeValues"][":sk"] = {
                    "S": f"METRIC#{metric_name}#"
                }
            else:
                query_params["KeyConditionExpression"] += " AND begins_with(SK, :sk)"
                query_params["ExpressionAttributeValues"][":sk"] = {"S": "METRIC#"}

            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey

            while True:
                if limit is not None:
                    remaining = limit - len(metrics)
                    query_params["Limit"] = remaining

                response = self._client.query(**query_params)
                for item in response["Items"]:
                    if item.get("TYPE", {}).get("S") == "JOB_METRIC":
                        metrics.append(itemToJobMetric(item))

                if limit is not None and len(metrics) >= limit:
                    metrics = metrics[:limit]
                    last_evaluated_key = response.get("LastEvaluatedKey", None)
                    break

                if "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response["LastEvaluatedKey"]
                else:
                    last_evaluated_key = None
                    break

            return metrics, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise Exception(
                    f"Could not list job metrics from the database: {e}"
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
                    f"Could not list job metrics from the database: {e}"
                ) from e

    def getMetricsByName(
        self,
        metric_name: str,
        limit: int = None,
        lastEvaluatedKey: dict | None = None,
    ) -> tuple[list[JobMetric], dict | None]:
        """
        Retrieve all metrics with a specific name across all jobs.

        Parameters:
            metric_name (str): The name of the metric to search for.
            limit (int, optional): The maximum number of metrics to return.
            lastEvaluatedKey (dict, optional): A key that marks the starting point for the query.

        Returns:
            tuple:
                - A list of JobMetric objects with the specified name.
                - A dict representing the LastEvaluatedKey from the final query page, or None if no further pages.

        Raises:
            ValueError: If parameters are invalid.
            Exception: If the underlying database query fails.
        """
        if not metric_name or not isinstance(metric_name, str):
            raise ValueError("Metric name is required and must be a non-empty string.")

        if limit is not None and not isinstance(limit, int):
            raise ValueError("Limit must be an integer")
        if limit is not None and limit <= 0:
            raise ValueError("Limit must be greater than 0")
        if lastEvaluatedKey is not None:
            if not isinstance(lastEvaluatedKey, dict):
                raise ValueError("LastEvaluatedKey must be a dictionary")
            validate_last_evaluated_key(lastEvaluatedKey)

        metrics = []
        try:
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSI1",
                "KeyConditionExpression": "GSI1PK = :pk",
                "ExpressionAttributeValues": {
                    ":pk": {"S": f"METRIC#{metric_name}"},
                },
                "ScanIndexForward": True,  # Ascending order by default
            }

            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey

            while True:
                if limit is not None:
                    remaining = limit - len(metrics)
                    query_params["Limit"] = remaining

                response = self._client.query(**query_params)
                for item in response["Items"]:
                    if item.get("TYPE", {}).get("S") == "JOB_METRIC":
                        metrics.append(itemToJobMetric(item))

                if limit is not None and len(metrics) >= limit:
                    metrics = metrics[:limit]
                    last_evaluated_key = response.get("LastEvaluatedKey", None)
                    break

                if "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response["LastEvaluatedKey"]
                else:
                    last_evaluated_key = None
                    break

            return metrics, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise Exception(
                    f"Could not query metrics by name from the database: {e}"
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
                    f"Could not query metrics by name from the database: {e}"
                ) from e

    def getMetricsByNameAcrossJobs(
        self,
        metric_name: str,
        limit: int = None,
        lastEvaluatedKey: dict | None = None,
    ) -> tuple[list[JobMetric], dict | None]:
        """
        Retrieve metrics with a specific name across all jobs, grouped by job.

        This method is optimized for comparing the same metric across different jobs.
        Results are automatically grouped by job_id and then ordered by timestamp.

        Parameters:
            metric_name (str): The name of the metric to search for.
            limit (int, optional): The maximum number of metrics to return.
            lastEvaluatedKey (dict, optional): A key that marks the starting point for the query.

        Returns:
            tuple:
                - A list of JobMetric objects with the specified name, sorted by job_id and timestamp.
                - A dict representing the LastEvaluatedKey from the final query page, or None if no further pages.

        Raises:
            ValueError: If parameters are invalid.
            Exception: If the underlying database query fails.
        """
        if not metric_name or not isinstance(metric_name, str):
            raise ValueError("Metric name is required and must be a non-empty string.")

        if limit is not None and not isinstance(limit, int):
            raise ValueError("Limit must be an integer")
        if limit is not None and limit <= 0:
            raise ValueError("Limit must be greater than 0")
        if lastEvaluatedKey is not None:
            if not isinstance(lastEvaluatedKey, dict):
                raise ValueError("LastEvaluatedKey must be a dictionary")
            validate_last_evaluated_key(lastEvaluatedKey)

        metrics = []
        try:
            query_params = {
                "TableName": self.table_name,
                "IndexName": "GSI2",
                "KeyConditionExpression": "GSI2PK = :pk",
                "ExpressionAttributeValues": {
                    ":pk": {"S": f"METRIC#{metric_name}"},
                },
                "ScanIndexForward": True,  # Ascending order by default
            }

            if lastEvaluatedKey is not None:
                query_params["ExclusiveStartKey"] = lastEvaluatedKey

            while True:
                if limit is not None:
                    remaining = limit - len(metrics)
                    query_params["Limit"] = remaining

                response = self._client.query(**query_params)
                for item in response["Items"]:
                    if item.get("TYPE", {}).get("S") == "JOB_METRIC":
                        metrics.append(itemToJobMetric(item))

                if limit is not None and len(metrics) >= limit:
                    metrics = metrics[:limit]
                    last_evaluated_key = response.get("LastEvaluatedKey", None)
                    break

                if "LastEvaluatedKey" in response:
                    query_params["ExclusiveStartKey"] = response["LastEvaluatedKey"]
                else:
                    last_evaluated_key = None
                    break

            return metrics, last_evaluated_key
        except ClientError as e:
            error_code = e.response.get("Error", {}).get("Code", "")
            if error_code == "ResourceNotFoundException":
                raise Exception(
                    f"Could not query metrics by name across jobs from the database: {e}"
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
                    f"Could not query metrics by name across jobs from the database: {e}"
                ) from e
