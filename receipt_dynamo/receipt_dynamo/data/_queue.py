from typing import TYPE_CHECKING, Any, Dict, Optional

from botocore.exceptions import ClientError

from receipt_dynamo.data._base import DynamoClientProtocol

if TYPE_CHECKING:
    from receipt_dynamo.data._base import (
        PutRequestTypeDef,
        QueryInputTypeDef,
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
from receipt_dynamo.entities.queue_job import QueueJob, item_to_queue_job
from receipt_dynamo.entities.rwl_queue import Queue, item_to_queue


def validate_last_evaluated_key(lek: Dict[str, Any]) -> None:
    """Validates the format of a LastEvaluatedKey for pagination.

    Args:
        lek (dict): The LastEvaluatedKey to validate.

    Raises:
        ValueError: If the LastEvaluatedKey is invalid.
    """
    # If None, it's valid (means not provided)
    if lek is None:
        return

    if not all(k in lek for k in ["PK", "SK"]):
        raise ValueError("LastEvaluatedKey must contain PK and SK")

    # Check if the values are in the correct format
    if not all(
        isinstance(lek[k], dict) and "S" in lek[k] for k in ["PK", "SK"]
    ):
        raise ValueError(
            "LastEvaluatedKey values must be in DynamoDB format with 'S' attribute"
        )


class _Queue(DynamoClientProtocol):
    """Queue-related operations for the DynamoDB client."""

    def add_queue(self, queue: Queue) -> None:
        """Adds a queue to the DynamoDB table.

        Args:
            queue (Queue): The queue to add.

        Raises:
            ValueError: If the queue is invalid or already exists.
            ClientError: If there is a problem with the DynamoDB service.
        """
        if queue is None:
            raise ValueError("queue cannot be None")

        if not isinstance(queue, Queue):
            raise ValueError("queue must be an instance of Queue")

        try:
            # Create the DynamoDB item from the Queue object
            item = queue.to_item()

            # Add the item to the DynamoDB table with a condition expression to
            # ensure it doesn't already exist
            self._client.put_item(
                TableName=self.table_name,
                Item=item,
                ConditionExpression="attribute_not_exists(PK)",
            )
        except ClientError as e:
            if (
                e.response["Error"]["Code"]
                == "ConditionalCheckFailedException"
            ):
                raise ValueError(
                    f"Queue {queue.queue_name} already exists"
                ) from e
            else:
                # Re-raise the original ClientError for other DynamoDB-related
                # issues
                raise

    def add_queues(self, queues: list[Queue]) -> None:
        """Adds multiple queues to the DynamoDB table.

        Args:
            queues (list[Queue]): The list of queues to add.

        Raises:
            ValueError: If the queues parameter is invalid.
            ClientError: If there is a problem with the DynamoDB service.
        """
        if queues is None:
            raise ValueError("queues cannot be None")

        if not isinstance(queues, list):
            raise ValueError("queues must be a list")

        if not all(isinstance(queue, Queue) for queue in queues):
            raise ValueError("all items in queues must be Queue instances")

        # If the list is empty, there's nothing to do
        if not queues:
            return

        # Use the batch_write_item operation to write multiple items
        # efficiently
        try:
            # Prepare the batch request
            request_items = {
                self.table_name: [
                    WriteRequestTypeDef(
                        PutRequest=PutRequestTypeDef(Item=queue.to_item())
                    )
                    for queue in queues
                ]
            }

            # Execute the batch write and handle unprocessed items
            response = self._client.batch_write_item(
                RequestItems=request_items
            )

            # Check for unprocessed items and retry them
            unprocessed_items = response.get("UnprocessedItems", {})

            while unprocessed_items and unprocessed_items.get(
                self.table_name, []
            ):
                # Wait a moment before retrying
                import time

                time.sleep(0.5)

                # Retry the unprocessed items
                response = self._client.batch_write_item(
                    RequestItems=unprocessed_items
                )
                unprocessed_items = response.get("UnprocessedItems", {})

        except ClientError as e:
            # Handle different types of ClientError
            if (
                e.response["Error"]["Code"]
                == "ProvisionedThroughputExceededException"
            ):
                raise ClientError(
                    e.response,
                    "DynamoDB Provisioned Throughput Exceeded: Consider retrying with exponential backoff",
                )
            elif e.response["Error"]["Code"] == "InternalServerError":
                raise ClientError(
                    e.response,
                    "DynamoDB Internal Server Error: Consider retrying the operation",
                )
            elif e.response["Error"]["Code"] == "ValidationException":
                raise ClientError(
                    e.response,
                    "DynamoDB Validation Exception: Check the format of your request",
                )
            elif e.response["Error"]["Code"] == "AccessDeniedException":
                raise ClientError(
                    e.response,
                    "Access Denied: Ensure your IAM policy has the dynamodb:BatchWriteItem permission",
                )
            else:
                # Re-raise other types of ClientError
                raise ClientError(
                    e.response,
                    f"Error batch writing queues: {e.response['Error']['Code']}: {e.response['Error']['Message']}",
                )

    def update_queue(self, queue: Queue) -> None:
        """Updates a queue in the DynamoDB table.

        Args:
            queue (Queue): The queue to update.

        Raises:
            ValueError: If the queue is invalid or doesn't exist.
            ClientError: If there is a problem with the DynamoDB service.
        """
        if queue is None:
            raise ValueError("queue cannot be None")

        if not isinstance(queue, Queue):
            raise ValueError("queue must be an instance of Queue")

        try:
            # Create the DynamoDB item from the Queue object
            item = queue.to_item()

            # Update the item in the DynamoDB table with a condition expression
            # to ensure it already exists
            self._client.put_item(
                TableName=self.table_name,
                Item=item,
                ConditionExpression="attribute_exists(PK) AND attribute_exists(SK)",
            )
        except ClientError as e:
            if (
                e.response["Error"]["Code"]
                == "ConditionalCheckFailedException"
            ):
                raise ValueError(
                    f"Queue {queue.queue_name} does not exist"
                ) from e
            else:
                # Re-raise the original ClientError for other DynamoDB-related
                # issues
                raise

    def delete_queue(self, queue: Queue) -> None:
        """Deletes a queue from the DynamoDB table.

        Args:
            queue (Queue): The queue to delete.

        Raises:
            ValueError: If the queue is invalid or doesn't exist.
            ClientError: If there is a problem with the DynamoDB service.
        """
        if queue is None:
            raise ValueError("queue cannot be None")

        if not isinstance(queue, Queue):
            raise ValueError("queue must be an instance of Queue")

        try:
            # Delete the item from the DynamoDB table with a condition
            # expression to ensure it exists
            self._client.delete_item(
                TableName=self.table_name,
                Key=queue.key,
                ConditionExpression="attribute_exists(PK) AND attribute_exists(SK)",
            )
        except ClientError as e:
            if (
                e.response["Error"]["Code"]
                == "ConditionalCheckFailedException"
            ):
                raise ValueError(
                    f"Queue {queue.queue_name} does not exist"
                ) from e
            else:
                # Re-raise the original ClientError for other DynamoDB-related
                # issues
                raise

    def get_queue(self, queue_name: str) -> Queue:
        """Gets a queue from the DynamoDB table.

        Args:
            queue_name (str): The name of the queue to get.

        Returns:
            Queue: The queue object.

        Raises:
            ValueError: If the queue_name is invalid or the queue doesn't exist.
            ClientError: If there is a problem with the DynamoDB service.
        """
        if not queue_name:
            raise ValueError("queue_name cannot be empty")

        try:
            # Get the item from the DynamoDB table
            response = self._client.get_item(
                TableName=self.table_name,
                Key={"PK": {"S": f"QUEUE#{queue_name}"}, "SK": {"S": "QUEUE"}},
            )

            # Check if the item exists
            if "Item" not in response:
                raise ValueError(f"Queue {queue_name} not found")

            # Convert the DynamoDB item to a Queue object
            return item_to_queue(response["Item"])

        except ClientError as e:
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                raise ClientError(
                    e.response,
                    f"Table {self.table_name} not found",
                )
            elif e.response["Error"]["Code"] == "InternalServerError":
                raise ClientError(
                    e.response,
                    "DynamoDB Internal Server Error: Consider retrying the operation",
                )
            else:
                # Re-raise other types of ClientError
                raise

    def list_queues(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> tuple[list[Queue], Optional[Dict[str, Any]]]:
        """Lists all queues in the DynamoDB table.

        Args:
            limit (int, optional): The maximum number of queues to return. Defaults to None.
            last_evaluated_key (dict, optional): The pagination token from a previous request. Defaults to None.

        Returns:
            tuple[list[Queue], dict]: A tuple containing a list of Queue objects and the LastEvaluatedKey for pagination.

        Raises:
            ValueError: If the last_evaluated_key is invalid.
            ClientError: If there is a problem with the DynamoDB service.
        """
        if last_evaluated_key is not None:
            validate_last_evaluated_key(last_evaluated_key)

        # Prepare the query parameters
        query_params: QueryInputTypeDef = {
            "TableName": self.table_name,
            "IndexName": "GSI1",
            "KeyConditionExpression": "GSI1PK = :queue_type",
            "ExpressionAttributeValues": {":queue_type": {"S": "QUEUE"}},
        }

        # Add optional parameters if provided
        if limit is not None:
            query_params["Limit"] = limit

        if last_evaluated_key is not None:
            query_params["ExclusiveStartKey"] = last_evaluated_key

        try:
            # Execute the query
            response = self._client.query(**query_params)

            # Convert the DynamoDB items to Queue objects
            queues = [
                item_to_queue(item) for item in response.get("Items", [])
            ]

            # Return the queues and the LastEvaluatedKey for pagination
            return queues, response.get("LastEvaluatedKey")

        except ClientError as e:
            # Handle different types of ClientError
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                raise ClientError(
                    e.response,
                    f"Table {self.table_name} or GSI1 index not found",
                )
            else:
                # Re-raise other types of ClientError
                raise ClientError(
                    e.response,
                    f"Error listing queues: {e.response['Error']['Code']}: {e.response['Error']['Message']}",
                )

    def add_job_to_queue(self, queue_job: QueueJob) -> None:
        """Adds a job to a queue in the DynamoDB table.

        Args:
            queue_job (QueueJob): The queue-job association to add.

        Raises:
            ValueError: If the queue_job is invalid or already exists.
            ClientError: If there is a problem with the DynamoDB service.
        """
        if queue_job is None:
            raise ValueError("queue_job cannot be None")

        if not isinstance(queue_job, QueueJob):
            raise ValueError("queue_job must be an instance of QueueJob")

        try:
            # Create the DynamoDB item from the QueueJob object
            item = queue_job.to_item()

            # Add the item to the DynamoDB table with a condition expression to
            # ensure it doesn't already exist
            self._client.put_item(
                TableName=self.table_name,
                Item=item,
                ConditionExpression="attribute_not_exists(PK) OR attribute_not_exists(SK)",
            )

            # Update the job count for the queue
            queue = self.get_queue(queue_job.queue_name)
            queue.job_count += 1
            self.update_queue(queue)

        except ClientError as e:
            if (
                e.response["Error"]["Code"]
                == "ConditionalCheckFailedException"
            ):
                raise ValueError(
                    f"Job {queue_job.job_id} is already in queue {queue_job.queue_name}"
                )
            else:
                # Re-raise the original ClientError for other DynamoDB-related
                # issues
                raise

    def remove_job_from_queue(self, queue_job: QueueJob) -> None:
        """Removes a job from a queue in the DynamoDB table.

        Args:
            queue_job (QueueJob): The queue-job association to remove.

        Raises:
            ValueError: If the queue_job is invalid or doesn't exist.
            ClientError: If there is a problem with the DynamoDB service.
        """
        if queue_job is None:
            raise ValueError("queue_job cannot be None")

        if not isinstance(queue_job, QueueJob):
            raise ValueError("queue_job must be an instance of QueueJob")

        try:
            # Delete the item from the DynamoDB table with a condition
            # expression to ensure it exists
            self._client.delete_item(
                TableName=self.table_name,
                Key=queue_job.key,
                ConditionExpression="attribute_exists(PK) AND attribute_exists(SK)",
            )

            # Update the job count for the queue
            queue = self.get_queue(queue_job.queue_name)
            queue.job_count = max(
                0, queue.job_count - 1
            )  # Ensure job_count doesn't go below 0
            self.update_queue(queue)

        except ClientError as e:
            if (
                e.response["Error"]["Code"]
                == "ConditionalCheckFailedException"
            ):
                raise ValueError(
                    f"Job {queue_job.job_id} is not in queue {queue_job.queue_name}"
                )
            else:
                # Re-raise the original ClientError for other DynamoDB-related
                # issues
                raise

    def list_jobs_in_queue(
        self,
        queue_name: str,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> tuple[list[QueueJob], Optional[Dict[str, Any]]]:
        """Lists all jobs in a queue in the DynamoDB table.

        Args:
            queue_name (str): The name of the queue to list jobs from.
            limit (int, optional): The maximum number of jobs to return. Defaults to None.
            last_evaluated_key (dict, optional): The pagination token from a previous request. Defaults to None.

        Returns:
            tuple[list[QueueJob], dict]: A tuple containing a list of QueueJob objects and the LastEvaluatedKey for pagination.

        Raises:
            ValueError: If the queue_name is invalid or the last_evaluated_key is invalid.
            ClientError: If there is a problem with the DynamoDB service.
        """
        if not queue_name:
            raise ValueError("queue_name cannot be empty")

        # Check if the queue exists
        try:
            self.get_queue(queue_name)
        except ValueError as e:
            raise e

        if last_evaluated_key is not None:
            validate_last_evaluated_key(last_evaluated_key)

        # Prepare the query parameters
        query_params: QueryInputTypeDef = {
            "TableName": self.table_name,
            "KeyConditionExpression": "PK = :pk AND begins_with(SK, :job_prefix)",
            "ExpressionAttributeValues": {
                ":pk": {"S": f"QUEUE#{queue_name}"},
                ":job_prefix": {"S": "JOB#"},
            },
        }

        # Add optional parameters if provided
        if limit is not None:
            query_params["Limit"] = limit

        if last_evaluated_key is not None:
            query_params["ExclusiveStartKey"] = last_evaluated_key

        try:
            # Execute the query
            response = self._client.query(**query_params)

            # Convert the DynamoDB items to QueueJob objects
            queue_jobs = [
                item_to_queue_job(item) for item in response.get("Items", [])
            ]

            # Return the queue jobs and the LastEvaluatedKey for pagination
            return queue_jobs, response.get("LastEvaluatedKey")

        except ClientError as e:
            # Handle different types of ClientError
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                raise ClientError(
                    e.response,
                    f"Table {self.table_name} not found",
                )
            else:
                # Re-raise other types of ClientError
                raise ClientError(
                    e.response,
                    f"Error listing jobs in queue: {e.response['Error']['Code']}: {e.response['Error']['Message']}",
                )

    def find_queues_for_job(
        self,
        job_id: str,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> tuple[list[QueueJob], Optional[Dict[str, Any]]]:
        """Finds all queues that contain a specific job.

        Args:
            job_id (str): The ID of the job to find queues for.
            limit (int, optional): The maximum number of queues to return. Defaults to None.
            last_evaluated_key (dict, optional): The pagination token from a previous request. Defaults to None.

        Returns:
            tuple[list[QueueJob], dict]: A tuple containing a list of QueueJob objects and the LastEvaluatedKey for pagination.

        Raises:
            ValueError: If the job_id is invalid or the last_evaluated_key is invalid.
            ClientError: If there is a problem with the DynamoDB service.
        """
        if not job_id:
            raise ValueError("job_id cannot be empty")

        if last_evaluated_key is not None:
            validate_last_evaluated_key(last_evaluated_key)

        # Prepare the query parameters
        query_params: QueryInputTypeDef = {
            "TableName": self.table_name,
            "IndexName": "GSI1",
            "KeyConditionExpression": "GSI1PK = :job_type AND begins_with(GSI1SK, :job_prefix)",
            "ExpressionAttributeValues": {
                ":job_type": {"S": "JOB"},
                ":job_prefix": {"S": f"JOB#{job_id}#QUEUE#"},
            },
        }

        # Add optional parameters if provided
        if limit is not None:
            query_params["Limit"] = limit

        if last_evaluated_key is not None:
            query_params["ExclusiveStartKey"] = last_evaluated_key

        try:
            # Execute the query
            response = self._client.query(**query_params)

            # Convert the DynamoDB items to QueueJob objects
            queue_jobs = [
                item_to_queue_job(item) for item in response.get("Items", [])
            ]

            # Return the queue jobs and the LastEvaluatedKey for pagination
            return queue_jobs, response.get("LastEvaluatedKey")

        except ClientError as e:
            # Handle different types of ClientError
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                raise ClientError(
                    e.response,
                    f"Table {self.table_name} or GSI1 index not found",
                )
            else:
                # Re-raise other types of ClientError
                raise ClientError(
                    e.response,
                    f"Error finding queues for job: {e.response['Error']['Code']}: {e.response['Error']['Message']}",
                )
