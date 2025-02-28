from typing import Any, Dict, Generator, List, Optional, Tuple, Union
from datetime import datetime
from receipt_dynamo.entities.util import assert_valid_uuid, _repr_str


class JobMetric:
    """
    Represents a metric associated with a training job stored in a DynamoDB table.

    This class stores metrics data collected during job execution, such as loss values,
    accuracy, throughput, or any other measurable performance indicators.

    Attributes:
        job_id (str): UUID identifying the job.
        metric_name (str): Name of the metric (e.g., 'loss', 'accuracy', 'throughput').
        value (Union[float, int, str]): The value of the metric.
        timestamp (str): ISO format timestamp when the metric was recorded.
        unit (Optional[str]): Unit of measurement for the metric (e.g., 'seconds', 'percent').
        step (Optional[int]): Training step when the metric was recorded.
        epoch (Optional[int]): Training epoch when the metric was recorded.
    """

    def __init__(
        self,
        job_id: str,
        metric_name: str,
        value: Union[float, int, str],
        timestamp: Union[datetime, str],
        unit: Optional[str] = None,
        step: Optional[int] = None,
        epoch: Optional[int] = None,
    ):
        """Initializes a new JobMetric object for DynamoDB.

        Args:
            job_id (str): UUID identifying the job.
            metric_name (str): Name of the metric.
            value (Union[float, int, str]): The value of the metric.
            timestamp (Union[datetime, str]): When the metric was recorded.
            unit (Optional[str]): Unit of measurement for the metric.
            step (Optional[int]): Training step when the metric was recorded.
            epoch (Optional[int]): Training epoch when the metric was recorded.

        Raises:
            ValueError: If any parameter is of an invalid type or has an invalid value.
        """
        assert_valid_uuid(job_id)
        self.job_id = job_id

        if not isinstance(metric_name, str) or not metric_name:
            raise ValueError("metric_name must be a non-empty string")
        self.metric_name = metric_name

        if not isinstance(value, (float, int, str)):
            raise ValueError("value must be a number or string")
        self.value = value

        if isinstance(timestamp, datetime):
            self.timestamp = timestamp.isoformat()
        elif isinstance(timestamp, str):
            self.timestamp = timestamp
        else:
            raise ValueError("timestamp must be a datetime object or a string")

        if unit is not None and not isinstance(unit, str):
            raise ValueError("unit must be a string if provided")
        self.unit = unit

        if step is not None:
            if not isinstance(step, int) or step < 0:
                raise ValueError("step must be a non-negative integer if provided")
        self.step = step

        if epoch is not None:
            if not isinstance(epoch, int) or epoch < 0:
                raise ValueError("epoch must be a non-negative integer if provided")
        self.epoch = epoch

    def key(self) -> dict:
        """Generates the primary key for the job metric.

        Returns:
            dict: The primary key for the job metric.
        """
        return {
            "PK": {"S": f"JOB#{self.job_id}"},
            "SK": {"S": f"METRIC#{self.metric_name}#{self.timestamp}"},
        }

    def gsi1_key(self) -> dict:
        """Generates the GSI1 key for the job metric.

        Returns:
            dict: The GSI1 key for the job metric.
        """
        return {
            "GSI1PK": {"S": "METRIC"},
            "GSI1SK": {"S": f"METRIC#{self.metric_name}"},
        }

    def to_item(self) -> dict:
        """Converts the JobMetric object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the JobMetric object as a DynamoDB item.
        """
        item = {
            **self.key(),
            **self.gsi1_key(),
            "TYPE": {"S": "JOB_METRIC"},
            "job_id": {"S": self.job_id},
            "metric_name": {"S": self.metric_name},
            "timestamp": {"S": self.timestamp},
        }

        # Handle different value types
        if isinstance(self.value, str):
            item["value"] = {"S": self.value}
        elif isinstance(self.value, (int, float)):
            item["value"] = {"N": str(self.value)}

        # Add optional attributes if they exist
        if self.unit is not None:
            item["unit"] = {"S": self.unit}

        if self.step is not None:
            item["step"] = {"N": str(self.step)}

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
            f"value={self.value}, "
            f"timestamp={_repr_str(self.timestamp)}, "
            f"unit={_repr_str(self.unit)}, "
            f"step={self.step}, "
            f"epoch={self.epoch}"
            ")"
        )

    def __iter__(self) -> Generator[Tuple[str, Any], None, None]:
        """Returns an iterator over the JobMetric object's attributes.

        Returns:
            Generator[Tuple[str, Any], None, None]: An iterator over the JobMetric object's attribute name/value pairs.
        """
        yield "job_id", self.job_id
        yield "metric_name", self.metric_name
        yield "value", self.value
        yield "timestamp", self.timestamp
        yield "unit", self.unit
        yield "step", self.step
        yield "epoch", self.epoch

    def __eq__(self, other) -> bool:
        """Determines whether two JobMetric objects are equal.

        Args:
            other (JobMetric): The other JobMetric object to compare.

        Returns:
            bool: True if the JobMetric objects are equal, False otherwise.
        """
        if not isinstance(other, JobMetric):
            return False
        return (
            self.job_id == other.job_id
            and self.metric_name == other.metric_name
            and self.value == other.value
            and self.timestamp == other.timestamp
            and self.unit == other.unit
            and self.step == other.step
            and self.epoch == other.epoch
        )

    def __hash__(self) -> int:
        """Returns the hash value of the JobMetric object.

        Returns:
            int: The hash value of the JobMetric object.
        """
        return hash(
            (
                self.job_id,
                self.metric_name,
                self.value if not isinstance(self.value, float) else str(self.value),
                self.timestamp,
                self.unit,
                self.step,
                self.epoch,
            )
        )


def itemToJobMetric(item: dict) -> JobMetric:
    """Converts a DynamoDB item to a JobMetric object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        JobMetric: The JobMetric object represented by the DynamoDB item.

    Raises:
        ValueError: When the item format is invalid.
    """
    required_keys = {
        "PK",
        "SK",
        "TYPE",
        "job_id",
        "metric_name",
        "value",
        "timestamp",
    }
    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - item.keys()
        additional_keys = item.keys() - required_keys
        raise ValueError(
            f"Invalid item format\nmissing keys: {missing_keys}\nadditional keys: {additional_keys}"
        )

    try:
        # Extract basic fields
        job_id = item["job_id"]["S"]
        metric_name = item["metric_name"]["S"]
        timestamp = item["timestamp"]["S"]

        # Extract value based on its type
        if "S" in item["value"]:
            value = item["value"]["S"]
        elif "N" in item["value"]:
            try:
                value = int(item["value"]["N"])
            except ValueError:
                value = float(item["value"]["N"])
        else:
            # Instead of raising directly, throw a KeyError that will be caught below
            # This ensures consistent error messaging
            raise KeyError(f"Unsupported value type: {item['value']}")

        # Parse optional fields
        unit = item["unit"]["S"] if "unit" in item else None
        step = int(item["step"]["N"]) if "step" in item else None
        epoch = int(item["epoch"]["N"]) if "epoch" in item else None

        return JobMetric(
            job_id=job_id,
            metric_name=metric_name,
            value=value,
            timestamp=timestamp,
            unit=unit,
            step=step,
            epoch=epoch,
        )
    except (KeyError, ValueError) as e:
        # Catch both KeyError and ValueError to ensure consistent messaging
        raise ValueError(f"Error converting item to JobMetric: {e}")


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


def _parse_dynamodb_map(dynamodb_map: Dict) -> Dict:
    """Parses a DynamoDB map to a Python dictionary.

    Args:
        dynamodb_map (Dict): The DynamoDB map to parse.

    Returns:
        Dict: The parsed Python dictionary.
    """
    result = {}
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
