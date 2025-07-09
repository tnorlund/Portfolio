from datetime import datetime
from typing import Any, Dict, Generator, Optional, Tuple

from receipt_dynamo.entities.util import _repr_str, assert_valid_uuid


class Job:
    """
    Represents a training job and its associated metadata stored in a DynamoDB table.

    This class encapsulates job-related information such as its unique identifier,
    name, description, status, priority, and configuration. It is designed to support
    operations such as generating DynamoDB keys and converting job metadata to a
    DynamoDB-compatible item.

    Attributes:
        job_id (str): UUID identifying the job.
        name (str): The name of the job.
        description (str): A description of the job.
        created_at (datetime): The timestamp when the job was created.
        created_by (str): The user who created the job.
        status (str): The current status of the job (pending, running, succeeded, failed, cancelled).
        priority (str): The priority level of the job (low, medium, high, critical).
        job_config (Dict): The configuration for the job (stored as a JSON).
        estimated_duration (int): The estimated duration of the job in seconds.
        tags (Dict[str, str]): Tags associated with the job.
    """

    def __init__(
        self,
        job_id: str,
        name: str,
        description: str,
        created_at: datetime,
        created_by: str,
        status: str,
        priority: str,
        job_config: Dict[str, Any],
        estimated_duration: Optional[int] = None,
        tags: Optional[Dict[str, str]] = None,
    ):
        """Initializes a new Job object for DynamoDB.

        Args:
            job_id (str): UUID identifying the job.
            name (str): The name of the job.
            description (str): A description of the job.
            created_at (datetime): The timestamp when the job was created.
            created_by (str): The user who created the job.
            status (str): The current status of the job.
            priority (str): The priority level of the job.
            job_config (Dict): The configuration for the job.
            estimated_duration (int, optional): The estimated duration in seconds.
            tags (Dict[str, str], optional): Tags associated with the job.

        Raises:
            ValueError: If any parameter is of an invalid type or has an invalid value.
        """
        assert_valid_uuid(job_id)
        self.job_id = job_id

        if not isinstance(name, str) or not name:
            raise ValueError("name must be a non-empty string")
        self.name = name

        if not isinstance(description, str):
            raise ValueError("description must be a string")
        self.description = description

        self.created_at: str
        if isinstance(created_at, datetime):
            self.created_at = created_at.isoformat()
        elif isinstance(created_at, str):
            self.created_at = created_at
        else:
            raise ValueError(
                "created_at must be a datetime object or a string"
            )

        if not isinstance(created_by, str) or not created_by:
            raise ValueError("created_by must be a non-empty string")
        self.created_by = created_by

        valid_statuses = [
            "pending",
            "running",
            "succeeded",
            "failed",
            "cancelled",
            "interrupted",
        ]
        if not isinstance(status, str) or status.lower() not in valid_statuses:
            raise ValueError(f"status must be one of {valid_statuses}")
        self.status: str = status.lower()

        valid_priorities = ["low", "medium", "high", "critical"]
        if (
            not isinstance(priority, str)
            or priority.lower() not in valid_priorities
        ):
            raise ValueError(f"priority must be one of {valid_priorities}")
        self.priority: str = priority.lower()

        if not isinstance(job_config, dict):
            raise ValueError("job_config must be a dictionary")
        self.job_config: Dict[str, Any] = job_config

        if estimated_duration is not None:
            if (
                not isinstance(estimated_duration, int)
                or estimated_duration <= 0
            ):
                raise ValueError(
                    "estimated_duration must be a positive integer"
                )
        self.estimated_duration: Optional[int] = estimated_duration

        if tags is not None and not isinstance(tags, dict):
            raise ValueError("tags must be a dictionary")
        self.tags: Dict[str, str] = tags or {}

    @property
    def key(self) -> Dict[str, Any]:
        """Generates the primary key for the job.

        Returns:
            dict: The primary key for the job.
        """
        return {"PK": {"S": f"JOB#{self.job_id}"}, "SK": {"S": "JOB"}}

    def gsi1_key(self) -> Dict[str, Any]:
        """Generates the GSI1 key for the job.

        Returns:
            dict: The GSI1 key for the job.
        """
        return {
            "GSI1PK": {"S": f"STATUS#{self.status}"},
            "GSI1SK": {"S": f"CREATED#{self.created_at}"},
        }

    def to_item(self) -> Dict[str, Any]:
        """Converts the Job object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the Job object as a DynamoDB item.
        """
        item = {
            **self.key,
            **self.gsi1_key(),
            "TYPE": {"S": "JOB"},
            "name": {"S": self.name},
            "description": {"S": self.description},
            "created_at": {"S": self.created_at},
            "created_by": {"S": self.created_by},
            "status": {"S": self.status},
            "priority": {"S": self.priority},
            "job_config": {"M": self._dict_to_dynamodb_map(self.job_config)},
        }

        if self.estimated_duration is not None:
            item["estimated_duration"] = {"N": str(self.estimated_duration)}

        if self.tags:
            item["tags"] = {"M": {k: {"S": v} for k, v in self.tags.items()}}

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
        """Returns a string representation of the Job object.

        Returns:
            str: A string representation of the Job object.
        """
        return (
            "Job("
            f"job_id={_repr_str(self.job_id)}, "
            f"name={_repr_str(self.name)}, "
            f"description={_repr_str(self.description)}, "
            f"created_at={_repr_str(self.created_at)}, "
            f"created_by={_repr_str(self.created_by)}, "
            f"status={_repr_str(self.status)}, "
            f"priority={_repr_str(self.priority)}, "
            f"job_config={self.job_config}, "
            f"estimated_duration={self.estimated_duration}, "
            f"tags={self.tags}"
            ")"
        )

    def __iter__(self) -> Generator[Tuple[str, Any], None, None]:
        """Returns an iterator over the Job object's attributes.

        Returns:
            Generator[Tuple[str, Any], None, None]: An iterator over the Job object's attribute name/value pairs.
        """
        yield "job_id", self.job_id
        yield "name", self.name
        yield "description", self.description
        yield "created_at", self.created_at
        yield "created_by", self.created_by
        yield "status", self.status
        yield "priority", self.priority
        yield "job_config", self.job_config
        yield "estimated_duration", self.estimated_duration
        yield "tags", self.tags

    def __eq__(self, other) -> bool:
        """Determines whether two Job objects are equal.

        Args:
            other (Job): The other Job object to compare.

        Returns:
            bool: True if the Job objects are equal, False otherwise.

        Note:
            If other is not an instance of Job, False is returned.
        """
        if not isinstance(other, Job):
            return False
        return (
            self.job_id == other.job_id
            and self.name == other.name
            and self.description == other.description
            and self.created_at == other.created_at
            and self.created_by == other.created_by
            and self.status == other.status
            and self.priority == other.priority
            and self.job_config == other.job_config
            and self.estimated_duration == other.estimated_duration
            and self.tags == other.tags
        )

    def __hash__(self) -> int:
        """Returns the hash value of the Job object.

        Returns:
            int: The hash value of the Job object.
        """
        return hash(
            (
                self.job_id,
                self.name,
                self.description,
                self.created_at,
                self.created_by,
                self.status,
                self.priority,
                # Can't hash dictionaries, so convert to tuple of sorted items
                tuple(sorted((k, str(v)) for k, v in self.job_config.items())),
                self.estimated_duration,
                # Can't hash dictionaries, so convert to tuple of sorted items
                tuple(sorted(self.tags.items())) if self.tags else None,
            )
        )


def item_to_job(item: Dict[str, Any]) -> Job:
    """Converts a DynamoDB item to a Job object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        Job: The Job object represented by the DynamoDB item.

    Raises:
        ValueError: When the item format is invalid.
    """
    required_keys = {
        "PK",
        "SK",
        "TYPE",
        "name",
        "description",
        "created_at",
        "created_by",
        "status",
        "priority",
        "job_config",
    }
    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - item.keys()
        additional_keys = item.keys() - required_keys
        raise ValueError(
            f"Invalid item format\nmissing keys: {missing_keys}\nadditional keys: {additional_keys}"
        )

    try:
        # Parse job_id from the PK
        job_id = item["PK"]["S"].split("#")[1]

        # Extract basic string fields
        name = item["name"]["S"]
        description = item["description"]["S"]
        created_at = item["created_at"]["S"]
        created_by = item["created_by"]["S"]
        status = item["status"]["S"]
        priority = item["priority"]["S"]

        # Parse job_config from DynamoDB map
        job_config = _parse_dynamodb_map(item["job_config"]["M"])

        # Parse optional fields
        estimated_duration = (
            int(item["estimated_duration"]["N"])
            if "estimated_duration" in item
            else None
        )

        # Parse tags if present
        tags: Optional[Dict[str, Any]] = None
        if "tags" in item and "M" in item["tags"]:
            tags = {k: v["S"] for k, v in item["tags"]["M"].items()}

        return Job(
            job_id=job_id,
            name=name,
            description=description,
            created_at=created_at,
            created_by=created_by,
            status=status,
            priority=priority,
            job_config=job_config,
            estimated_duration=estimated_duration,
            tags=tags,
        )
    except KeyError as e:
        raise ValueError(f"Error converting item to Job: {e}")


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
    elif "L" in dynamodb_value:
        return [_parse_dynamodb_value(item) for item in dynamodb_value["L"]]
    elif "S" in dynamodb_value:
        return dynamodb_value["S"]
    elif "N" in dynamodb_value:
        # Try to convert to int first, then float if that fails
        try:
            return int(dynamodb_value["N"])
        except ValueError:
            return float(dynamodb_value["N"])
    elif "BOOL" in dynamodb_value:
        return dynamodb_value["BOOL"]
    elif "NULL" in dynamodb_value:
        return None
    else:
        # Default fallback
        return str(dynamodb_value)
