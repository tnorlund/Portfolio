from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Generator, Optional, Tuple

from receipt_dynamo.entities.dynamodb_utils import (
    dict_to_dynamodb_map,
    parse_dynamodb_map,
    parse_dynamodb_value,
    to_dynamodb_value,
)
from receipt_dynamo.entities.util import _repr_str, assert_valid_uuid


@dataclass(eq=True, unsafe_hash=False)
class InstanceJob:
    """
    Represents the association between an EC2 instance and a job in DynamoDB.

    This class encapsulates the relationship between an instance and a job,
    including when the job was assigned to the instance, its current status,
    and resource utilization metrics.

    Attributes:
        instance_id (str): UUID identifying the instance.
        job_id (str): UUID identifying the job.
        assigned_at (datetime): The timestamp when the job was assigned to the
            instance.
        status (str): The current status of the job on this instance.
        resource_utilization (Dict): Resource utilization metrics (CPU, memory,
            GPU, etc.)
    """

    instance_id: str
    job_id: str
    assigned_at: str
    status: str
    resource_utilization: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        """Validates fields after dataclass initialization.

        Raises:
            ValueError: If any parameter is of an invalid type or has an
                invalid value.
        """
        if not isinstance(self.instance_id, str) or not self.instance_id:
            raise ValueError("instance_id must be a non-empty string")

        assert_valid_uuid(self.job_id)

        # Handle assigned_at conversion
        if isinstance(self.assigned_at, datetime):
            self.assigned_at = self.assigned_at.isoformat()
        elif not isinstance(self.assigned_at, str):
            raise ValueError("assigned_at must be a datetime object or a string")

        valid_statuses = [
            "assigned",
            "running",
            "completed",
            "failed",
            "cancelled",
        ]
        if (
            not isinstance(self.status, str)
            or self.status.lower() not in valid_statuses
        ):
            raise ValueError(f"status must be one of {valid_statuses}")
        self.status = self.status.lower()

        if self.resource_utilization is not None and not isinstance(
            self.resource_utilization, dict
        ):
            raise ValueError("resource_utilization must be a dictionary")
        if self.resource_utilization is None:
            self.resource_utilization = {}

    @property
    def key(self) -> Dict[str, Any]:
        """Generates the primary key for the instance-job relationship.

        Returns:
            dict: The primary key for the instance-job relationship.
        """
        return {
            "PK": {"S": f"INSTANCE#{self.instance_id}"},
            "SK": {"S": f"JOB#{self.job_id}"},
        }

    def gsi1_key(self) -> Dict[str, Any]:
        """Generates the GSI1 key for the instance-job relationship.

        Returns:
            dict: The GSI1 key for the instance-job relationship.
        """
        return {
            "GSI1PK": {"S": "JOB"},
            "GSI1SK": {"S": f"JOB#{self.job_id}#INSTANCE#{self.instance_id}"},
        }

    def to_item(self) -> Dict[str, Any]:
        """Converts the InstanceJob object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the InstanceJob object as a
                DynamoDB item.
        """
        item = {
            **self.key,
            **self.gsi1_key(),
            "TYPE": {"S": "INSTANCE_JOB"},
            "assigned_at": {"S": self.assigned_at},
            "status": {"S": self.status},
        }

        if self.resource_utilization:
            item["resource_utilization"] = {
                "M": dict_to_dynamodb_map(self.resource_utilization)
            }

        return item

    def __repr__(self) -> str:
        """Returns a string representation of the InstanceJob object.

        Returns:
            str: A string representation of the InstanceJob object.
        """
        return (
            "InstanceJob("
            f"instance_id={_repr_str(self.instance_id)}, "
            f"job_id={_repr_str(self.job_id)}, "
            f"assigned_at={_repr_str(self.assigned_at)}, "
            f"status={_repr_str(self.status)}, "
            f"resource_utilization={self.resource_utilization}"
            ")"
        )

    def __iter__(self) -> Generator[Tuple[str, Any], None, None]:
        """Returns an iterator over the InstanceJob object's attributes.

        Returns:
            Generator[Tuple[str, Any], None, None]: An iterator over the
                InstanceJob object's attribute name/value pairs.
        """
        yield "instance_id", self.instance_id
        yield "job_id", self.job_id
        yield "assigned_at", self.assigned_at
        yield "status", self.status
        yield "resource_utilization", self.resource_utilization

    def __hash__(self) -> int:
        """Returns the hash value of the InstanceJob object.

        Returns:
            int: The hash value of the InstanceJob object.
        """
        return hash(
            (
                self.instance_id,
                self.job_id,
                self.assigned_at,
                self.status,
                # Can't hash dictionaries, so convert to tuple of sorted items
                (
                    tuple(
                        sorted(
                            (k, str(v)) for k, v in self.resource_utilization.items()
                        )
                    )
                    if self.resource_utilization
                    else None
                ),
            )
        )


def item_to_instance_job(item: Dict[str, Any]) -> InstanceJob:
    """Converts a DynamoDB item to an InstanceJob object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        InstanceJob: The InstanceJob object represented by the DynamoDB item.

    Raises:
        ValueError: When the item format is invalid.
    """
    required_keys = {
        "PK",
        "SK",
        "TYPE",
        "assigned_at",
        "status",
    }
    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - item.keys()
        additional_keys = item.keys() - required_keys
        raise ValueError(
            f"Invalid item format\nmissing keys: {missing_keys}\n"
            f"additional keys: {additional_keys}"
        )

    try:
        # Parse instance_id and job_id from the PK and SK
        instance_id = item["PK"]["S"].split("#")[1]
        job_id = item["SK"]["S"].split("#")[1]

        # Extract basic fields
        assigned_at = item["assigned_at"]["S"]
        status = item["status"]["S"]

        # Parse resource_utilization from DynamoDB map if present
        resource_utilization = None
        if "resource_utilization" in item and "M" in item["resource_utilization"]:
            resource_utilization = parse_dynamodb_map(item["resource_utilization"]["M"])

        return InstanceJob(
            instance_id=instance_id,
            job_id=job_id,
            assigned_at=assigned_at,
            status=status,
            resource_utilization=resource_utilization,
        )
    except KeyError as e:
        raise ValueError(f"Error converting item to InstanceJob: {e}") from e
