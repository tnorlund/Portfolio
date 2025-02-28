from botocore.exceptions import ClientError

from receipt_dynamo.entities.queue_job import QueueJob, itemToQueueJob


def validate_last_evaluated_key(lek: dict) -> None:
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
        raise ValueError("LastEvaluatedKey must contain both PK and SK")

    # Check if the values are in the correct format
    if not all(isinstance(lek[k], dict) and "S" in lek[k]
               for k in ["PK", "SK"]):
        raise ValueError("LastEvaluatedKey has invalid format")


class Queue:
    def listJobsInQueue(self, queue_name: str, limit: int = None, lastEvaluatedKey: dict = None) -> tuple[list[QueueJob], dict]:
        """Lists all jobs in a specified queue.

        Args:
            queue_name (str): The name of the queue to list jobs for.
            limit (int, optional): The maximum number of items to return. Defaults to None.
            lastEvaluatedKey (dict, optional): The LastEvaluatedKey from a previous query. Defaults to None.

        Returns:
            tuple[list[QueueJob], dict]: A tuple containing a list of QueueJob objects and the LastEvaluatedKey for pagination.

        Raises:
            ValueError: If the queue_name is invalid or the queue doesn't exist.
            ClientError: If there is a problem with the DynamoDB service.
        """
        if not queue_name:
            raise ValueError("queue_name cannot be empty")

        # Check if the queue exists
        try:
            self.getQueue(queue_name)
        except ValueError as e:
            raise e

        # Validate the lastEvaluatedKey if provided
        if lastEvaluatedKey:
            validate_last_evaluated_key(lastEvaluatedKey)

        # Set up the query parameters
        query_params = {"TableName": self.table_name,
            "KeyConditionExpression": "PK = :pk AND begins_with(SK, :sk_prefix)",
            "ExpressionAttributeValues": {":pk": {"S": f"QUEUE#{queue_name}"},
                ":sk_prefix": {"S": "JOB#"},},}

        # Add the limit if provided
        if limit is not None:
            query_params["Limit"] = limit

        # Add the LastEvaluatedKey if provided
        if lastEvaluatedKey:
            query_params["ExclusiveStartKey"] = lastEvaluatedKey

        try:
            # Query the DynamoDB table
            response = self._client.query(**query_params)

            # Convert the items to QueueJob objects
            queue_jobs = []
            for item in response.get("Items", []):
                queue_jobs.append(itemToQueueJob(item))

            # Return the list of QueueJob objects and the LastEvaluatedKey for
            # pagination
            return queue_jobs, response.get("LastEvaluatedKey", {})

        except ClientError as e:
            # Handle specific DynamoDB errors
            if e.response["Error"]["Code"] == "ResourceNotFoundException":
                raise ValueError(f"Table {self.table_name} does not exist")
            # Re-raise other ClientErrors
            raise
