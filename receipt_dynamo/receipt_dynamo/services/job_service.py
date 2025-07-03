"""Refactored Job Service with modular operations."""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import boto3

from receipt_dynamo import Job, JobStatus, item_to_job
from receipt_dynamo.data._job import _Job
from receipt_dynamo.services.job_operations import (JobCheckpointOperations,
                                                    JobDependencyOperations,
                                                    JobMetricOperations,
                                                    JobResourceOperations,
                                                    JobStatusOperations)


class JobService(
    _Job,
    JobStatusOperations,
    JobMetricOperations,
    JobResourceOperations,
    JobDependencyOperations,
    JobCheckpointOperations,
):
    """Service for managing ML training jobs with modular operations."""

    def __init__(self, table_name: str, region: str = "us-east-1"):
        """Initialize the JobService.

        Args:
            table_name: Name of the DynamoDB table
            region: AWS region
        """
        self.table_name = table_name
        self.region = region
        self._client = boto3.client("dynamodb", region_name=region)

    def create_job(
        self,
        job_name: str,
        job_description: str,
        user_id: str,
        job_config: Dict[str, Any],
        priority: str = "medium",
        estimated_duration: Optional[int] = None,
        tags: Optional[Dict[str, str]] = None,
    ) -> Job:
        """Create a new job.

        Args:
            job_name: Name of the job
            job_description: Description of the job
            user_id: ID of the user creating the job
            job_config: Configuration for the job
            priority: Job priority (low, medium, high)
            estimated_duration: Estimated duration in seconds
            tags: Optional tags for the job

        Returns:
            The created Job
        """
        job = Job(
            job_id=str(uuid.uuid4()),
            name=job_name,
            description=job_description,
            created_at=datetime.now(),
            created_by=user_id,
            status="pending",
            priority=priority,
            job_config=job_config,
            estimated_duration=estimated_duration,
            tags=tags or {},
        )

        # Add the job to DynamoDB
        self.add_job(job)

        # Add initial status
        self.add_job_status(job.job_id, "pending", "Job created")

        return job

    def get_job(self, job_id: str) -> Job:
        """Get a job by ID."""
        item = super().get_job(job_id)
        return item_to_job(item)

    def get_job_with_status(self, job_id: str) -> Tuple[Job, List[JobStatus]]:
        """Get a job with its status history."""
        job = self.get_job(job_id)
        status_history = self.get_job_status_history(job_id)
        return job, status_history

    def update_job(self, job: Job) -> None:
        """Update a job."""
        super().update_job(job)

    def delete_job(self, job: Job) -> None:
        """Delete a job."""
        super().delete_job(job)

    def list_jobs(
        self, limit: int = 100, last_evaluated_key: Dict[str, Any] = None
    ) -> Tuple[List[Job], Optional[Dict[str, Any]]]:
        """List all jobs."""
        items, lek = super().list_jobs(limit, last_evaluated_key)
        jobs = [item_to_job(item) for item in items]
        return jobs, lek

    def list_jobs_by_status(
        self,
        status: str,
        limit: int = 100,
        last_evaluated_key: Dict[str, Any] = None,
    ) -> Tuple[List[Job], Optional[Dict[str, Any]]]:
        """List jobs by status."""
        items, lek = super().list_jobs_by_status(
            status, limit, last_evaluated_key
        )
        jobs = [item_to_job(item) for item in items]
        return jobs, lek

    def list_jobs_by_user(
        self,
        user_id: str,
        limit: int = 100,
        last_evaluated_key: Dict[str, Any] = None,
    ) -> Tuple[List[Job], Optional[Dict[str, Any]]]:
        """List jobs by user."""
        items, lek = super().list_jobs_by_user(
            user_id, limit, last_evaluated_key
        )
        jobs = [item_to_job(item) for item in items]
        return jobs, lek

    def check_job_dependencies(
        self, job_id: str
    ) -> Tuple[bool, List[Dict[str, Any]]]:
        """Check if all dependencies for a job are satisfied.

        This is a convenience method that uses the dependency operations
        with the current service's get_job method.
        """
        return self.check_dependencies_satisfied(job_id, self.get_job)
