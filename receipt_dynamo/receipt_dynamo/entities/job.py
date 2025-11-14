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
class Job:
    """
    Represents a training job and its associated metadata stored in a DynamoDB
    table.

    This class encapsulates job-related information such as its unique
    identifier, name, description, status, priority, and configuration. It is
    designed to support operations such as generating DynamoDB keys and
    converting job metadata to a
    DynamoDB-compatible item.

    Attributes:
        job_id (str): UUID identifying the job.
        name (str): The name of the job.
        description (str): A description of the job.
        created_at (datetime): The timestamp when the job was created.
        created_by (str): The user who created the job.
        status (str): The current status of the job (pending, running,
            succeeded, failed, cancelled).
        priority (str): The priority level of the job (low, medium, high,
            critical).
        job_config (Dict): The configuration for the job (stored as a JSON).
        estimated_duration (int): The estimated duration of the job in seconds.
        tags (Dict[str, str]): Tags associated with the job.
    """

    job_id: str
    name: str
    description: str
    created_at: str
    created_by: str
    status: str
    priority: str
    job_config: Dict[str, Any]
    estimated_duration: Optional[int] = None
    tags: Optional[Dict[str, str]] = None
    # Optional S3 storage metadata for this job. Expected keys (all optional):
    #   bucket, run_root_prefix, checkpoints_prefix, best_prefix, logs_prefix,
    #   config_prefix, publish_model_prefix
    storage: Optional[Dict[str, str]] = None

    def __post_init__(self):
        """Validates fields after dataclass initialization.

        Raises:
            ValueError: If any parameter is of an invalid type or has an
                invalid value.
        """
        assert_valid_uuid(self.job_id)

        if not isinstance(self.name, str) or not self.name:
            raise ValueError("name must be a non-empty string")

        if not isinstance(self.description, str):
            raise ValueError("description must be a string")

        # Handle created_at conversion
        if isinstance(self.created_at, datetime):
            self.created_at = self.created_at.isoformat()
        elif not isinstance(self.created_at, str):
            raise ValueError(
                "created_at must be a datetime object or a string"
            )

        if not isinstance(self.created_by, str) or not self.created_by:
            raise ValueError("created_by must be a non-empty string")

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

        valid_priorities = ["low", "medium", "high", "critical"]
        if (
            not isinstance(self.priority, str)
            or self.priority.lower() not in valid_priorities
        ):
            raise ValueError(f"priority must be one of {valid_priorities}")
        self.priority = self.priority.lower()

        if not isinstance(self.job_config, dict):
            raise ValueError("job_config must be a dictionary")

        if self.estimated_duration is not None:
            if (
                not isinstance(self.estimated_duration, int)
                or self.estimated_duration <= 0
            ):
                raise ValueError(
                    "estimated_duration must be a positive integer"
                )

        if self.tags is not None and not isinstance(self.tags, dict):
            raise ValueError("tags must be a dictionary")
        if self.tags is None:
            self.tags = {}

        # Validate optional storage map
        if self.storage is not None:
            if not isinstance(self.storage, dict):
                raise ValueError("storage must be a dictionary when provided")
            # Normalize prefixes to have trailing '/'
            normalized: Dict[str, str] = {}
            for k, v in self.storage.items():
                if not isinstance(k, str) or not isinstance(v, str):
                    raise ValueError("storage keys and values must be strings")
                if k.endswith("_prefix") and v and not v.endswith("/"):
                    normalized[k] = v + "/"
                else:
                    normalized[k] = v
            # Replace with normalized copy to avoid mutating the input dict elsewhere
            self.storage = normalized

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
            "job_config": {"M": dict_to_dynamodb_map(self.job_config)},
        }

        if self.estimated_duration is not None:
            item["estimated_duration"] = {"N": str(self.estimated_duration)}

        if self.tags:
            item["tags"] = {"M": {k: {"S": v} for k, v in self.tags.items()}}

        if self.storage:
            item["storage"] = {"M": dict_to_dynamodb_map(self.storage)}

        return item

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
            Generator[Tuple[str, Any], None, None]: An iterator over the Job
                object's attribute name/value pairs.
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
        yield "storage", self.storage

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
                # Include storage map in hash if present
                (
                    tuple(sorted((k, str(v)) for k, v in self.storage.items()))
                    if self.storage
                    else None
                ),
            )
        )

    # ----- S3 Storage helper methods -----
    def storage_bucket(self) -> Optional[str]:
        """Returns the S3 bucket associated with this job, if provided."""
        return (
            (self.storage or {}).get("bucket")
            if self.storage is not None
            else None
        )

    def storage_prefix(self, key: str) -> Optional[str]:
        """Returns a storage prefix by key (e.g., 'run_root_prefix', 'best_prefix')."""
        return (
            (self.storage or {}).get(key) if self.storage is not None else None
        )

    def s3_uri_for_prefix(self, key: str) -> Optional[str]:
        """Builds an s3:// URI for a named prefix if bucket and prefix are set."""
        bucket = self.storage_bucket()
        prefix = self.storage_prefix(key)
        if bucket and prefix:
            return f"s3://{bucket}/{prefix}"
        return None

    def best_dir_uri(self) -> Optional[str]:
        """Returns the s3:// URI to the best checkpoint directory if known."""
        # Prefer explicit best_prefix; otherwise derive from run_root_prefix if available
        explicit = self.s3_uri_for_prefix("best_prefix")
        if explicit:
            return explicit
        bucket = self.storage_bucket()
        run_root = self.storage_prefix("run_root_prefix")
        if bucket and run_root:
            return f"s3://{bucket}/{run_root}best/"
        return None

    def publish_dir_uri(self) -> Optional[str]:
        """Returns the s3:// URI where the publishable model bundle should live, if set."""
        return self.s3_uri_for_prefix("publish_model_prefix")


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
            f"Invalid item format\nmissing keys: {missing_keys}\n"
            f"additional keys: {additional_keys}"
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
        job_config = parse_dynamodb_map(item["job_config"]["M"])

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

        # Parse optional storage map
        storage: Optional[Dict[str, Any]] = None
        if "storage" in item and "M" in item["storage"]:
            storage = parse_dynamodb_map(item["storage"]["M"])

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
            storage=storage,
        )
    except KeyError as e:
        raise ValueError(f"Error converting item to Job: {e}") from e
