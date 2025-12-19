from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Generator, Optional, Tuple

from receipt_dynamo.entities.util import _repr_str, assert_valid_uuid


@dataclass(eq=True, unsafe_hash=False)
class JobStatus:
    """
    Represents a status update for a job stored in a DynamoDB table.

    This class encapsulates job status information such as the current
    status, progress, message, and update timestamp. It is designed to support
    operations
    such as generating DynamoDB keys and converting job status data to a
    DynamoDB-compatible item.

    Attributes:
        job_id (str): UUID identifying the job.
        status (str): The status of the job (pending, running, succeeded,
            failed, cancelled).
        updated_at (datetime): The timestamp when the status was updated.
        progress (float): The progress of the job as a percentage (0-100).
        message (str): A message describing the status update.
        updated_by (str): The user or system that updated the status.
        instance_id (str): The ID of the instance that updated the status.
    """

    job_id: str
    status: str
    updated_at: str
    progress: Optional[float] = None
    message: Optional[str] = None
    updated_by: Optional[str] = None
    instance_id: Optional[str] = None

    def __post_init__(self):
        """Validates fields after dataclass initialization.

        Raises:
            ValueError: If any parameter is of an invalid type or has an
                invalid value.
        """
        assert_valid_uuid(self.job_id)

        valid_statuses = [
            "pending",
            "running",
            "succeeded",
            "failed",
            "cancelled",
            "interrupted",
        ]
        if (
            not isinstance(self.status, str)
            or self.status.lower() not in valid_statuses
        ):
            raise ValueError(f"status must be one of {valid_statuses}")
        self.status = self.status.lower()

        # Handle updated_at conversion
        if isinstance(self.updated_at, datetime):
            self.updated_at = self.updated_at.isoformat()
        elif not isinstance(self.updated_at, str):
            raise ValueError(
                "updated_at must be a datetime object or a string"
            )

        if self.progress is not None:
            if (
                not isinstance(self.progress, (int, float))
                or self.progress < 0
                or self.progress > 100
            ):
                raise ValueError("progress must be a number between 0 and 100")
            self.progress = float(self.progress)

        if self.message is not None and not isinstance(self.message, str):
            raise ValueError("message must be a string")

        if self.updated_by is not None and not isinstance(
            self.updated_by, str
        ):
            raise ValueError("updated_by must be a string")

        if self.instance_id is not None and not isinstance(
            self.instance_id, str
        ):
            raise ValueError("instance_id must be a string")

    @property
    def key(self) -> Dict[str, Any]:
        """Generates the primary key for the job status.

        Returns:
            dict: The primary key for the job status.
        """
        return {
            "PK": {"S": f"JOB#{self.job_id}"},
            "SK": {"S": f"STATUS#{self.updated_at}"},
        }

    def gsi1_key(self) -> Dict[str, Any]:
        """Generates the GSI1 key for the job status.

        Returns:
            dict: The GSI1 key for the job status.
        """
        return {
            "GSI1PK": {"S": f"STATUS#{self.status}"},
            "GSI1SK": {"S": f"UPDATED#{self.updated_at}"},
        }

    def to_item(self) -> Dict[str, Any]:
        """Converts the JobStatus object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the JobStatus object as a
                DynamoDB item.
        """
        item = {
            **self.key,
            **self.gsi1_key(),
            "TYPE": {"S": "JOB_STATUS"},
            "status": {"S": self.status},
            "updated_at": {"S": self.updated_at},
        }

        if self.progress is not None:
            item["progress"] = {"N": str(self.progress)}

        if self.message:
            item["message"] = {"S": self.message}

        if self.updated_by:
            item["updated_by"] = {"S": self.updated_by}

        if self.instance_id:
            item["instance_id"] = {"S": self.instance_id}

        return item

    def __repr__(self) -> str:
        """Returns a string representation of the JobStatus object.

        Returns:
            str: A string representation of the JobStatus object.
        """
        return (
            "JobStatus("
            f"job_id={_repr_str(self.job_id)}, "
            f"status={_repr_str(self.status)}, "
            f"updated_at={_repr_str(self.updated_at)}, "
            f"progress={self.progress}, "
            f"message={_repr_str(self.message)}, "
            f"updated_by={_repr_str(self.updated_by)}, "
            f"instance_id={_repr_str(self.instance_id)}"
            ")"
        )

    def __iter__(self) -> Generator[Tuple[str, Any], None, None]:
        """Returns an iterator over the JobStatus object's attributes.

        Returns:
            Generator[Tuple[str, Any], None, None]: An iterator over the
                JobStatus object's attribute name/value pairs.
        """
        yield "job_id", self.job_id
        yield "status", self.status
        yield "updated_at", self.updated_at
        yield "progress", self.progress
        yield "message", self.message
        yield "updated_by", self.updated_by
        yield "instance_id", self.instance_id

    def __hash__(self) -> int:
        """Returns the hash value of the JobStatus object.

        Returns:
            int: The hash value of the JobStatus object.
        """
        return hash(
            (
                self.job_id,
                self.status,
                self.updated_at,
                self.progress,
                self.message,
                self.updated_by,
                self.instance_id,
            )
        )


def item_to_job_status(item: Dict[str, Any]) -> JobStatus:
    """Converts a DynamoDB item to a JobStatus object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        JobStatus: The JobStatus object represented by the DynamoDB item.

    Raises:
        ValueError: When the item format is invalid.
    """
    required_keys = {
        "PK",
        "SK",
        "TYPE",
        "status",
        "updated_at",
    }
    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - item.keys()
        additional_keys = item.keys() - required_keys
        raise ValueError(
            f"Invalid item format\nmissing keys: {missing_keys}\n"
            f"additional keys: {additional_keys}"
        )

    try:
        # Parse job_id from the PK
        job_id = item["PK"]["S"].split("#")[1]

        # Extract basic fields
        status = item["status"]["S"]
        updated_at = item["updated_at"]["S"]

        # Parse optional fields
        progress = float(item["progress"]["N"]) if "progress" in item else None
        message = item["message"]["S"] if "message" in item else None
        updated_by = item["updated_by"]["S"] if "updated_by" in item else None
        instance_id = (
            item["instance_id"]["S"] if "instance_id" in item else None
        )

        return JobStatus(
            job_id=job_id,
            status=status,
            updated_at=updated_at,
            progress=progress,
            message=message,
            updated_by=updated_by,
            instance_id=instance_id,
        )
    except KeyError as e:
        raise ValueError(f"Error converting item to JobStatus: {e}") from e
