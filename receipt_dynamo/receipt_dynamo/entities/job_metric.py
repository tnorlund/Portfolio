import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Generator, Optional, Tuple, Union

from receipt_dynamo.entities.dynamodb_utils import (
    dict_to_dynamodb_map,
    parse_dynamodb_map,
    parse_dynamodb_value,
    to_dynamodb_value,
)
from receipt_dynamo.entities.util import (
    _repr_str,
    assert_type,
    assert_valid_uuid,
    format_type_error,
)


@dataclass(eq=True, unsafe_hash=False)
class JobMetric:
    """
    Represents a metric recorded during a training job stored in a DynamoDB
    table.

    This class is used to track metrics such as loss, accuracy, or any other
    numerical measurements recorded during the execution of a training job.

    Attributes:
        job_id (str): UUID identifying the job.
        metric_name (str): Name of the metric (e.g., 'loss', 'accuracy').
        timestamp (str): ISO-formatted timestamp when the metric was recorded.
        value (Union[float, Dict]): The value of the metric (may be a simple
            number or a complex structure).
        unit (Optional[str]): The unit of the metric (e.g., 'percent',
            'seconds').
        step (Optional[int]): The training step at which the metric was
            recorded.
        epoch (Optional[int]): The training epoch at which the metric was
            recorded.
    """

    job_id: str
    metric_name: str
    timestamp: str
    value: Union[int, float, Dict[str, Any]]
    unit: Optional[str] = None
    step: Optional[int] = None
    epoch: Optional[int] = None

    def __post_init__(self):
        """Validates fields after dataclass initialization.

        Raises:
            ValueError: If any parameter is of an invalid type or has an
                invalid value.
        """
        assert_valid_uuid(self.job_id)

        assert_type("metric_name", self.metric_name, str, ValueError)
        if not self.metric_name:
            raise ValueError("metric_name must be a non-empty string")

        # Handle timestamp conversion
        if isinstance(self.timestamp, datetime):
            self.timestamp = self.timestamp.isoformat()
        elif not isinstance(self.timestamp, str):
            raise ValueError(
                format_type_error("timestamp", self.timestamp, (datetime, str))
            )

        if not isinstance(self.value, (float, int, dict)):
            try:
                # Try to convert to float if possible
                self.value = float(self.value)
            except (ValueError, TypeError) as e:
                # If not convertible to float and not a dict, we raise an error
                if not isinstance(self.value, dict):
                    raise ValueError(
                        "value must be a number (int/float) or a dictionary"
                    ) from e

    @property
    def key(self) -> Dict[str, Any]:
        """Generates the primary key for the job metric.

        Returns:
            dict: The primary key for the job metric.
        """
        return {
            "PK": {"S": f"JOB#{self.job_id}"},
            "SK": {"S": f"METRIC#{self.metric_name}#{self.timestamp}"},
        }

    def gsi1_key(self) -> Dict[str, Any]:
        """
        Generate a Global Secondary Index (GSI) key for the job metric.

        Returns:
            dict: The GSI key mapping.
        """
        return {
            "GSI1PK": {"S": f"METRIC#{self.metric_name}"},
            "GSI1SK": {"S": f"{self.timestamp}"},
        }

    def gsi2_key(self) -> Dict[str, Any]:
        """
        Generate a second Global Secondary Index (GSI2) key for the job
        metric. This enables efficient comparison of the same metric across
        different jobs.

        Returns:
            dict: The GSI2 key mapping.
        """
        return {
            "GSI2PK": {"S": f"METRIC#{self.metric_name}"},
            "GSI2SK": {"S": f"JOB#{self.job_id}#{self.timestamp}"},
        }

    def to_item(self) -> Dict[str, Any]:
        """Converts the JobMetric object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the JobMetric object as a DynamoDB
                item.
        """
        item = {
            **self.key,
            **self.gsi1_key(),
            **self.gsi2_key(),
            "TYPE": {"S": "JOB_METRIC"},
            "job_id": {"S": self.job_id},
            "metric_name": {"S": self.metric_name},
            "timestamp": {"S": self.timestamp},
        }

        # Handle value based on type
        if isinstance(self.value, (int, float)):
            item["value"] = {"N": str(self.value)}
        elif isinstance(self.value, dict):
            item["value"] = {"M": dict_to_dynamodb_map(self.value)}
        else:
            # This should not happen due to validation in __init__, but just in
            # case
            item["value"] = {"S": json.dumps(self.value)}

        # Add unit if provided
        if self.unit is not None:
            item["unit"] = {"S": self.unit}

        # Add step if provided
        if self.step is not None:
            item["step"] = {"N": str(self.step)}

        # Add epoch if provided
        if self.epoch is not None:
            item["epoch"] = {"N": str(self.epoch)}

        return item

    def __repr__(self) -> str:
        """Returns a string representation of the JobMetric object.

        Returns:
            str: A string representation of the JobMetric object.
        """
        return (
            "JobMetric("
            f"job_id={_repr_str(self.job_id)}, "
            f"metric_name={_repr_str(self.metric_name)}, "
            f"timestamp={_repr_str(self.timestamp)}, "
            f"value={self.value}, "
            f"unit={_repr_str(self.unit)}, "
            f"step={self.step}, "
            f"epoch={self.epoch}"
            ")"
        )

    def __iter__(self) -> Generator[Tuple[str, Any], None, None]:
        """Returns an iterator over the JobMetric object's attributes.

        Returns:
            Generator[Tuple[str, Any], None, None]: An iterator over the
                JobMetric object's attribute name/value pairs.
        """
        yield "job_id", self.job_id
        yield "metric_name", self.metric_name
        yield "timestamp", self.timestamp
        yield "value", self.value
        yield "unit", self.unit
        yield "step", self.step
        yield "epoch", self.epoch


    def __hash__(self) -> int:
        """Returns the hash value of the JobMetric object.

        Returns:
            int: The hash value of the JobMetric object.
        """
        # Convert value to string if it's a dict since dicts aren't hashable
        value_for_hash = (
            json.dumps(self.value)
            if isinstance(self.value, dict)
            else self.value
        )

        return hash(
            (
                self.job_id,
                self.metric_name,
                self.timestamp,
                value_for_hash,
                self.unit,
                self.step,
                self.epoch,
            )
        )


def item_to_job_metric(item: Dict[str, Any]) -> JobMetric:
    """Converts a DynamoDB item to a JobMetric object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        JobMetric: The JobMetric object represented by the DynamoDB item.

    Raises:
        ValueError: When the item format is invalid.
    """
    required_keys = {"PK", "SK", "job_id", "metric_name", "timestamp", "value"}
    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - item.keys()
        additional_keys = (
            item.keys()
            - required_keys
            - {
                "GSI1PK",
                "GSI1SK",
                "GSI2PK",
                "GSI2SK",
                "unit",
                "step",
                "epoch",
                "TYPE",
            }
        )
        raise ValueError(
            f"Invalid item format\nmissing keys: {missing_keys}\n"
            f"additional keys: {additional_keys}"
        )

    try:
        job_id = item["job_id"]["S"]
        metric_name = item["metric_name"]["S"]
        timestamp = item["timestamp"]["S"]

        # Parse value based on its type
        value: Union[int, float, Dict[str, Any]]
        if "N" in item["value"]:
            try:
                value = int(item["value"]["N"])
            except ValueError:
                value = float(item["value"]["N"])
        elif "M" in item["value"]:
            value = parse_dynamodb_map(item["value"]["M"])
        elif "S" in item["value"]:
            # Try to parse from JSON string
            try:
                value = json.loads(item["value"]["S"])
            except json.JSONDecodeError:
                value = item["value"]["S"]
        else:
            raise ValueError(f"Unsupported value format: {item['value']}")

        # Parse unit, step, and epoch if present
        unit = item.get("unit", {}).get("S") if "unit" in item else None

        step = None
        if "step" in item and "N" in item["step"]:
            step = int(item["step"]["N"])

        epoch = None
        if "epoch" in item and "N" in item["epoch"]:
            epoch = int(item["epoch"]["N"])

        return JobMetric(
            job_id=job_id,
            metric_name=metric_name,
            timestamp=timestamp,
            value=value,
            unit=unit,
            step=step,
            epoch=epoch,
        )
    except (KeyError, ValueError) as e:
        raise ValueError(f"Error parsing item: {str(e)}") from e
