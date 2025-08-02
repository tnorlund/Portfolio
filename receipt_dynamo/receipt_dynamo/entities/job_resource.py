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
class JobResource:
    """
    Represents resources allocated to a training job stored in a DynamoDB
    table.

    This class tracks computing resources (CPU, memory, GPU, etc.) allocated
    to a specific job, the instance providing those resources, and the
    allocation lifecycle.

    Attributes:
        job_id (str): UUID identifying the job.
        resource_id (str): UUID identifying the resource allocation.
        instance_id (str): ID of the EC2 instance providing the resources.
        instance_type (str): The type of EC2 instance (e.g., p3.2xlarge).
        gpu_count (int): Number of GPUs allocated to the job.
        allocated_at (datetime): The timestamp when the resources were
            allocated.
        released_at (Optional[datetime]): The timestamp when the resources were
            released, if applicable.
        status (str): The current status of the resource allocation
            (allocated, released, failed).
        resource_type (str): The type of resource (cpu, gpu, memory, etc.).
        resource_config (Dict): Additional configuration details for the
            resource allocation.
    """

    job_id: str
    resource_id: str
    instance_id: str
    instance_type: str
    resource_type: str
    allocated_at: str
    status: str
    gpu_count: Optional[int] = None
    released_at: Optional[str] = None
    resource_config: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        """Validates fields after dataclass initialization.

        Raises:
            ValueError: If any parameter is of an invalid type or has an
                invalid value.
        """
        assert_valid_uuid(self.job_id)
        assert_valid_uuid(self.resource_id)

        if not isinstance(self.instance_id, str) or not self.instance_id:
            raise ValueError("instance_id must be a non-empty string")

        if not isinstance(self.instance_type, str) or not self.instance_type:
            raise ValueError("instance_type must be a non-empty string")

        valid_resource_types = [
            "cpu",
            "gpu",
            "memory",
            "storage",
            "network",
            "combined",
        ]
        if (
            not isinstance(self.resource_type, str)
            or self.resource_type.lower() not in valid_resource_types
        ):
            raise ValueError(
                f"resource_type must be one of {valid_resource_types}"
            )
        self.resource_type = self.resource_type.lower()

        # Handle allocated_at conversion
        if isinstance(self.allocated_at, datetime):
            self.allocated_at = self.allocated_at.isoformat()
        elif not isinstance(self.allocated_at, str):
            raise ValueError(
                "allocated_at must be a datetime object or a string"
            )

        # Handle released_at conversion
        if self.released_at is not None:
            if isinstance(self.released_at, datetime):
                self.released_at = self.released_at.isoformat()
            elif not isinstance(self.released_at, str):
                raise ValueError(
                    "released_at must be a datetime object or a string"
                )

        valid_statuses = ["allocated", "released", "failed", "pending"]
        if not isinstance(self.status, str) or self.status.lower() not in valid_statuses:
            raise ValueError(f"status must be one of {valid_statuses}")
        self.status = self.status.lower()

        if self.gpu_count is not None:
            if not isinstance(self.gpu_count, int) or self.gpu_count < 0:
                raise ValueError("gpu_count must be a non-negative integer")

        if self.resource_config is not None and not isinstance(
            self.resource_config, dict
        ):
            raise ValueError("resource_config must be a dictionary")
        if self.resource_config is None:
            self.resource_config = {}

    @property
    def key(self) -> Dict[str, Any]:
        """Generates the primary key for the job resource.

        Returns:
            dict: The primary key for the job resource.
        """
        return {
            "PK": {"S": f"JOB#{self.job_id}"},
            "SK": {"S": f"RESOURCE#{self.resource_id}"},
        }

    def gsi1_key(self) -> Dict[str, Any]:
        """Generates the GSI1 key for the job resource.

        Returns:
            dict: The GSI1 key for the job resource.
        """
        return {
            "GSI1PK": {"S": "RESOURCE"},
            "GSI1SK": {"S": f"RESOURCE#{self.resource_id}"},
        }

    def to_item(self) -> Dict[str, Any]:
        """Converts the JobResource object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the JobResource object as a
                DynamoDB item.
        """
        item = {
            **self.key,
            **self.gsi1_key(),
            "TYPE": {"S": "JOB_RESOURCE"},
            "job_id": {"S": self.job_id},
            "resource_id": {"S": self.resource_id},
            "instance_id": {"S": self.instance_id},
            "instance_type": {"S": self.instance_type},
            "resource_type": {"S": self.resource_type},
            "allocated_at": {"S": self.allocated_at},
            "status": {"S": self.status},
        }

        if self.gpu_count is not None:
            item["gpu_count"] = {"N": str(self.gpu_count)}

        if self.released_at is not None:
            item["released_at"] = {"S": self.released_at}

        if self.resource_config:
            item["resource_config"] = {
                "M": dict_to_dynamodb_map(self.resource_config)
            }

        return item

    def __repr__(self) -> str:
        """Returns a string representation of the JobResource object.

        Returns:
            str: A string representation of the JobResource object.
        """
        return (
            "JobResource("
            f"job_id={_repr_str(self.job_id)}, "
            f"resource_id={_repr_str(self.resource_id)}, "
            f"instance_id={_repr_str(self.instance_id)}, "
            f"instance_type={_repr_str(self.instance_type)}, "
            f"resource_type={_repr_str(self.resource_type)}, "
            f"allocated_at={_repr_str(self.allocated_at)}, "
            f"released_at={_repr_str(self.released_at)}, "
            f"status={_repr_str(self.status)}, "
            f"gpu_count={self.gpu_count}, "
            f"resource_config={self.resource_config}"
            ")"
        )

    def __iter__(self) -> Generator[Tuple[str, Any], None, None]:
        """Returns an iterator over the JobResource object's attributes.

        Returns:
            Generator[Tuple[str, Any], None, None]: An iterator over the
                JobResource object's attribute name/value pairs.
        """
        yield "job_id", self.job_id
        yield "resource_id", self.resource_id
        yield "instance_id", self.instance_id
        yield "instance_type", self.instance_type
        yield "resource_type", self.resource_type
        yield "allocated_at", self.allocated_at
        yield "released_at", self.released_at
        yield "status", self.status
        yield "gpu_count", self.gpu_count
        yield "resource_config", self.resource_config


    def __hash__(self) -> int:
        """Returns the hash value of the JobResource object.

        Returns:
            int: The hash value of the JobResource object.
        """
        return hash(
            (
                self.job_id,
                self.resource_id,
                self.instance_id,
                self.instance_type,
                self.resource_type,
                self.allocated_at,
                self.released_at,
                self.status,
                self.gpu_count,
            )
        )


def item_to_job_resource(item: Dict[str, Any]) -> JobResource:
    """Converts a DynamoDB item to a JobResource object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        JobResource: The JobResource object represented by the DynamoDB item.

    Raises:
        ValueError: When the item format is invalid.
    """
    required_keys = {
        "PK",
        "SK",
        "TYPE",
        "job_id",
        "resource_id",
        "instance_id",
        "instance_type",
        "resource_type",
        "allocated_at",
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
        job_id = item["job_id"]["S"]
        resource_id = item["resource_id"]["S"]
        instance_id = item["instance_id"]["S"]
        instance_type = item["instance_type"]["S"]
        resource_type = item["resource_type"]["S"]
        allocated_at = item["allocated_at"]["S"]
        status = item["status"]["S"]

        released_at = item.get("released_at", {}).get("S", None)
        gpu_count = (
            int(item["gpu_count"]["N"]) if "gpu_count" in item else None
        )

        resource_config = None
        if "resource_config" in item:
            resource_config = parse_dynamodb_map(item["resource_config"]["M"])

        return JobResource(
            job_id=job_id,
            resource_id=resource_id,
            instance_id=instance_id,
            instance_type=instance_type,
            resource_type=resource_type,
            allocated_at=allocated_at,
            status=status,
            gpu_count=gpu_count,
            released_at=released_at,
            resource_config=resource_config,
        )
    except (KeyError, ValueError) as e:
        raise ValueError(f"Error converting item to JobResource: {e}") from e
