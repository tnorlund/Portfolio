"""Job resource operations."""

import uuid
from datetime import datetime
from typing import Dict, List

from receipt_dynamo import JobResource, item_to_job_resource
from receipt_dynamo.data._job_resource import _JobResource


class JobResourceOperations(_JobResource):
    """Handles job resource-related operations."""

    def add_job_resource(
        self,
        job_id: str,
        resource_type: str,
        gpu_count: int = 0,
        instance_id: str = None,
        instance_type: str = None,
    ) -> JobResource:
        """Add a resource allocation for a job.

        Args:
            job_id: The job ID
            resource_type: Type of resource (e.g., "GPU", "CPU")
            gpu_count: Number of GPUs allocated
            instance_id: ID of the instance providing resources
            instance_type: Type of instance

        Returns:
            The created JobResource
        """
        resource = JobResource(
            job_id=job_id,
            resource_id=str(uuid.uuid4()),
            instance_id=instance_id or "unknown",
            instance_type=instance_type or "unknown",
            resource_type=resource_type,
            allocated_at=datetime.now(),
            status="allocated",
            gpu_count=gpu_count,
        )
        super().add_job_resource(resource)
        return resource

    def get_job_resources(self, job_id: str) -> List[JobResource]:
        """Get all resources allocated to a job."""
        items = super().get_job_resources(job_id)
        return [item_to_job_resource(item) for item in items]
