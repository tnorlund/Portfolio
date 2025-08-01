from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Generator, Tuple

from receipt_dynamo.entities.util import _repr_str


@dataclass(eq=True, unsafe_hash=False)
class Queue:
    """
    Represents a queue for organizing training jobs in DynamoDB.

    This class encapsulates queue-related information such as its name,
    description, creation time, maximum concurrent jobs, priority, and job
    count. It is designed to support operations such as generating DynamoDB
    keys and converting queue metadata to a DynamoDB-compatible item.

    Attributes:
        queue_name (str): The name of the queue (unique identifier).
        description (str): A description of the queue.
        created_at (datetime or str): The timestamp when the queue was created.
        max_concurrent_jobs (int): Maximum number of jobs that can run
            concurrently.
        priority (str): The priority level of the queue (low, medium, high,
            critical).
        job_count (int): The current number of jobs in the queue.
    """

    queue_name: str
    description: str
    created_at: datetime | str
    max_concurrent_jobs: int = 1
    priority: str = "medium"
    job_count: int = 0

    def __post_init__(self):
        """Initializes and validates the Queue object.

        Raises:
            ValueError: If any parameter is of an invalid type or has an
                invalid value.
        """
        if not isinstance(self.queue_name, str) or not self.queue_name:
            raise ValueError("queue_name must be a non-empty string")

        if not isinstance(self.description, str):
            raise ValueError("description must be a string")

        if isinstance(self.created_at, datetime):
            self.created_at = self.created_at.isoformat()
        elif isinstance(self.created_at, str):
            pass  # Already a string
        else:
            raise ValueError(
                "created_at must be a datetime object or a string"
            )

        if not isinstance(self.max_concurrent_jobs, int) or self.max_concurrent_jobs < 1:
            raise ValueError("max_concurrent_jobs must be a positive integer")

        valid_priorities = ["low", "medium", "high", "critical"]
        if (
            not isinstance(self.priority, str)
            or self.priority.lower() not in valid_priorities
        ):
            raise ValueError(f"priority must be one of {valid_priorities}")
        self.priority = self.priority.lower()

        if not isinstance(self.job_count, int) or self.job_count < 0:
            raise ValueError("job_count must be a non-negative integer")

    @property
    def key(self) -> Dict[str, Any]:
        """Generates the primary key for the queue.

        Returns:
            dict: The primary key for the queue.
        """
        return {"PK": {"S": f"QUEUE#{self.queue_name}"}, "SK": {"S": "QUEUE"}}

    def gsi1_key(self) -> Dict[str, Any]:
        """Generates the GSI1 key for the queue.

        Returns:
            dict: The GSI1 key for the queue.
        """
        return {
            "GSI1PK": {"S": "QUEUE"},
            "GSI1SK": {"S": f"QUEUE#{self.queue_name}"},
        }

    def to_item(self) -> Dict[str, Any]:
        """Converts the Queue object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the Queue object as a DynamoDB
                item.
        """
        item = {
            **self.key,
            **self.gsi1_key(),
            "TYPE": {"S": "QUEUE"},
            "description": {"S": self.description},
            "created_at": {"S": self.created_at},
            "max_concurrent_jobs": {"N": str(self.max_concurrent_jobs)},
            "priority": {"S": self.priority},
            "job_count": {"N": str(self.job_count)},
        }
        return item

    def __repr__(self) -> str:
        """Returns a string representation of the Queue object.

        Returns:
            str: A string representation of the Queue object.
        """
        return (
            "Queue("
            f"queue_name={_repr_str(self.queue_name)}, "
            f"description={_repr_str(self.description)}, "
            f"created_at={_repr_str(self.created_at)}, "
            f"max_concurrent_jobs={self.max_concurrent_jobs}, "
            f"priority={_repr_str(self.priority)}, "
            f"job_count={self.job_count}"
            ")"
        )

    def __iter__(self) -> Generator[Tuple[str, Any], None, None]:
        """Returns an iterator over the Queue object's attributes.

        Returns:
            Generator[Tuple[str, Any], None, None]: An iterator over
                attribute name/value pairs.
        """
        yield "queue_name", self.queue_name
        yield "description", self.description
        yield "created_at", self.created_at
        yield "max_concurrent_jobs", self.max_concurrent_jobs
        yield "priority", self.priority
        yield "job_count", self.job_count


    def __hash__(self) -> int:
        """Returns the hash value of the Queue object.

        Returns:
            int: The hash value of the Queue object.
        """
        return hash(
            (
                self.queue_name,
                self.description,
                self.created_at,
                self.max_concurrent_jobs,
                self.priority,
                self.job_count,
            )
        )


def item_to_queue(item: Dict[str, Any]) -> Queue:
    """Converts a DynamoDB item to a Queue object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        Queue: The Queue object represented by the DynamoDB item.

    Raises:
        ValueError: When the item format is invalid.
    """
    required_keys = {
        "PK",
        "SK",
        "TYPE",
        "description",
        "created_at",
        "max_concurrent_jobs",
        "priority",
        "job_count",
    }
    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - item.keys()
        additional_keys = item.keys() - required_keys
        raise ValueError(
            f"Invalid item format\nmissing keys: {missing_keys}\n"
            f"additional keys: {additional_keys}"
        )

    try:
        # Parse queue_name from the PK
        queue_name = item["PK"]["S"].split("#")[1]

        # Extract fields
        description = item["description"]["S"]
        created_at = item["created_at"]["S"]
        max_concurrent_jobs = int(item["max_concurrent_jobs"]["N"])
        priority = item["priority"]["S"]
        job_count = int(item["job_count"]["N"])

        return Queue(
            queue_name=queue_name,
            description=description,
            created_at=created_at,
            max_concurrent_jobs=max_concurrent_jobs,
            priority=priority,
            job_count=job_count,
        )
    except (KeyError, IndexError) as e:
        raise ValueError(f"Error converting item to Queue: {e}") from e
