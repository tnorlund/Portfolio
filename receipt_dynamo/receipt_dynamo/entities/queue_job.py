from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Generator, Tuple

from receipt_dynamo.entities.util import _repr_str, assert_valid_uuid


@dataclass(eq=True, unsafe_hash=False)
class QueueJob:
    """
    Represents an association between a queue and a job in DynamoDB.

    This class encapsulates the relationship between a queue and a job,
    including
    attributes such as enqueued timestamp, priority, and position in the queue.
    It is designed to support operations such as generating DynamoDB keys and
    converting queue-job associations to DynamoDB-compatible items.

    Attributes:
        queue_name (str): The name of the queue.
        job_id (str): UUID identifying the job.
        enqueued_at (str): The timestamp when the job was added to the queue.
        priority (str): The priority level of the job in this queue (low,
            medium, high, critical).
        position (int): The position of the job in the queue (lower numbers
            are processed first).
    """

    queue_name: str
    job_id: str
    enqueued_at: datetime | str
    priority: str = "medium"
    position: int = 0

    def __post_init__(self):
        """Validate and process the dataclass fields after initialization.

        Raises:
            ValueError: If any parameter is of an invalid type or has an
                invalid value.
        """
        if not isinstance(self.queue_name, str) or not self.queue_name:
            raise ValueError("queue_name must be a non-empty string")

        assert_valid_uuid(self.job_id)

        # Convert datetime to string if needed
        if isinstance(self.enqueued_at, datetime):
            self.enqueued_at = self.enqueued_at.isoformat()
        elif isinstance(self.enqueued_at, str):
            # Keep as string
            pass
        else:
            raise ValueError(
                "enqueued_at must be a datetime object or a string"
            )

        valid_priorities = ["low", "medium", "high", "critical"]
        if (
            not isinstance(self.priority, str)
            or self.priority.lower() not in valid_priorities
        ):
            raise ValueError(f"priority must be one of {valid_priorities}")
        self.priority = self.priority.lower()

        if not isinstance(self.position, int) or self.position < 0:
            raise ValueError("position must be a non-negative integer")

    @property
    def key(self) -> Dict[str, Any]:
        """Generates the primary key for the queue-job association.

        Returns:
            dict: The primary key for the queue-job association.
        """
        return {
            "PK": {"S": f"QUEUE#{self.queue_name}"},
            "SK": {"S": f"JOB#{self.job_id}"},
        }

    def gsi1_key(self) -> Dict[str, Any]:
        """Generates the GSI1 key for the queue-job association.

        Returns:
            dict: The GSI1 key for the queue-job association.
        """
        return {
            "GSI1PK": {"S": "JOB"},
            "GSI1SK": {"S": f"JOB#{self.job_id}#QUEUE#{self.queue_name}"},
        }

    def to_item(self) -> Dict[str, Any]:
        """Converts the QueueJob object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the QueueJob object as a
                DynamoDB item.
        """
        item = {
            **self.key,
            **self.gsi1_key(),
            "TYPE": {"S": "QUEUE_JOB"},
            "enqueued_at": {"S": self.enqueued_at},
            "priority": {"S": self.priority},
            "position": {"N": str(self.position)},
        }
        return item

    def __repr__(self) -> str:
        """Returns a string representation of the QueueJob object.

        Returns:
            str: A string representation of the QueueJob object.
        """
        return (
            "QueueJob("
            f"queue_name={_repr_str(self.queue_name)}, "
            f"job_id={_repr_str(self.job_id)}, "
            f"enqueued_at={_repr_str(self.enqueued_at)}, "
            f"priority={_repr_str(self.priority)}, "
            f"position={self.position}"
            ")"
        )

    def __iter__(self) -> Generator[Tuple[str, Any], None, None]:
        """Returns an iterator over the QueueJob object's attributes.

        Returns:
            Generator[Tuple[str, Any], None, None]: An iterator over
                attribute name/value pairs.
        """
        yield "queue_name", self.queue_name
        yield "job_id", self.job_id
        yield "enqueued_at", self.enqueued_at
        yield "priority", self.priority
        yield "position", self.position

    def __hash__(self) -> int:
        """Returns the hash value of the QueueJob object.

        Returns:
            int: The hash value of the QueueJob object.
        """
        return hash(
            (
                self.queue_name,
                self.job_id,
                self.enqueued_at,
                self.priority,
                self.position,
            )
        )


def item_to_queue_job(item: Dict[str, Any]) -> QueueJob:
    """Converts a DynamoDB item to a QueueJob object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        QueueJob: The QueueJob object represented by the DynamoDB item.

    Raises:
        ValueError: When the item format is invalid.
    """
    required_keys = {"PK", "SK", "TYPE", "enqueued_at", "priority", "position"}
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

        # Parse job_id from the SK
        job_id = item["SK"]["S"].split("#")[1]

        # Extract fields
        enqueued_at = item["enqueued_at"]["S"]
        priority = item["priority"]["S"]
        position = int(item["position"]["N"])

        return QueueJob(
            queue_name=queue_name,
            job_id=job_id,
            enqueued_at=enqueued_at,
            priority=priority,
            position=position,
        )
    except (KeyError, IndexError) as e:
        raise ValueError(f"Error converting item to QueueJob: {e}") from e
