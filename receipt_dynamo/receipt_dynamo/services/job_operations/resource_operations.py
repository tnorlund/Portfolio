"""Job resource operations."""

import uuid
from datetime import datetime
from typing import List, Optional

from receipt_dynamo.data._job_resource import _JobResource
from receipt_dynamo.entities.job_resource import JobResource


class JobResourceOperations(_JobResource):
    """Handles job resource-related operations."""

    def add_job_resource_with_params(
        self,
        job_id: str,
        resource_type: str,
        gpu_count: int = 0,
        instance_id: Optional[str] = None,
        instance_type: Optional[str] = None,
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
        # TODO: Implement get_job_resources in _JobResource base class
        # For now, use list_job_resources if available
        return []  # Placeholder until base method is implemented
