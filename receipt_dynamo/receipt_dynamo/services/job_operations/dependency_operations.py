"""Job dependency operations."""

from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from receipt_dynamo.data._job_dependency import _JobDependency
from receipt_dynamo.entities.job import Job
from receipt_dynamo.entities.job_dependency import JobDependency


class JobDependencyOperations(_JobDependency):
    """Handles job dependency-related operations."""

    def add_job_dependency_with_params(
        self,
        job_id: str,
        dependency_job_id: str,
        type: str = "COMPLETION",
        condition: Optional[str] = None,
    ) -> JobDependency:
        """Add a dependency between jobs.

        Args:
            job_id: The job that depends on another
            dependency_job_id: The job that must complete first
            type: Type of dependency (COMPLETION, SUCCESS, FAILURE, ARTIFACT)
            condition: Optional condition for the dependency

        Returns:
            The created JobDependency
        """
        dependency = JobDependency(
            dependent_job_id=job_id,
            dependency_job_id=dependency_job_id,
            type=type,
            created_at=datetime.now(),
            condition=condition,
        )
        super().add_job_dependency(dependency)
        return dependency

    def get_job_dependencies(self, job_id: str) -> List[JobDependency]:
        """Get all dependencies for a job."""
        # TODO: Implement get_job_dependencies in _JobDependency base class
        return []

    def get_dependent_jobs(self, job_id: str) -> List[JobDependency]:
        """Get all jobs that depend on this job."""
        # TODO: Implement get_dependent_jobs in _JobDependency base class
        return []

    def check_dependencies_satisfied(
        self, job_id: str, get_job_func
    ) -> Tuple[bool, List[Dict[str, Any]]]:
        """Check if all dependencies for a job are satisfied.

        Args:
            job_id: The ID of the job to check dependencies for
            get_job_func: Function to get a job by ID

        Returns:
            A tuple containing:
            - boolean indicating if all dependencies are satisfied
            - list of unsatisfied dependencies with details
        """
        dependencies = self.get_job_dependencies(job_id)

        if not dependencies:
            return True, []

        unsatisfied = []
        all_satisfied = True

        for dependency in dependencies:
            is_satisfied = False
            dependency_details: Dict[str, Any] = {
                "dependency_job_id": dependency.dependency_job_id,
                "type": dependency.type,
                "condition": dependency.condition,
            }

            try:
                # Get the dependency job status
                dependency_job = get_job_func(dependency.dependency_job_id)
                dependency_details["current_status"] = dependency_job.status

                # Check if the dependency is satisfied based on type
                if dependency.type == "COMPLETION":
                    is_satisfied = dependency_job.status in [
                        "succeeded",
                        "failed",
                        "cancelled",
                    ]
                elif dependency.type == "SUCCESS":
                    is_satisfied = dependency_job.status == "succeeded"
                elif dependency.type == "FAILURE":
                    is_satisfied = dependency_job.status == "failed"
                elif dependency.type == "ARTIFACT":
                    # Special condition for artifact dependency
                    if dependency.condition and hasattr(dependency_job, "job_config"):
                        # Check if the artifact exists
                        is_satisfied = self._check_artifact_exists(
                            dependency_job, dependency.condition
                        )
                    else:
                        is_satisfied = False

            except ValueError:
                dependency_details["error"] = "Job not found"

            dependency_details["is_satisfied"] = is_satisfied

            if not is_satisfied:
                all_satisfied = False
                unsatisfied.append(dependency_details)

        return all_satisfied, unsatisfied

    def _check_artifact_exists(self, job: Job, artifact_path: str) -> bool:
        """Check if a job artifact exists.

        Args:
            job: The job to check artifacts for
            artifact_path: The path to the artifact

        Returns:
            Boolean indicating if the artifact exists
        """
        # TODO: Implement actual artifact checking logic
        # This would depend on your artifact storage system (S3, etc.)
        return True
