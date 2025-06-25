"""
Job Service layer for receipt_dynamo operations.

This module provides a service class that encapsulates all DynamoDB operations
related to jobs and provides a clean API for client applications to use.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities.job import Job, itemToJob
from receipt_dynamo.entities.job_checkpoint import JobCheckpoint
from receipt_dynamo.entities.job_dependency import JobDependency
from receipt_dynamo.entities.job_log import JobLog
from receipt_dynamo.entities.job_metric import JobMetric
from receipt_dynamo.entities.job_resource import JobResource
from receipt_dynamo.entities.job_status import JobStatus, itemToJobStatus


class JobService:
    """
    Service layer for job-related operations.

    This class encapsulates all interactions with DynamoDB for job entities
    and provides a clean API for client applications.
    """

    def __init__(self, table_name: str, region: str = "us-east-1"):
        """
        Initialize the JobService.

        Args:
            table_name: The name of the DynamoDB table
            region: AWS region (defaults to us-east-1)
        """
        self.dynamo_client = DynamoClient(table_name=table_name, region=region)
        self.table_name = table_name
        self.region = region

    # Job CRUD operations
    def create_job(
        self,
        job_id: str,
        name: str,
        description: str,
        created_by: str,
        status: str,
        priority: str,
        job_config: Dict[str, Any],
        estimated_duration: Optional[int] = None,
        tags: Optional[Dict[str, str]] = None,
    ) -> Job:
        """
        Create a new job in DynamoDB.

        Args:
            job_id: UUID identifying the job
            name: The name of the job
            description: A description of the job
            created_by: The user who created the job
            status: The initial status of the job
            priority: The priority level of the job
            job_config: The configuration for the job
            estimated_duration: The estimated duration of the job in seconds
            tags: Tags associated with the job

        Returns:
            The created Job object

        Raises:
            ValueError: When a job with the same ID already exists
        """
        job = Job(
            job_id=job_id,
            name=name,
            description=description,
            created_at=datetime.now(),
            created_by=created_by,
            status=status,
            priority=priority,
            job_config=job_config,
            estimated_duration=estimated_duration,
            tags=tags,
        )

        self.dynamo_client.addJob(job)
        return job

    def get_job(self, job_id: str) -> Job:
        """
        Get a job by its ID.

        Args:
            job_id: The ID of the job to retrieve

        Returns:
            The Job object

        Raises:
            Exception: When the job is not found
        """
        return self.dynamo_client.getJob(job_id)

    def get_job_with_status(self, job_id: str) -> Tuple[Job, List[JobStatus]]:
        """
        Get a job and its associated status records.

        Args:
            job_id: The ID of the job to retrieve

        Returns:
            A tuple containing the Job object and a list of JobStatus objects

        Raises:
            Exception: When the job is not found
        """
        return self.dynamo_client.getJobWithStatus(job_id)

    def update_job(self, job: Job) -> None:
        """
        Update an existing job.

        Args:
            job: The job object to update

        Raises:
            Exception: When the job does not exist
        """
        self.dynamo_client.updateJob(job)

    def delete_job(self, job: Job) -> None:
        """
        Delete a job.

        Args:
            job: The job to delete

        Raises:
            Exception: When the job does not exist
        """
        self.dynamo_client.deleteJob(job)

    def list_jobs(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict] = None,
    ) -> Tuple[List[Job], Optional[Dict]]:
        """
        List jobs with pagination support.

        Args:
            limit: Maximum number of jobs to return
            last_evaluated_key: The key to continue from (for pagination)

        Returns:
            A tuple containing a list of Job objects and the last evaluated key
        """
        return self.dynamo_client.listJobs(limit, last_evaluated_key)

    def list_jobs_by_status(
        self,
        status: str,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict] = None,
    ) -> Tuple[List[Job], Optional[Dict]]:
        """
        List jobs with a specific status.

        Args:
            status: The status to filter by
            limit: Maximum number of jobs to return
            last_evaluated_key: The key to continue from (for pagination)

        Returns:
            A tuple containing a list of Job objects and the last evaluated key
        """
        return self.dynamo_client.listJobsByStatus(status, limit, last_evaluated_key)

    def list_jobs_by_user(
        self,
        user_id: str,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict] = None,
    ) -> Tuple[List[Job], Optional[Dict]]:
        """
        List jobs created by a specific user.

        Args:
            user_id: The user ID to filter by
            limit: Maximum number of jobs to return
            last_evaluated_key: The key to continue from (for pagination)

        Returns:
            A tuple containing a list of Job objects and the last evaluated key
        """
        return self.dynamo_client.listJobsByUser(user_id, limit, last_evaluated_key)

    # Job status operations
    def add_job_status(self, job_id: str, status: str, message: str) -> JobStatus:
        """
        Add a new status record for a job.

        Args:
            job_id: The ID of the job
            status: The status value
            message: Message describing the status change

        Returns:
            The created JobStatus object
        """
        job_status = JobStatus(
            job_id=job_id,
            updated_at=datetime.now(),
            status=status,
            message=message,
        )
        self.dynamo_client.addJobStatus(job_status)
        return job_status

    def get_job_status_history(self, job_id: str) -> List[JobStatus]:
        """
        Get the status history for a job.

        Args:
            job_id: The ID of the job

        Returns:
            A list of JobStatus objects
        """
        return self.dynamo_client.listJobStatusesByJob(job_id)

    # Job log operations
    def add_job_log(self, job_id: str, log_level: str, message: str) -> JobLog:
        """
        Add a log entry for a job.

        Args:
            job_id: The ID of the job
            log_level: Log level (INFO, WARNING, ERROR, etc.)
            message: The log message

        Returns:
            The created JobLog object
        """
        job_log = JobLog(
            job_id=job_id,
            timestamp=datetime.now(),
            log_level=log_level,
            message=message,
        )
        self.dynamo_client.addJobLog(job_log)
        return job_log

    def get_job_logs(self, job_id: str) -> List[JobLog]:
        """
        Get log entries for a job.

        Args:
            job_id: The ID of the job

        Returns:
            A list of JobLog objects
        """
        return self.dynamo_client.listJobLogsByJob(job_id)

    # Job metric operations
    def add_job_metric(
        self,
        job_id: str,
        metric_name: str,
        metric_value: float,
        metadata: Optional[Dict] = None,
    ) -> JobMetric:
        """
        Add a metric for a job.

        Args:
            job_id: The ID of the job
            metric_name: The name of the metric
            metric_value: The value of the metric
            metadata: Additional metadata for the metric

        Returns:
            The created JobMetric object
        """
        job_metric = JobMetric(
            job_id=job_id,
            timestamp=datetime.now(),
            metric_name=metric_name,
            metric_value=metric_value,
            metadata=metadata or {},
        )
        self.dynamo_client.addJobMetric(job_metric)
        return job_metric

    def get_job_metrics(self, job_id: str) -> List[JobMetric]:
        """
        Get metrics for a job.

        Args:
            job_id: The ID of the job

        Returns:
            A list of JobMetric objects
        """
        return self.dynamo_client.listJobMetricsByJob(job_id)

    # Job resource operations
    def add_job_resource(
        self,
        job_id: str,
        resource_type: str,
        resource_id: str,
        metadata: Optional[Dict] = None,
    ) -> JobResource:
        """
        Add a resource association to a job.

        Args:
            job_id: The ID of the job
            resource_type: The type of resource
            resource_id: The ID of the resource
            metadata: Additional metadata for the resource

        Returns:
            The created JobResource object
        """
        job_resource = JobResource(
            job_id=job_id,
            resource_type=resource_type,
            resource_id=resource_id,
            created_at=datetime.now(),
            metadata=metadata or {},
        )
        self.dynamo_client.addJobResource(job_resource)
        return job_resource

    def get_job_resources(self, job_id: str) -> List[JobResource]:
        """
        Get resources associated with a job.

        Args:
            job_id: The ID of the job

        Returns:
            A list of JobResource objects
        """
        return self.dynamo_client.listJobResourcesByJob(job_id)

    # Job dependency operations
    def add_job_dependency(self, job_id: str, depends_on_job_id: str) -> JobDependency:
        """
        Add a dependency between jobs.

        Args:
            job_id: The ID of the dependent job
            depends_on_job_id: The ID of the job it depends on

        Returns:
            The created JobDependency object
        """
        job_dependency = JobDependency(
            job_id=job_id,
            depends_on_job_id=depends_on_job_id,
            created_at=datetime.now(),
        )
        self.dynamo_client.addJobDependency(job_dependency)
        return job_dependency

    def get_job_dependencies(self, job_id: str) -> List[JobDependency]:
        """
        Get dependencies for a job.

        Args:
            job_id: The ID of the job

        Returns:
            A list of JobDependency objects representing what this job depends on
        """
        return self.dynamo_client.listJobDependenciesByJob(job_id)

    def get_dependent_jobs(self, job_id: str) -> List[JobDependency]:
        """
        Get jobs that depend on a specific job.

        Args:
            job_id: The ID of the job

        Returns:
            A list of JobDependency objects representing jobs that depend on this job
        """
        return self.dynamo_client.listDependents(job_id)

    def check_dependencies_satisfied(self, job_id: str) -> Tuple[bool, List[Dict]]:
        """
        Check if all dependencies for a job are satisfied.

        Args:
            job_id: The ID of the job to check dependencies for

        Returns:
            A tuple containing:
            - boolean indicating if all dependencies are satisfied
            - list of unsatisfied dependencies with details
        """
        # Get all dependencies for this job
        dependencies = self.get_job_dependencies(job_id)

        if not dependencies:
            # No dependencies means all are satisfied
            return True, []

        unsatisfied = []
        all_satisfied = True

        for dependency in dependencies:
            is_satisfied = False
            dependency_details = {
                "dependency_job_id": dependency.dependency_job_id,
                "type": dependency.type,
                "condition": dependency.condition,
            }

            try:
                # Get the dependency job status
                dependency_job = self.get_job(dependency.dependency_job_id)
                dependency_details["current_status"] = dependency_job.status

                # Check if the dependency is satisfied based on type
                if dependency.type == "COMPLETION":
                    # Job completed with any status
                    is_satisfied = dependency_job.status in [
                        "succeeded",
                        "failed",
                        "cancelled",
                    ]
                elif dependency.type == "SUCCESS":
                    # Job completed successfully
                    is_satisfied = dependency_job.status == "succeeded"
                elif dependency.type == "FAILURE":
                    # Job failed
                    is_satisfied = dependency_job.status == "failed"
                elif dependency.type == "ARTIFACT":
                    # Special condition for artifact dependency
                    if dependency.condition and hasattr(dependency_job, "job_config"):
                        # Check if the artifact exists based on condition
                        # This would need to be implemented based on your artifact storage system
                        is_satisfied = self._check_artifact_exists(
                            dependency_job, dependency.condition
                        )
                    else:
                        is_satisfied = False

            except ValueError:
                # Dependency job not found
                dependency_details["error"] = "Job not found"

            dependency_details["is_satisfied"] = is_satisfied

            if not is_satisfied:
                all_satisfied = False
                unsatisfied.append(dependency_details)

        return all_satisfied, unsatisfied

    def _check_artifact_exists(self, job: Job, artifact_condition: str) -> bool:
        """
        Check if an artifact exists for a job based on a condition.

        Args:
            job: The job that should have produced the artifact
            artifact_condition: The condition specifying the artifact

        Returns:
            True if the artifact exists, False otherwise
        """
        # This is a placeholder implementation
        # You would need to implement this based on your artifact storage system
        # For example, checking in S3 for a specific file
        return False

    # Job checkpoint operations
    def add_job_checkpoint(
        self,
        job_id: str,
        checkpoint_name: str,
        metadata: Optional[Dict] = None,
    ) -> JobCheckpoint:
        """
        Add a checkpoint for a job.

        Args:
            job_id: The ID of the job
            checkpoint_name: The name of the checkpoint
            metadata: Additional metadata for the checkpoint

        Returns:
            The created JobCheckpoint object
        """
        job_checkpoint = JobCheckpoint(
            job_id=job_id,
            checkpoint_name=checkpoint_name,
            created_at=datetime.now(),
            metadata=metadata or {},
        )
        self.dynamo_client.addJobCheckpoint(job_checkpoint)
        return job_checkpoint

    def get_job_checkpoints(self, job_id: str) -> List[JobCheckpoint]:
        """
        Get checkpoints for a job.

        Args:
            job_id: The ID of the job

        Returns:
            A list of JobCheckpoint objects
        """
        return self.dynamo_client.listJobCheckpointsByJob(job_id)
