from typing import TYPE_CHECKING, Any, Dict, Optional

from receipt_dynamo.data.base_operations import (
    BatchOperationsMixin,
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    handle_dynamodb_errors,
)
from receipt_dynamo.data.shared_exceptions import (
    EntityNotFoundError,
    EntityValidationError,
)
from receipt_dynamo.entities.queue_job import QueueJob, item_to_queue_job
from receipt_dynamo.entities.rwl_queue import Queue, item_to_queue

if TYPE_CHECKING:
    from receipt_dynamo.data.base_operations import (
        QueryInputTypeDef,
    )


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
        raise EntityValidationError("LastEvaluatedKey must contain PK and SK")

    # Check if the values are in the correct format
    if not all(
        isinstance(lek[k], dict) and "S" in lek[k] for k in ["PK", "SK"]
    ):
        raise EntityValidationError(
            (
                "LastEvaluatedKey values must be in "
                "DynamoDB format with 'S' attribute"
            )
        )


class _Queue(
    DynamoDBBaseOperations,
    SingleEntityCRUDMixin,
    BatchOperationsMixin,
):
    """Queue-related operations for the DynamoDB client."""

    @handle_dynamodb_errors("add_queue")
    def add_queue(self, queue: Queue) -> None:
        """Adds a queue to the DynamoDB table.

        Args:
            queue (Queue): The queue to add.

        Raises:
            ValueError: If the queue is invalid or already exists.
        """
        self._validate_entity(queue, Queue, "queue")
        self._add_entity(
            queue, condition_expression="attribute_not_exists(PK)"
        )

    @handle_dynamodb_errors("add_queues")
    def add_queues(self, queues: list[Queue]) -> None:
        """Adds multiple queues to the DynamoDB table.

        Args:
            queues (list[Queue]): The list of queues to add.

        Raises:
            ValueError: If the queues parameter is invalid.
        """
        self._validate_entity_list(queues, Queue, "queues")

        # If the list is empty, there's nothing to do
        if not queues:
            return

        # Use the mixin's batch operation method directly
        self._add_entities_batch(queues, Queue, "queues")

    @handle_dynamodb_errors("update_queue")
    def update_queue(self, queue: Queue) -> None:
        """Updates a queue in the DynamoDB table.

        Args:
            queue (Queue): The queue to update.

        Raises:
            ValueError: If the queue is invalid or doesn't exist.
        """
        self._validate_entity(queue, Queue, "queue")
        self._update_entity(
            queue,
            condition_expression=(
                "attribute_exists(PK) AND attribute_exists(SK)"
            ),
        )

    @handle_dynamodb_errors("delete_queue")
    def delete_queue(self, queue: Queue) -> None:
        """Deletes a queue from the DynamoDB table.

        Args:
            queue (Queue): The queue to delete.

        Raises:
            ValueError: If the queue is invalid or doesn't exist.
        """
        self._validate_entity(queue, Queue, "queue")
        self._delete_entity(
            queue,
            condition_expression=(
                "attribute_exists(PK) AND attribute_exists(SK)"
            ),
        )

    @handle_dynamodb_errors("get_queue")
    def get_queue(self, queue_name: str) -> Queue:
        """Gets a queue from the DynamoDB table.

        Args:
            queue_name (str): The name of the queue to get.

        Returns:
            Queue: The queue object.

        Raises:
            ValueError: If the queue_name is invalid or the queue does not
                exist.
        """
        if not queue_name:
            raise EntityValidationError("queue_name cannot be empty")

        result = self._get_entity(
            primary_key=f"QUEUE#{queue_name}",
            sort_key="QUEUE",
            entity_class=Queue,
            converter_func=item_to_queue,
        )

        if result is None:
            raise EntityNotFoundError(f"Queue {queue_name} not found")

        return result

    @handle_dynamodb_errors("list_queues")
    def list_queues(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> tuple[list[Queue], Optional[Dict[str, Any]]]:
        """Lists all queues in the DynamoDB table.

        Args:
            limit (int, optional): The maximum number of queues to return.
                Defaults to None.
            last_evaluated_key (dict, optional): The pagination token from a
                previous request. Defaults to None.

        Returns:
            tuple[list[Queue], dict]: A tuple containing a list of Queue
                objects and the LastEvaluatedKey for pagination.

        Raises:
            ValueError: If the last_evaluated_key is invalid.
        """
        if last_evaluated_key is not None:
            validate_last_evaluated_key(last_evaluated_key)

        return self._query_entities(
            index_name="GSI1",
            key_condition_expression="GSI1PK = :queue_type",
            expression_attribute_names=None,
            expression_attribute_values={":queue_type": {"S": "QUEUE"}},
            converter_func=item_to_queue,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("add_job_to_queue")
    def add_job_to_queue(self, queue_job: QueueJob) -> None:
        """Adds a job to a queue in the DynamoDB table.

        Args:
            queue_job (QueueJob): The queue-job association to add.

        Raises:
            ValueError: If the queue_job is invalid or already exists.
        """
        self._validate_entity(queue_job, QueueJob, "queue_job")

        # Add the item to the DynamoDB table with a condition expression
        # to ensure it doesn't already exist
        self._add_entity(
            queue_job,
            condition_expression=(
                "attribute_not_exists(PK) OR attribute_not_exists(SK)"
            ),
        )

        # Update the job count for the queue
        queue = self.get_queue(queue_job.queue_name)
        queue.job_count += 1
        self.update_queue(queue)

    @handle_dynamodb_errors("remove_job_from_queue")
    def remove_job_from_queue(self, queue_job: QueueJob) -> None:
        """Removes a job from a queue in the DynamoDB table.

        Args:
            queue_job (QueueJob): The queue-job association to remove.

        Raises:
            ValueError: If the queue_job is invalid or doesn't exist.
        """
        self._validate_entity(queue_job, QueueJob, "queue_job")

        # Delete the item from the DynamoDB table with a condition
        # expression to ensure it exists
        self._delete_entity(
            queue_job,
            condition_expression=(
                "attribute_exists(PK) AND attribute_exists(SK)"
            ),
        )

        # Update the job count for the queue
        queue = self.get_queue(queue_job.queue_name)
        queue.job_count = max(
            0, queue.job_count - 1
        )  # Ensure job_count doesn't go below 0
        self.update_queue(queue)

    @handle_dynamodb_errors("list_jobs_in_queue")
    def list_jobs_in_queue(
        self,
        queue_name: str,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> tuple[list[QueueJob], Optional[Dict[str, Any]]]:
        """Lists all jobs in a queue in the DynamoDB table.

        Args:
            queue_name (str): The name of the queue to list jobs from.
            limit (int, optional): The maximum number of jobs to return.
                Defaults to None.
            last_evaluated_key (dict, optional): The pagination token from a
                previous request. Defaults to None.

        Returns:
            tuple[list[QueueJob], dict]: A tuple containing a list of
                QueueJob objects and the LastEvaluatedKey for pagination.

        Raises:
            ValueError: If the queue_name is invalid or the last_evaluated_key
                is invalid.
        """
        if not queue_name:
            raise EntityValidationError("queue_name cannot be empty")

        # Check if the queue exists
        try:
            self.get_queue(queue_name)
        except ValueError as e:
            raise e

        if last_evaluated_key is not None:
            validate_last_evaluated_key(last_evaluated_key)

        return self._query_entities(
            index_name=None,
            key_condition_expression=(
                "PK = :pk AND begins_with(SK, :job_prefix)"
            ),
            expression_attribute_names=None,
            expression_attribute_values={
                ":pk": {"S": f"QUEUE#{queue_name}"},
                ":job_prefix": {"S": "JOB#"},
            },
            converter_func=item_to_queue_job,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )

    @handle_dynamodb_errors("find_queues_for_job")
    def find_queues_for_job(
        self,
        job_id: str,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict[str, Any]] = None,
    ) -> tuple[list[QueueJob], Optional[Dict[str, Any]]]:
        """Finds all queues that contain a specific job.

        Args:
            job_id (str): The ID of the job to find queues for.
            limit (int, optional): The maximum number of queues to return.
                Defaults to None.
            last_evaluated_key (dict, optional): The pagination token from a
                previous request. Defaults to None.

        Returns:
            tuple[list[QueueJob], dict]: A tuple containing a list of
                QueueJob objects and the LastEvaluatedKey for pagination.

        Raises:
            ValueError: If the job_id is invalid or the last_evaluated_key
                is invalid.
        """
        if not job_id:
            raise EntityValidationError("job_id cannot be empty")

        if last_evaluated_key is not None:
            validate_last_evaluated_key(last_evaluated_key)

        return self._query_entities(
            index_name="GSI1",
            key_condition_expression=(
                "GSI1PK = :job_type AND begins_with(GSI1SK, :job_prefix)"
            ),
            expression_attribute_names=None,
            expression_attribute_values={
                ":job_type": {"S": "JOB"},
                ":job_prefix": {"S": f"JOB#{job_id}#QUEUE#"},
            },
            converter_func=item_to_queue_job,
            limit=limit,
            last_evaluated_key=last_evaluated_key,
        )
