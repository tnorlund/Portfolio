"""
Job class for ML training job definition.
"""

import dataclasses
import enum
import json
import time
import uuid
from typing import Any, Dict, List, Optional, Union


class JobStatus(enum.Enum):
    """Status of a training job."""

    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    INTERRUPTED = (
        "interrupted"  # For jobs interrupted by spot instance termination
    )


class JobPriority(enum.Enum):
    """Priority level for a job."""

    LOW = "low"  # Non-urgent jobs
    MEDIUM = "medium"  # Default priority
    HIGH = "high"  # Important jobs
    CRITICAL = "critical"  # Jobs that must be processed immediately


@dataclasses.dataclass
class Job:
    """
    Represents a training job to be processed by the training infrastructure.

    This class defines the job properties and provides methods to serialize/deserialize
    the job for storage in SQS.
    """

    # Required job fields
    name: str
    type: str  # Type of job (e.g., "training", "evaluation", "hyperparameter_search")
    config: Dict[
        str, Any
    ]  # Configuration for the job (includes model config, training params, etc.)

    # Optional job fields
    job_id: str = dataclasses.field(default_factory=lambda: str(uuid.uuid4()))
    status: JobStatus = JobStatus.PENDING
    priority: JobPriority = JobPriority.MEDIUM
    created_at: float = dataclasses.field(default_factory=time.time)
    started_at: Optional[float] = None
    completed_at: Optional[float] = None
    attempt_count: int = 0
    max_attempts: int = 3
    error_message: Optional[str] = None
    tags: Dict[str, str] = dataclasses.field(default_factory=dict)
    dependencies: List[str] = dataclasses.field(
        default_factory=list
    )  # List of job IDs this job depends on
    timeout_seconds: Optional[int] = (
        None  # Maximum runtime in seconds before job is considered failed
    )

    def __post_init__(self):
        """Post-initialization processing to handle job_type compatibility."""
        # No additional processing needed at this point, but this hook is useful for future extensions
        pass

    @property
    def job_type(self) -> str:
        """
        Getter for job_type (alias for 'type' field).

        This property exists for backward compatibility with code that expects a job_type attribute.
        New code should use the 'type' field directly.
        """
        return self.type

    @job_type.setter
    def job_type(self, value: str) -> None:
        """
        Setter for job_type (sets the 'type' field).

        This property exists for backward compatibility with code that sets a job_type attribute.
        New code should set the 'type' field directly.
        """
        self.type = value

    def to_dict(self) -> Dict[str, Any]:
        """Convert job to dictionary representation."""
        job_dict = dataclasses.asdict(self)

        # Convert enum values to strings
        job_dict["status"] = self.status.value
        job_dict["priority"] = self.priority.value

        # Add job_type for backward compatibility
        job_dict["job_type"] = self.type

        return job_dict

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Job":
        """Create job from dictionary representation."""
        # Convert string values back to enums
        if "status" in data and isinstance(data["status"], str):
            data["status"] = JobStatus(data["status"])

        if "priority" in data and isinstance(data["priority"], str):
            data["priority"] = JobPriority(data["priority"])

        # Handle job_type for backward compatibility
        if "job_type" in data and "type" not in data:
            data["type"] = data.pop("job_type")

        return cls(**data)

    def to_json(self) -> str:
        """Serialize job to JSON string."""
        return json.dumps(self.to_dict())

    @classmethod
    def from_json(cls, json_data: str) -> "Job":
        """Deserialize job from JSON string."""
        data = json.loads(json_data)
        return cls.from_dict(data)

    def get_sqs_message_attributes(
        self,
    ) -> Dict[str, Dict[str, Union[str, Dict[str, str]]]]:
        """
        Get SQS message attributes for the job.

        These attributes are used for message filtering and prioritization in SQS.
        """
        return {
            "JobId": {"DataType": "String", "StringValue": self.job_id},
            "JobType": {"DataType": "String", "StringValue": self.type},
            "Priority": {
                "DataType": "String",
                "StringValue": self.priority.value,
            },
            "Status": {"DataType": "String", "StringValue": self.status.value},
            "AttemptCount": {
                "DataType": "Number",
                "StringValue": str(self.attempt_count),
            },
        }

    def get_sqs_message_group_id(self) -> str:
        """
        Get SQS message group ID for the job.

        For FIFO queues, messages in the same group are processed in order.
        We use priority as the group to ensure higher priority jobs are processed first.
        """
        return f"priority.{self.priority.value}"

    def get_sqs_deduplication_id(self) -> str:
        """
        Get SQS deduplication ID for the job.

        For FIFO queues, this ensures the same job is not added multiple times.
        We use job_id and attempt_count to ensure retries can still occur.
        """
        return f"{self.job_id}.{self.attempt_count}"

    def mark_started(self) -> None:
        """Mark job as started."""
        self.status = JobStatus.RUNNING
        self.started_at = time.time()

    def mark_completed(
        self, success: bool, error_message: Optional[str] = None
    ) -> None:
        """Mark job as completed (success or failure)."""
        self.status = JobStatus.SUCCEEDED if success else JobStatus.FAILED
        self.completed_at = time.time()
        if error_message:
            self.error_message = error_message

    def mark_interrupted(self) -> None:
        """Mark job as interrupted (e.g., by spot instance termination)."""
        self.status = JobStatus.INTERRUPTED
        self.completed_at = time.time()

    def increment_attempt(self) -> None:
        """Increment attempt count."""
        self.attempt_count += 1

    def can_retry(self) -> bool:
        """Check if job can be retried."""
        return (
            self.status in (JobStatus.FAILED, JobStatus.INTERRUPTED)
            and self.attempt_count < self.max_attempts
        )

    def is_completed(self) -> bool:
        """Check if job is completed (success or failure)."""
        return self.status in (
            JobStatus.SUCCEEDED,
            JobStatus.FAILED,
            JobStatus.CANCELLED,
        )

    def get_duration_seconds(self) -> Optional[float]:
        """Get job duration in seconds."""
        if self.started_at is None:
            return None

        end_time = self.completed_at or time.time()
        return end_time - self.started_at

    def is_timed_out(self) -> bool:
        """Check if job has timed out."""
        if self.timeout_seconds is None or self.started_at is None:
            return False

        return (time.time() - self.started_at) > self.timeout_seconds
