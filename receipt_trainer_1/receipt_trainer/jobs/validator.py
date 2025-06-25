"""
Job dependency validator for ML training jobs.

This module provides functions to validate job dependencies,
check for circular dependencies, and ensure dependency integrity.
"""

from typing import Dict, List, Set, Any, Optional, Tuple
import logging
from queue import Queue

from receipt_dynamo.services.job_service import JobService
from receipt_dynamo.entities.job_dependency import JobDependency


logger = logging.getLogger(__name__)


def validate_job_dependency(
    dependent_job_id: str,
    dependency_job_id: str,
    dependency_type: str,
    condition: Optional[str] = None,
    job_service: Optional[JobService] = None,
) -> Tuple[bool, Optional[str]]:
    """
    Validate a job dependency.

    Args:
        dependent_job_id: ID of the job that depends on another
        dependency_job_id: ID of the job being depended on
        dependency_type: Type of dependency (COMPLETION, SUCCESS, FAILURE, ARTIFACT)
        condition: Optional condition for the dependency
        job_service: JobService instance for checking if jobs exist

    Returns:
        Tuple containing:
        - Boolean indicating if dependency is valid
        - Error message if invalid, None otherwise
    """
    # Check for simple cases first
    if dependent_job_id == dependency_job_id:
        return False, "A job cannot depend on itself"

    valid_types = ["COMPLETION", "SUCCESS", "FAILURE", "ARTIFACT"]
    if dependency_type.upper() not in valid_types:
        return False, f"Dependency type must be one of {valid_types}"

    # If ARTIFACT type is specified, condition is required
    if dependency_type.upper() == "ARTIFACT" and not condition:
        return False, "Condition is required for ARTIFACT dependency type"

    # If job service is provided, check if jobs exist
    if job_service:
        try:
            # Check if dependent job exists
            job_service.get_job(dependent_job_id)
        except ValueError:
            return False, f"Dependent job {dependent_job_id} does not exist"

        try:
            # Check if dependency job exists
            job_service.get_job(dependency_job_id)
        except ValueError:
            return False, f"Dependency job {dependency_job_id} does not exist"

    return True, None


def detect_circular_dependencies(
    job_id: str, job_service: JobService
) -> Tuple[bool, Optional[List[str]]]:
    """
    Detect circular dependencies for a job.

    Uses a breadth-first search algorithm to detect cycles in the dependency graph.

    Args:
        job_id: ID of the job to check for circular dependencies
        job_service: JobService instance for retrieving dependencies

    Returns:
        Tuple containing:
        - Boolean indicating if circular dependencies exist (True if circular)
        - List of job IDs forming the circular dependency if found, None otherwise
    """
    # Map each job to its dependencies
    job_to_deps = {}

    # Queue for BFS
    queue = Queue()
    queue.put((job_id, [job_id]))

    while not queue.empty():
        current_job, path = queue.get()

        # Get dependencies for current job if not already fetched
        if current_job not in job_to_deps:
            try:
                dependencies = job_service.get_job_dependencies(current_job)
                job_to_deps[current_job] = [
                    dep.dependency_job_id for dep in dependencies
                ]
            except Exception as e:
                logger.warning(
                    f"Error getting dependencies for job {current_job}: {str(e)}"
                )
                job_to_deps[current_job] = []

        # Check each dependency
        for dep_job_id in job_to_deps[current_job]:
            # If dependency is already in path, we found a cycle
            if dep_job_id in path:
                # Extract the cycle
                cycle_start = path.index(dep_job_id)
                cycle = path[cycle_start:] + [dep_job_id]
                return True, cycle

            # Otherwise, add to queue with updated path
            new_path = path + [dep_job_id]
            queue.put((dep_job_id, new_path))

    # No circular dependencies found
    return False, None


def validate_dependencies_for_job(
    job_id: str, job_service: JobService
) -> Tuple[bool, List[Dict]]:
    """
    Validate all dependencies for a job.

    Args:
        job_id: ID of the job to validate dependencies for
        job_service: JobService instance for retrieving dependencies

    Returns:
        Tuple containing:
        - Boolean indicating if all dependencies are valid
        - List of validation issues
    """
    issues = []

    # Check for circular dependencies
    has_circular, cycle = detect_circular_dependencies(job_id, job_service)
    if has_circular:
        issues.append(
            {
                "type": "circular_dependency",
                "message": f"Circular dependency detected: {' -> '.join(cycle)}",
                "job_ids": cycle,
            }
        )

    # Get direct dependencies
    try:
        dependencies = job_service.get_job_dependencies(job_id)

        # Validate each dependency
        for dependency in dependencies:
            is_valid, error_message = validate_job_dependency(
                dependent_job_id=job_id,
                dependency_job_id=dependency.dependency_job_id,
                dependency_type=dependency.type,
                condition=dependency.condition,
                job_service=job_service,
            )

            if not is_valid:
                issues.append(
                    {
                        "type": "invalid_dependency",
                        "dependency_job_id": dependency.dependency_job_id,
                        "message": error_message,
                    }
                )

    except Exception as e:
        issues.append(
            {"type": "error", "message": f"Error validating dependencies: {str(e)}"}
        )

    return len(issues) == 0, issues
