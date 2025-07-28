from datetime import datetime
from typing import Any, Dict, Generator, Optional, Tuple

from receipt_dynamo.entities.util import _repr_str, assert_valid_uuid


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

    def __init__(
        self,
        instance_id: str,
        job_id: str,
        assigned_at: datetime,
        status: str,
        resource_utilization: Optional[Dict[str, Any]] = None,
    ):
        """Initializes a new InstanceJob object for DynamoDB.

        Args:
            instance_id (str): Amazon EC2 instance ID.
            job_id (str): UUID identifying the job.
            assigned_at (datetime): The timestamp when the job was assigned to
                the instance.
            status (str): The current status of the job on this instance.
            resource_utilization (Dict, optional): Resource utilization
                metrics.

        Raises:
            ValueError: If any parameter is of an invalid type or has an
                invalid value.
        """
        if not isinstance(instance_id, str) or not instance_id:
            raise ValueError("instance_id must be a non-empty string")
        self.instance_id = instance_id

        assert_valid_uuid(job_id)
        self.job_id = job_id

        self.assigned_at: str
        if isinstance(assigned_at, datetime):
            self.assigned_at = assigned_at.isoformat()
        elif isinstance(assigned_at, str):
            self.assigned_at = assigned_at
        else:
            raise ValueError(
                "assigned_at must be a datetime object or a string"
            )

        valid_statuses = [
            "assigned",
            "running",
            "completed",
            "failed",
            "cancelled",
        ]
        if not isinstance(status, str) or status.lower() not in valid_statuses:
            raise ValueError(f"status must be one of {valid_statuses}")
        self.status = status.lower()

        if resource_utilization is not None and not isinstance(
            resource_utilization, dict
        ):
            raise ValueError("resource_utilization must be a dictionary")
        self.resource_utilization = resource_utilization or {}

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
                "M": self._dict_to_dynamodb_map(self.resource_utilization)
            }

        return item

    def _dict_to_dynamodb_map(self, d: Dict) -> Dict:
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

    def _to_dynamodb_value(self, v: Any) -> Dict:
        """Converts a Python value to a DynamoDB value.

        Args:
            v (Any): The value to convert.

        Returns:
            Dict: The DynamoDB value representation.
        """
        if isinstance(v, dict):
            return {"M": self._dict_to_dynamodb_map(v)}
        if isinstance(v, list):
            return {"L": [self._to_dynamodb_value(item) for item in v]}
        if isinstance(v, str):
            return {"S": v}
        if isinstance(v, (int, float)):
            return {"N": str(v)}
        if isinstance(v, bool):
            return {"BOOL": v}
        if v is None:
            return {"NULL": True}
        return {"S": str(v)}

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

    def __eq__(self, other) -> bool:
        """Determines whether two InstanceJob objects are equal.

        Args:
            other (InstanceJob): The other InstanceJob object to compare.

        Returns:
            bool: True if the InstanceJob objects are equal, False otherwise.

        Note:
            If other is not an instance of InstanceJob, False is returned.
        """
        if not isinstance(other, InstanceJob):
            return False
        return (
            self.instance_id == other.instance_id
            and self.job_id == other.job_id
            and self.assigned_at == other.assigned_at
            and self.status == other.status
            and self.resource_utilization == other.resource_utilization
        )

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
                            (k, str(v))
                            for k, v in self.resource_utilization.items()
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
        if (
            "resource_utilization" in item
            and "M" in item["resource_utilization"]
        ):
            resource_utilization = _parse_dynamodb_map(
                item["resource_utilization"]["M"]
            )

        return InstanceJob(
            instance_id=instance_id,
            job_id=job_id,
            assigned_at=assigned_at,
            status=status,
            resource_utilization=resource_utilization,
        )
    except KeyError as e:
        raise ValueError(f"Error converting item to InstanceJob: {e}") from e


def _parse_dynamodb_map(dynamodb_map: Dict) -> Dict:
    """Parses a DynamoDB map to a Python dictionary.

    Args:
        dynamodb_map (Dict): The DynamoDB map to parse.

    Returns:
        Dict: The parsed Python dictionary.
    """
    result: Dict[str, Any] = {}
    for k, v in dynamodb_map.items():
        if "M" in v:
            result[k] = _parse_dynamodb_map(v["M"])
        elif "L" in v:
            result[k] = [_parse_dynamodb_value(item) for item in v["L"]]
        elif "S" in v:
            result[k] = v["S"]
        elif "N" in v:
            # Try to convert to int first, then float if that fails
            try:
                result[k] = int(v["N"])
            except ValueError:
                result[k] = float(v["N"])
        elif "BOOL" in v:
            result[k] = v["BOOL"]
        elif "NULL" in v:
            result[k] = None
        else:
            # Default fallback
            result[k] = str(v)
    return result


def _parse_dynamodb_value(dynamodb_value: Dict) -> Any:
    """Parses a DynamoDB value to a Python value.

    Args:
        dynamodb_value (Dict): The DynamoDB value to parse.

    Returns:
        Any: The parsed Python value.
    """
    if "M" in dynamodb_value:
        return _parse_dynamodb_map(dynamodb_value["M"])
    if "L" in dynamodb_value:
        return [_parse_dynamodb_value(item) for item in dynamodb_value["L"]]
    if "S" in dynamodb_value:
        return dynamodb_value["S"]
    if "N" in dynamodb_value:
        # Try to convert to int first, then float if that fails
        try:
            return int(dynamodb_value["N"])
        except ValueError:
            return float(dynamodb_value["N"])
    if "BOOL" in dynamodb_value:
        return dynamodb_value["BOOL"]
    if "NULL" in dynamodb_value:
        return None
    # Default fallback
    return str(dynamodb_value)
