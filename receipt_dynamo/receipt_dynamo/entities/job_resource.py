from datetime import datetime
from typing import Any, Dict, Generator, Optional, Tuple

from receipt_dynamo.entities.util import _repr_str, assert_valid_uuid


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

    def __init__(
        self,
        job_id: str,
        resource_id: str,
        instance_id: str,
        instance_type: str,
        resource_type: str,
        allocated_at: datetime,
        status: str,
        gpu_count: Optional[int] = None,
        released_at: Optional[datetime] = None,
        resource_config: Optional[Dict[str, Any]] = None,
    ):
        """Initializes a new JobResource object for DynamoDB.

        Args:
            job_id (str): UUID identifying the job.
            resource_id (str): UUID identifying the resource allocation.
            instance_id (str): ID of the EC2 instance providing the resources.
            instance_type (str): The type of EC2 instance (e.g., p3.2xlarge).
            resource_type (str): The type of resource allocation.
            allocated_at (datetime): The timestamp when the resources were
                allocated.
            status (str): The current status of the resource allocation.
            gpu_count (Optional[int]): Number of GPUs allocated to the job.
            released_at (Optional[datetime]): The timestamp when resources
                were released.
            resource_config (Optional[Dict]): Additional configuration details
                for the resource.

        Raises:
            ValueError: If any parameter is of an invalid type or has an
                invalid value.
        """
        assert_valid_uuid(job_id)
        self.job_id = job_id

        assert_valid_uuid(resource_id)
        self.resource_id = resource_id

        if not isinstance(instance_id, str) or not instance_id:
            raise ValueError("instance_id must be a non-empty string")
        self.instance_id = instance_id

        if not isinstance(instance_type, str) or not instance_type:
            raise ValueError("instance_type must be a non-empty string")
        self.instance_type = instance_type

        valid_resource_types = [
            "cpu",
            "gpu",
            "memory",
            "storage",
            "network",
            "combined",
        ]
        if (
            not isinstance(resource_type, str)
            or resource_type.lower() not in valid_resource_types
        ):
            raise ValueError(
                f"resource_type must be one of {valid_resource_types}"
            )
        self.resource_type = resource_type.lower()

        self.allocated_at: str
        if isinstance(allocated_at, datetime):
            self.allocated_at = allocated_at.isoformat()
        elif isinstance(allocated_at, str):
            self.allocated_at = allocated_at
        else:
            raise ValueError(
                "allocated_at must be a datetime object or a string"
            )

        self.released_at: Optional[str]
        if released_at is not None:
            if isinstance(released_at, datetime):
                self.released_at = released_at.isoformat()
            elif isinstance(released_at, str):
                self.released_at = released_at
            else:
                raise ValueError(
                    "released_at must be a datetime object or a string"
                )
        else:
            self.released_at = None

        valid_statuses = ["allocated", "released", "failed", "pending"]
        if not isinstance(status, str) or status.lower() not in valid_statuses:
            raise ValueError(f"status must be one of {valid_statuses}")
        self.status = status.lower()

        if gpu_count is not None:
            if not isinstance(gpu_count, int) or gpu_count < 0:
                raise ValueError("gpu_count must be a non-negative integer")
        self.gpu_count: Optional[int] = gpu_count

        if resource_config is not None and not isinstance(
            resource_config, dict
        ):
            raise ValueError("resource_config must be a dictionary")
        self.resource_config: Dict[str, Any] = resource_config or {}

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
                "M": self._dict_to_dynamodb_map(self.resource_config)
            }

        return item

    def _dict_to_dynamodb_map(self, d: Dict[str, Any]) -> Dict[str, Any]:
        """Converts a Python dictionary to a DynamoDB map.

        Args:
            d (Dict): The dictionary to convert.

        Returns:
            Dict: The DynamoDB map representation.
        """
        result: Dict[str, Any] = {}
        for k, v in d.items():
            if isinstance(v, dict):
                result[k] = {"M": self._dict_to_dynamodb_map(v)}
            elif isinstance(v, list):
                result[k] = {
                    "L": [self._to_dynamodb_value(item) for item in v]
                }
            elif isinstance(v, str):
                result[k] = {"S": v}
            elif isinstance(v, (int, float)):
                result[k] = {"N": str(v)}
            elif isinstance(v, bool):
                result[k] = {"BOOL": v}
            elif v is None:
                result[k] = {"NULL": True}
            else:
                result[k] = {"S": str(v)}
        return result

    def _to_dynamodb_value(self, v: Any) -> Dict[str, Any]:
        """Converts a Python value to a DynamoDB value.

        Args:
            v (Any): The value to convert.

        Returns:
            Dict: The DynamoDB value representation.
        """
        if isinstance(v, dict):
            return {"M": self._dict_to_dynamodb_map(v)}
        elif isinstance(v, list):
            return {"L": [self._to_dynamodb_value(item) for item in v]}
        elif isinstance(v, str):
            return {"S": v}
        elif isinstance(v, (int, float)):
            return {"N": str(v)}
        elif isinstance(v, bool):
            return {"BOOL": v}
        elif v is None:
            return {"NULL": True}
        else:
            return {"S": str(v)}

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

    def __eq__(self, other) -> bool:
        """Determines whether two JobResource objects are equal.

        Args:
            other (JobResource): The other JobResource object to compare.

        Returns:
            bool: True if the JobResource objects are equal, False otherwise.
        """
        if not isinstance(other, JobResource):
            return False
        return (
            self.job_id == other.job_id
            and self.resource_id == other.resource_id
            and self.instance_id == other.instance_id
            and self.instance_type == other.instance_type
            and self.resource_type == other.resource_type
            and self.allocated_at == other.allocated_at
            and self.released_at == other.released_at
            and self.status == other.status
            and self.gpu_count == other.gpu_count
            and self.resource_config == other.resource_config
        )

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
            resource_config = _parse_dynamodb_map(item["resource_config"]["M"])

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


def _parse_dynamodb_value(dynamodb_value: Dict) -> Any:
    """Parse a DynamoDB-formatted value back to a Python value.

    Args:
        dynamodb_value (Dict): A DynamoDB-formatted value.

    Returns:
        Any: The Python-native value.

    Raises:
        ValueError: If the DynamoDB value format is invalid.
    """
    if "S" in dynamodb_value:
        return dynamodb_value["S"]
    elif "N" in dynamodb_value:
        try:
            return int(dynamodb_value["N"])
        except ValueError:
            return float(dynamodb_value["N"])
    elif "BOOL" in dynamodb_value:
        return dynamodb_value["BOOL"]
    elif "NULL" in dynamodb_value:
        return None
    elif "M" in dynamodb_value:
        return _parse_dynamodb_map(dynamodb_value["M"])
    elif "L" in dynamodb_value:
        return [_parse_dynamodb_value(item) for item in dynamodb_value["L"]]
    else:
        raise ValueError(f"Unknown DynamoDB value format: {dynamodb_value}")


def _parse_dynamodb_map(dynamodb_map: Dict[str, Any]) -> Dict[str, Any]:
    """Parse a DynamoDB-formatted map back to a Python dictionary.

    Args:
        dynamodb_map (Dict): A DynamoDB-formatted map.

    Returns:
        Dict: The equivalent Python dictionary.

    Raises:
        ValueError: If the DynamoDB map format is invalid.
    """
    result: Dict[str, Any] = {}
    for k, v in dynamodb_map.items():
        result[k] = _parse_dynamodb_value(v)
    return result
