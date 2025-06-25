from typing import Dict, List, Optional, Tuple

from botocore.exceptions import ClientError
from receipt_dynamo.data._base import DynamoClientProtocol
from receipt_dynamo.entities.job_log import JobLog, itemToJobLog


class _JobLog(DynamoClientProtocol):
    """
    Provides methods for accessing job log data in DynamoDB.

    This class offers methods to add, get, delete, and list job logs.
    """

    def addJobLog(self, job_log: JobLog):
        """Adds a job log entry to the DynamoDB table.

        Args:
            job_log (JobLog): The job log to add.

        Raises:
            ValueError: If job_log is None or not a JobLog instance.
            ClientError: If a DynamoDB error occurs.
        """
        if job_log is None:
            raise ValueError("job_log cannot be None")
        if not isinstance(job_log, JobLog):
            raise ValueError(
                f"job_log must be a JobLog instance, got {type(job_log)}"
            )

        try:
            self._client.put_item(
                TableName=self.table_name,
                Item=job_log.to_item(),
                ConditionExpression="attribute_not_exists(PK) AND attribute_not_exists(SK)",
            )
        except ClientError as e:
            if (
                e.response["Error"]["Code"]
                == "ConditionalCheckFailedException"
            ):
                raise ValueError(
                    f"Job log for job {job_log.job_id} with timestamp {job_log.timestamp} already exists"
                )
            raise

    def addJobLogs(self, job_logs: List[JobLog]):
        """Adds multiple job logs to the DynamoDB table in a batch.

        Args:
            job_logs (List[JobLog]): The job logs to add.

        Raises:
            ValueError: If job_logs is None, not a list, or contains non-JobLog items.
            ClientError: If a DynamoDB error occurs.
        """
        if job_logs is None:
            raise ValueError("job_logs cannot be None")
        if not isinstance(job_logs, list):
            raise ValueError(f"job_logs must be a list, got {type(job_logs)}")
        if not all(isinstance(log, JobLog) for log in job_logs):
            raise ValueError("All items in job_logs must be JobLog instances")

        if not job_logs:
            return  # Nothing to do

        # DynamoDB batch write has a limit of 25 items
        batch_size = 25
        for i in range(0, len(job_logs), batch_size):
            batch = job_logs[i : i + batch_size]

            request_items = {
                self.table_name: [
                    {"PutRequest": {"Item": log.to_item()}} for log in batch
                ]
            }

            response = self._client.batch_write_item(
                RequestItems=request_items
            )

            # Handle unprocessed items with exponential backoff
            unprocessed_items = response.get("UnprocessedItems", {})
            retry_count = 0
            max_retries = 3

            while unprocessed_items and retry_count < max_retries:
                retry_count += 1
                response = self._client.batch_write_item(
                    RequestItems=unprocessed_items
                )
                unprocessed_items = response.get("UnprocessedItems", {})

            if unprocessed_items:
                raise ClientError(
                    {
                        "Error": {
                            "Code": "ProvisionedThroughputExceededException",
                            "Message": f"Could not process all items after {max_retries} retries",
                        }
                    },
                    "BatchWriteItem",
                )

    def getJobLog(self, job_id: str, timestamp: str) -> JobLog:
        """Gets a job log entry from the DynamoDB table.

        Args:
            job_id (str): The ID of the job.
            timestamp (str): The timestamp of the log entry.

        Returns:
            JobLog: The job log from the DynamoDB table.

        Raises:
            ValueError: If job_id or timestamp is None, or the job log is not found.
            ClientError: If a DynamoDB error occurs.
        """
        if job_id is None:
            raise ValueError("job_id cannot be None")
        if timestamp is None:
            raise ValueError("timestamp cannot be None")

        response = self._client.get_item(
            TableName=self.table_name,
            Key={
                "PK": {"S": f"JOB#{job_id}"},
                "SK": {"S": f"LOG#{timestamp}"},
            },
        )

        item = response.get("Item")
        if not item:
            raise ValueError(
                f"Job log with job_id {job_id} and timestamp {timestamp} not found"
            )

        return itemToJobLog(item)

    def listJobLogs(
        self,
        job_id: str,
        limit: Optional[int] = None,
        lastEvaluatedKey: Optional[Dict] = None,
    ) -> Tuple[List[JobLog], Optional[Dict]]:
        """Lists all log entries for a specific job.

        Args:
            job_id (str): The ID of the job.
            limit (int, optional): The maximum number of items to return.
            lastEvaluatedKey (Dict, optional): The key to start pagination from.

        Returns:
            Tuple[List[JobLog], Optional[Dict]]: A tuple containing the list of job logs and the last evaluated key.

        Raises:
            ValueError: If job_id is None.
            ClientError: If a DynamoDB error occurs.
        """
        if job_id is None:
            raise ValueError("job_id cannot be None")

        # Prepare KeyConditionExpression
        key_condition_expression = "PK = :pk AND begins_with(SK, :sk_prefix)"
        expression_attribute_values = {
            ":pk": {"S": f"JOB#{job_id}"},
            ":sk_prefix": {"S": "LOG#"},
        }

        # Prepare query parameters
        query_params = {
            "TableName": self.table_name,
            "KeyConditionExpression": key_condition_expression,
            "ExpressionAttributeValues": expression_attribute_values,
        }

        if limit is not None:
            query_params["Limit"] = limit

        if lastEvaluatedKey is not None:
            query_params["ExclusiveStartKey"] = lastEvaluatedKey

        # Execute query
        response = self._client.query(**query_params)

        # Process results
        job_logs = [itemToJobLog(item) for item in response.get("Items", [])]
        last_evaluated_key = response.get("LastEvaluatedKey")

        return job_logs, last_evaluated_key

    def deleteJobLog(self, job_log: JobLog):
        """Deletes a job log entry from the DynamoDB table.

        Args:
            job_log (JobLog): The job log to delete.

        Raises:
            ValueError: If job_log is None or not a JobLog instance.
            ClientError: If a DynamoDB error occurs.
        """
        if job_log is None:
            raise ValueError("job_log cannot be None")
        if not isinstance(job_log, JobLog):
            raise ValueError(
                f"job_log must be a JobLog instance, got {type(job_log)}"
            )

        try:
            self._client.delete_item(
                TableName=self.table_name,
                Key={
                    "PK": {"S": f"JOB#{job_log.job_id}"},
                    "SK": {"S": f"LOG#{job_log.timestamp}"},
                },
                ConditionExpression="attribute_exists(PK) AND attribute_exists(SK)",
            )
        except ClientError as e:
            if (
                e.response["Error"]["Code"]
                == "ConditionalCheckFailedException"
            ):
                raise ValueError(
                    f"Job log for job {job_log.job_id} with timestamp {job_log.timestamp} not found"
                )
            raise
