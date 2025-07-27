from datetime import datetime
from typing import Any, Dict, Generator, Tuple

from receipt_dynamo.entities.util import _repr_str, assert_valid_uuid


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

    def __init__(
        self,
        queue_name: str,
        job_id: str,
        enqueued_at: datetime | str,
        priority: str = "medium",
        position: int = 0,
    ):
        """Initializes a new QueueJob object for DynamoDB.

        Args:
            queue_name (str): The name of the queue.
            job_id (str): UUID identifying the job.
            enqueued_at (datetime or str): The timestamp when the job was
                added to the queue.
            priority (str, optional): The priority level of the job.
                Defaults to "medium".
            position (int, optional): The position in the queue. Defaults to 0.

        Raises:
            ValueError: If any parameter is of an invalid type or has an
                invalid value.
        """
        if not isinstance(queue_name, str) or not queue_name:
            raise ValueError("queue_name must be a non-empty string")
        self.queue_name = queue_name

        assert_valid_uuid(job_id)
        self.job_id = job_id

        self.enqueued_at: str
        if isinstance(enqueued_at, datetime):
            self.enqueued_at = enqueued_at.isoformat()
        elif isinstance(enqueued_at, str):
            self.enqueued_at = enqueued_at
        else:
            raise ValueError(
                "enqueued_at must be a datetime object or a string"
            )

        valid_priorities = ["low", "medium", "high", "critical"]
        if (
            not isinstance(priority, str)
            or priority.lower() not in valid_priorities
        ):
            raise ValueError(f"priority must be one of {valid_priorities}")
        self.priority = priority.lower()

        if not isinstance(position, int) or position < 0:
            raise ValueError("position must be a non-negative integer")
        self.position: int = position

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

    def __eq__(self, other) -> bool:
        """Determines whether two QueueJob objects are equal.

        Args:
            other (QueueJob): The other QueueJob object to compare.

        Returns:
            bool: True if the QueueJob objects are equal, False otherwise.
        """
        if not isinstance(other, QueueJob):
            return False
        return (
            self.queue_name == other.queue_name
            and self.job_id == other.job_id
            and self.enqueued_at == other.enqueued_at
            and self.priority == other.priority
            and self.position == other.position
        )

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
        raise ValueError(f"Error converting item to QueueJob: {e}")
