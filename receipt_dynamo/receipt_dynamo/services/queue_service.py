"""
Queue Service layer for receipt_dynamo operations.

This module provides a service class that encapsulates all DynamoDB operations
related to queues and provides a clean API for client applications to use.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities.queue_job import QueueJob
from receipt_dynamo.entities.rwl_queue import Queue


class QueueService:
    """
    Service layer for queue-related operations.

    This class encapsulates all interactions with DynamoDB for queue entities
    and provides a clean API for client applications.
    """

    def __init__(self, table_name: str, region: str = "us-east-1"):
        """
        Initialize the QueueService.

        Args:
            table_name: The name of the DynamoDB table
            region: AWS region (defaults to us-east-1)
        """
        self.dynamo_client = DynamoClient(table_name=table_name, region=region)
        self.table_name = table_name
        self.region = region

    # Queue operations
    def create_queue(
        self,
        queue_id: str,
        name: str,
        description: str,
        created_by: str,
        metadata: Optional[Dict[str, Any]] = None,
        tags: Optional[Dict[str, str]] = None,
    ) -> Queue:
        """
        Create a new queue in DynamoDB.

        Args:
            queue_id: UUID identifying the queue
            name: The name of the queue
            description: A description of the queue
            created_by: The user who created the queue
            metadata: Additional metadata for the queue
            tags: Tags associated with the queue

        Returns:
            The created Queue object

        Raises:
            ValueError: When a queue with the same ID already exists
        """
        queue = Queue(
            queue_id=queue_id,
            name=name,
            description=description,
            created_at=datetime.now(),
            created_by=created_by,
            metadata=metadata or {},
            tags=tags or {},
        )

        self.dynamo_client.addQueue(queue)
        return queue

    def get_queue(self, queue_id: str) -> Queue:
        """
        Get a queue by its ID.

        Args:
            queue_id: The ID of the queue to retrieve

        Returns:
            The Queue object

        Raises:
            Exception: When the queue is not found
        """
        return self.dynamo_client.getQueue(queue_id)

    def update_queue(self, queue: Queue) -> None:
        """
        Update an existing queue.

        Args:
            queue: The queue object to update

        Raises:
            Exception: When the queue does not exist
        """
        self.dynamo_client.updateQueue(queue)

    def delete_queue(self, queue: Queue) -> None:
        """
        Delete a queue.

        Args:
            queue: The queue to delete

        Raises:
            Exception: When the queue does not exist
        """
        self.dynamo_client.deleteQueue(queue)

    def list_queues(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict] = None,
    ) -> tuple[list[Queue], Optional[Dict]]:
        """
        List queues with pagination support.

        Args:
            limit: Maximum number of queues to return
            last_evaluated_key: The key to continue from (for pagination)

        Returns:
            A tuple containing a list of Queue objects and the last evaluated key
        """
        return self.dynamo_client.listQueues(limit, last_evaluated_key)

    def list_queues_by_user(
        self,
        user_id: str,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict] = None,
    ) -> tuple[list[Queue], Optional[Dict]]:
        """
        List queues created by a specific user.

        Args:
            user_id: The user ID to filter by
            limit: Maximum number of queues to return
            last_evaluated_key: The key to continue from (for pagination)

        Returns:
            A tuple containing a list of Queue objects and the last evaluated key
        """
        return self.dynamo_client.listQueuesByUser(
            user_id, limit, last_evaluated_key
        )

    # Queue job operations
    def add_job_to_queue(
        self,
        queue_id: str,
        job_id: str,
        priority: int = 0,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> QueueJob:
        """
        Add a job to a queue.

        Args:
            queue_id: The ID of the queue
            job_id: The ID of the job
            priority: Priority value for the job (higher values = higher priority)
            metadata: Additional metadata for the queue job

        Returns:
            The created QueueJob object

        Raises:
            ValueError: When the job is already in the queue
        """
        queue_job = QueueJob(
            queue_id=queue_id,
            job_id=job_id,
            added_at=datetime.now(),
            priority=priority,
            status="pending",
            metadata=metadata or {},
        )

        self.dynamo_client.addQueueJob(queue_job)
        return queue_job

    def get_queue_job(self, queue_id: str, job_id: str) -> QueueJob:
        """
        Get a job in a queue.

        Args:
            queue_id: The ID of the queue
            job_id: The ID of the job

        Returns:
            The QueueJob object

        Raises:
            Exception: When the queue job is not found
        """
        return self.dynamo_client.getQueueJob(queue_id, job_id)

    def update_queue_job(self, queue_job: QueueJob) -> None:
        """
        Update a job in a queue.

        Args:
            queue_job: The queue job to update

        Raises:
            Exception: When the queue job does not exist
        """
        self.dynamo_client.updateQueueJob(queue_job)

    def delete_queue_job(self, queue_job: QueueJob) -> None:
        """
        Remove a job from a queue.

        Args:
            queue_job: The queue job to delete

        Raises:
            Exception: When the queue job does not exist
        """
        self.dynamo_client.deleteQueueJob(queue_job)

    def list_jobs_in_queue(
        self,
        queue_id: str,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict] = None,
    ) -> tuple[list[QueueJob], Optional[Dict]]:
        """
        List jobs in a queue with pagination support.

        Args:
            queue_id: The ID of the queue
            limit: Maximum number of queue jobs to return
            last_evaluated_key: The key to continue from (for pagination)

        Returns:
            A tuple containing a list of QueueJob objects and the last evaluated key
        """
        return self.dynamo_client.listQueueJobsByQueue(
            queue_id, limit, last_evaluated_key
        )

    def list_queues_for_job(
        self,
        job_id: str,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict] = None,
    ) -> tuple[list[QueueJob], Optional[Dict]]:
        """
        List queues containing a specific job with pagination support.

        Args:
            job_id: The ID of the job
            limit: Maximum number of queue jobs to return
            last_evaluated_key: The key to continue from (for pagination)

        Returns:
            A tuple containing a list of QueueJob objects and the last evaluated key
        """
        return self.dynamo_client.listQueueJobsByJob(
            job_id, limit, last_evaluated_key
        )

    def get_next_job(self, queue_id: str) -> Optional[QueueJob]:
        """
        Get the next job from a queue based on priority and time added.

        Args:
            queue_id: The ID of the queue

        Returns:
            The next QueueJob object, or None if the queue is empty
        """
        # Get pending jobs and sort by priority (desc) and added_at (asc)
        jobs, _ = self.dynamo_client.listQueueJobsByQueue(
            queue_id, status="pending"
        )
        if not jobs:
            return None

        # Sort by priority (descending) and then by added_at (ascending)
        sorted_jobs = sorted(jobs, key=lambda j: (-j.priority, j.added_at))

        # Return the highest priority job
        return sorted_jobs[0] if sorted_jobs else None

    def claim_job(self, queue_id: str, instance_id: str) -> Optional[QueueJob]:
        """
        Claim the next job from a queue for an instance.

        Args:
            queue_id: The ID of the queue
            instance_id: The ID of the instance claiming the job

        Returns:
            The claimed QueueJob object, or None if no jobs are available
        """
        next_job = self.get_next_job(queue_id)
        if not next_job:
            return None

        # Update job status to claimed
        next_job.status = "claimed"
        next_job.claimed_at = datetime.now()
        next_job.claimed_by = instance_id
        self.update_queue_job(next_job)

        return next_job

    def mark_job_completed(
        self, queue_id: str, job_id: str, success: bool
    ) -> None:
        """
        Mark a job in a queue as completed.

        Args:
            queue_id: The ID of the queue
            job_id: The ID of the job
            success: Whether the job completed successfully

        Raises:
            Exception: When the queue job does not exist
        """
        queue_job = self.get_queue_job(queue_id, job_id)
        queue_job.status = "succeeded" if success else "failed"
        queue_job.completed_at = datetime.now()
        self.update_queue_job(queue_job)

    def release_job(self, queue_id: str, job_id: str) -> None:
        """
        Release a claimed job back to the queue.

        Args:
            queue_id: The ID of the queue
            job_id: The ID of the job

        Raises:
            Exception: When the queue job does not exist
        """
        queue_job = self.get_queue_job(queue_id, job_id)
        queue_job.status = "pending"
        queue_job.claimed_at = None
        queue_job.claimed_by = None
        self.update_queue_job(queue_job)
