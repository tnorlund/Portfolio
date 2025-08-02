"""
Instance Service layer for receipt_dynamo operations.

This module provides a service class that encapsulates all DynamoDB operations
related to instances and provides a clean API for client applications to use.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from receipt_dynamo.data.dynamo_client import DynamoClient
from receipt_dynamo.entities.instance import Instance
from receipt_dynamo.entities.instance_job import InstanceJob


class InstanceService:
    """
    Service layer for instance-related operations.

    This class encapsulates all interactions with DynamoDB for instance
    entities
    and provides a clean API for client applications.
    """

    def __init__(self, table_name: str, region: str = "us-east-1"):
        """
        Initialize the InstanceService.

        Args:
            table_name: The name of the DynamoDB table
            region: AWS region (defaults to us-east-1)
        """
        self.dynamo_client = DynamoClient(table_name=table_name, region=region)
        self.table_name = table_name
        self.region = region

    # Instance operations
    def register_instance(
        self,
        instance_id: str,
        instance_type: str,
        region: str,
        availability_zone: str,
        hostname: str,
        ip_address: str,
        status: str = "running",
        metadata: Optional[Dict[str, Any]] = None,
        tags: Optional[Dict[str, str]] = None,
    ) -> Instance:
        """
        Register a new instance in DynamoDB.

        Args:
            instance_id: UUID identifying the instance
            instance_type: The EC2 instance type
            region: The AWS region
            availability_zone: The AWS availability zone
            hostname: The hostname of the instance
            ip_address: The IP address of the instance
            status: The initial status of the instance
            metadata: Additional metadata for the instance
            tags: Tags associated with the instance

        Returns:
            The created Instance object

        Raises:
            ValueError: When an instance with the same ID already exists
        """
        instance = Instance(
            instance_id=instance_id,
            instance_type=instance_type,
            gpu_count=0,  # TODO: Get actual GPU count based on instance type
            status=status,
            launched_at=datetime.now(),  # TODO: Get actual launch time
            ip_address=ip_address,
            availability_zone=availability_zone,
            is_spot=False,  # TODO: Determine if this is a spot instance
            health_status="healthy",  # TODO: Get actual health status
        )

        self.dynamo_client.add_instance(instance)
        return instance

    def get_instance(self, instance_id: str) -> Instance:
        """
        Get an instance by its ID.

        Args:
            instance_id: The ID of the instance to retrieve

        Returns:
            The Instance object

        Raises:
            Exception: When the instance is not found
        """
        return self.dynamo_client.get_instance(instance_id)

    def update_instance(self, instance: Instance) -> None:
        """
        Update an existing instance.

        Args:
            instance: The instance object to update

        Raises:
            Exception: When the instance does not exist
        """
        self.dynamo_client.update_instance(instance)

    def update_instance_status(
        self, instance_id: str, status: str
    ) -> Instance:
        """
        Update the status of an instance.

        Args:
            instance_id: The ID of the instance
            status: The new status value

        Returns:
            The updated Instance object

        Raises:
            Exception: When the instance does not exist
        """
        instance = self.get_instance(instance_id)
        instance.status = status
        # TODO: Instance entity doesn't have last_seen attribute
        # instance.last_seen = datetime.now()
        self.update_instance(instance)
        return instance

    def update_instance_heartbeat(self, instance_id: str) -> Instance:
        """
        Update the last_seen timestamp of an instance.

        Args:
            instance_id: The ID of the instance

        Returns:
            The updated Instance object

        Raises:
            Exception: When the instance does not exist
        """
        instance = self.get_instance(instance_id)
        # TODO: Instance entity doesn't have last_seen attribute
        # instance.last_seen = datetime.now()
        self.update_instance(instance)
        return instance

    def delete_instance(self, instance: Instance) -> None:
        """
        Delete an instance.

        Args:
            instance: The instance to delete

        Raises:
            Exception: When the instance does not exist
        """
        self.dynamo_client.delete_instance(instance)

    def list_instances(
        self,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict] = None,
    ) -> Tuple[List[Instance], Optional[Dict]]:
        """
        List instances with pagination support.

        Args:
            limit: Maximum number of instances to return
            last_evaluated_key: The key to continue from (for pagination)

        Returns:
            A tuple containing a list of Instance objects and the last
            evaluated key
        """
        return self.dynamo_client.list_instances(limit, last_evaluated_key)

    def list_instances_by_status(
        self,
        status: str,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict] = None,
    ) -> Tuple[List[Instance], Optional[Dict]]:
        """
        List instances with a specific status.

        Args:
            status: The status to filter by
            limit: Maximum number of instances to return
            last_evaluated_key: The key to continue from (for pagination)

        Returns:
            A tuple containing a list of Instance objects and the last
            evaluated key
        """
        return self.dynamo_client.list_instances_by_status(
            status, limit, last_evaluated_key
        )

    def list_instances_by_type(
        self,
        instance_type: str,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict] = None,
    ) -> Tuple[List[Instance], Optional[Dict]]:
        """
        List instances of a specific type.

        Args:
            instance_type: The instance type to filter by
            limit: Maximum number of instances to return
            last_evaluated_key: The key to continue from (for pagination)

        Returns:
            A tuple containing a list of Instance objects and the last
            evaluated key
        """
        # TODO: list_instances_by_type is not implemented in data layer
        # return self.dynamo_client.list_instances_by_type(
        #     instance_type, limit, last_evaluated_key
        # )
        raise NotImplementedError(
            "list_instances_by_type is not implemented in the data layer"
        )

    def find_idle_instances(
        self, limit: Optional[int] = None
    ) -> List[Instance]:
        """
        Find instances that are idle (running but not processing jobs).

        Args:
            limit: Maximum number of instances to return

        Returns:
            A list of idle Instance objects
        """
        # Get running instances
        running_instances, _ = self.list_instances_by_status("running", limit)

        # Filter out instances with active jobs
        idle_instances = []
        for instance in running_instances:
            # Get active jobs for this instance
            active_jobs, _ = self.dynamo_client.list_instance_jobs(
                instance.instance_id
            )

            # If there are no active jobs, the instance is idle
            if not active_jobs:
                idle_instances.append(instance)

            # If we've reached the limit, stop
            if limit and len(idle_instances) >= limit:
                break

        return idle_instances

    # Instance job operations
    def assign_job_to_instance(
        self,
        instance_id: str,
        job_id: str,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> InstanceJob:
        """
        Assign a job to an instance.

        Args:
            instance_id: The ID of the instance
            job_id: The ID of the job
            metadata: Additional metadata for the assignment

        Returns:
            The created InstanceJob object

        Raises:
            ValueError: When the job is already assigned to the instance
        """
        instance_job = InstanceJob(
            instance_id=instance_id,
            job_id=job_id,
            assigned_at=datetime.now(),
            status="assigned",
            # resource_utilization is optional, defaults to None
        )

        self.dynamo_client.add_instance_job(instance_job)
        return instance_job

    def get_instance_job(self, instance_id: str, job_id: str) -> InstanceJob:
        """
        Get a job assignment for an instance.

        Args:
            instance_id: The ID of the instance
            job_id: The ID of the job

        Returns:
            The InstanceJob object

        Raises:
            Exception: When the instance job is not found
        """
        return self.dynamo_client.get_instance_job(instance_id, job_id)

    def update_instance_job(self, instance_job: InstanceJob) -> None:
        """
        Update a job assignment for an instance.

        Args:
            instance_job: The instance job to update

        Raises:
            Exception: When the instance job does not exist
        """
        self.dynamo_client.update_instance_job(instance_job)

    def update_instance_job_status(
        self,
        instance_id: str,
        job_id: str,
        status: str,
        message: Optional[str] = None,
    ) -> InstanceJob:
        """
        Update the status of a job assignment.

        Args:
            instance_id: The ID of the instance
            job_id: The ID of the job
            status: The new status value
            message: Optional message about the status change

        Returns:
            The updated InstanceJob object

        Raises:
            Exception: When the instance job does not exist
        """
        instance_job = self.get_instance_job(instance_id, job_id)
        instance_job.status = status

        # TODO: InstanceJob entity doesn't have metadata attribute
        # if message:
        #     if not instance_job.metadata:
        #         instance_job.metadata = {}
        #     instance_job.metadata["last_message"] = message
        #     instance_job.metadata["last_update"] = datetime.now().isoformat()

        self.update_instance_job(instance_job)
        return instance_job

    def delete_instance_job(self, instance_job: InstanceJob) -> None:
        """
        Delete a job assignment for an instance.

        Args:
            instance_job: The instance job to delete

        Raises:
            Exception: When the instance job does not exist
        """
        self.dynamo_client.delete_instance_job(instance_job)

    def list_jobs_for_instance(
        self,
        instance_id: str,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict] = None,
    ) -> Tuple[List[InstanceJob], Optional[Dict]]:
        """
        List jobs assigned to an instance with pagination support.

        Args:
            instance_id: The ID of the instance
            status: Optional status to filter by
            limit: Maximum number of instance jobs to return
            last_evaluated_key: The key to continue from (for pagination)

        Returns:
            A tuple containing a list of InstanceJob objects and the last
            evaluated key
        """
        return self.dynamo_client.list_instance_jobs(
            instance_id, limit, last_evaluated_key
        )

    def list_instances_for_job(
        self,
        job_id: str,
        status: Optional[str] = None,
        limit: Optional[int] = None,
        last_evaluated_key: Optional[Dict] = None,
    ) -> Tuple[List[InstanceJob], Optional[Dict]]:
        """
        List instances assigned to a job with pagination support.

        Args:
            job_id: The ID of the job
            status: Optional status to filter by
            limit: Maximum number of instance jobs to return
            last_evaluated_key: The key to continue from (for pagination)

        Returns:
            A tuple containing a list of InstanceJob objects and the last
            evaluated key
        """
        return self.dynamo_client.list_instances_for_job(
            job_id, limit, last_evaluated_key
        )
