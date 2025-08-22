from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Generator, Optional, Tuple

from receipt_dynamo.entities.util import _repr_str, assert_valid_uuid


@dataclass(eq=True, unsafe_hash=False)
class JobLog:
    """
    Represents a log entry for a job stored in DynamoDB.

    This class encapsulates job log information such as job identifier,
    timestamp, log level, message, source, and exception details. It supports
    operations like generating DynamoDB keys and converting log data to
    DynamoDB-compatible items.

    Attributes:
        job_id (str): UUID identifying the job this log belongs to.
        timestamp (str): The timestamp when the log was created.
        log_level (str): The log level (e.g., INFO, WARNING, ERROR, DEBUG).
        message (str): The log message.
        source (str, optional): The source of the log (e.g., component name).
        exception (str, optional): Exception details if applicable.
    """

    job_id: str
    timestamp: str
    log_level: str
    message: str
    source: Optional[str] = None
    exception: Optional[str] = None

    def __post_init__(self):
        """Validates fields after dataclass initialization.

        Raises:
            ValueError: If any parameter is of invalid type or has invalid
                value.
        """
        assert_valid_uuid(self.job_id)

        # Handle timestamp conversion
        if isinstance(self.timestamp, datetime):
            self.timestamp = self.timestamp.isoformat()
        elif not isinstance(self.timestamp, str):
            raise ValueError("timestamp must be a datetime object or a string")

        valid_log_levels = ["INFO", "WARNING", "ERROR", "DEBUG", "CRITICAL"]
        if (
            not isinstance(self.log_level, str)
            or self.log_level.upper() not in valid_log_levels
        ):
            raise ValueError(f"log_level must be one of {valid_log_levels}")
        self.log_level = self.log_level.upper()

        if not isinstance(self.message, str) or not self.message:
            raise ValueError("message must be a non-empty string")

        if self.source is not None and not isinstance(self.source, str):
            raise ValueError("source must be a string")

        if self.exception is not None and not isinstance(self.exception, str):
            raise ValueError("exception must be a string")

    @property
    def key(self) -> Dict[str, Any]:
        """Generates the primary key for the job log.

        Returns:
            dict: The primary key for the job log.
        """
        return {
            "PK": {"S": f"JOB#{self.job_id}"},
            "SK": {"S": f"LOG#{self.timestamp}"},
        }

    def gsi1_key(self) -> Dict[str, Any]:
        """Generates the GSI1 key for the job log.

        Returns:
            dict: The GSI1 key for the job log.
        """
        return {
            "GSI1PK": {"S": "LOG"},
            "GSI1SK": {"S": f"JOB#{self.job_id}#{self.timestamp}"},
        }

    def to_item(self) -> Dict[str, Any]:
        """Converts the JobLog object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the JobLog object as a DynamoDB
                item.
        """
        item = {
            **self.key,
            **self.gsi1_key(),
            "TYPE": {"S": "JOB_LOG"},
            "log_level": {"S": self.log_level},
            "message": {"S": self.message},
        }

        if self.source is not None:
            item["source"] = {"S": self.source}

        if self.exception is not None:
            item["exception"] = {"S": self.exception}

        return item

    def __repr__(self) -> str:
        """Returns a string representation of the JobLog object.

        Returns:
            str: A string representation of the JobLog object.
        """
        return (
            "JobLog("
            f"job_id={_repr_str(self.job_id)}, "
            f"timestamp={_repr_str(self.timestamp)}, "
            f"log_level={_repr_str(self.log_level)}, "
            f"message={_repr_str(self.message)}, "
            f"source={_repr_str(self.source)}, "
            f"exception={_repr_str(self.exception)}"
            ")"
        )

    def __iter__(self) -> Generator[Tuple[str, Any], None, None]:
        """Returns an iterator over the JobLog object's attributes.

        Returns:
            Generator[Tuple[str, Any], None, None]: An iterator over the
                JobLog object's attribute name/value pairs.
        """
        yield "job_id", self.job_id
        yield "timestamp", self.timestamp
        yield "log_level", self.log_level
        yield "message", self.message
        yield "source", self.source
        yield "exception", self.exception

    def __hash__(self) -> int:
        """Returns the hash value of the JobLog object.

        Returns:
            int: The hash value of the JobLog object.
        """
        return hash(
            (
                self.job_id,
                self.timestamp,
                self.log_level,
                self.message,
                self.source,
                self.exception,
            )
        )


def item_to_job_log(item: Dict[str, Any]) -> JobLog:
    """Converts a DynamoDB item to a JobLog object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        JobLog: The JobLog object represented by the DynamoDB item.

    Raises:
        ValueError: When the item format is invalid.
    """
    required_keys = {
        "PK",
        "SK",
        "TYPE",
        "log_level",
        "message",
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

        # Parse timestamp from the SK
        timestamp = item["SK"]["S"].split("#")[1]

        # Extract required fields
        log_level = item["log_level"]["S"]
        message = item["message"]["S"]

        # Extract optional fields
        source = item.get("source", {}).get("S")
        exception = item.get("exception", {}).get("S")

        return JobLog(
            job_id=job_id,
            timestamp=timestamp,
            log_level=log_level,
            message=message,
            source=source,
            exception=exception,
        )
    except KeyError as e:
        raise ValueError(f"Error converting item to JobLog: {e}") from e
