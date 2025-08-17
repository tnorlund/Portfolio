from dataclasses import dataclass
from typing import Any, Dict, Generator, Optional, Tuple

from receipt_dynamo.entities.dynamodb_utils import (
    dict_to_dynamodb_map,
    parse_dynamodb_map,
    parse_dynamodb_value,
    to_dynamodb_value,
)
from receipt_dynamo.entities.util import _repr_str, assert_valid_uuid


@dataclass(eq=True, unsafe_hash=False)
class JobCheckpoint:
    """
    Represents a model checkpoint for a training job stored in S3 and
    tracked in DynamoDB.

    This class tracks model checkpoints created during training jobs.
    It stores their location in S3, metadata about the checkpoint, and
    training metrics at the time the checkpoint was created.

    Attributes:
        job_id (str): UUID identifying the job.
        timestamp (str): Timestamp when the checkpoint was created. Used as
            unique identifier.
        s3_bucket (str): S3 bucket where the checkpoint is stored.
        s3_key (str): S3 key (path) where the checkpoint is stored.
        size_bytes (int): Size of the checkpoint file in bytes.
        model_state (bool): Whether the checkpoint includes model state.
        optimizer_state (bool): Whether the checkpoint includes optimizer
            state.
        metrics (Dict): Key-value pairs of metrics at the time of checkpoint.
        step (int): Training step when the checkpoint was created.
        epoch (int): Training epoch when the checkpoint was created.
        is_best (bool): Whether this is the best checkpoint for the job so far.
    """

    job_id: str
    timestamp: str
    s3_bucket: str
    s3_key: str
    size_bytes: int
    step: int
    epoch: int
    model_state: bool = True
    optimizer_state: bool = True
    metrics: Optional[Dict[str, Any]] = None
    is_best: bool = False

    def __post_init__(self):
        """Validates fields after dataclass initialization.

        Raises:
            ValueError: If any parameter is of an invalid type or has an
                invalid value.
        """
        assert_valid_uuid(self.job_id)

        if not isinstance(self.timestamp, str) or not self.timestamp:
            raise ValueError("timestamp must be a non-empty string")

        if not isinstance(self.s3_bucket, str) or not self.s3_bucket:
            raise ValueError("s3_bucket must be a non-empty string")

        if not isinstance(self.s3_key, str) or not self.s3_key:
            raise ValueError("s3_key must be a non-empty string")

        if not isinstance(self.size_bytes, int) or self.size_bytes < 0:
            raise ValueError("size_bytes must be a non-negative integer")

        if not isinstance(self.step, int) or self.step < 0:
            raise ValueError("step must be a non-negative integer")

        if not isinstance(self.epoch, int) or self.epoch < 0:
            raise ValueError("epoch must be a non-negative integer")

        if not isinstance(self.model_state, bool):
            raise ValueError("model_state must be a boolean")

        if not isinstance(self.optimizer_state, bool):
            raise ValueError("optimizer_state must be a boolean")

        if self.metrics is not None and not isinstance(self.metrics, dict):
            raise ValueError("metrics must be a dictionary")
        if self.metrics is None:
            self.metrics = {}

        if not isinstance(self.is_best, bool):
            raise ValueError("is_best must be a boolean")

    @property
    def key(self) -> Dict[str, Any]:
        """Generates the primary key for the job checkpoint.

        Returns:
            dict: The primary key for the job checkpoint.
        """
        return {
            "PK": {"S": f"JOB#{self.job_id}"},
            "SK": {"S": f"CHECKPOINT#{self.timestamp}"},
        }

    def gsi1_key(self) -> Dict[str, Any]:
        """Generates the GSI1 key for the job checkpoint.

        Returns:
            dict: The GSI1 key for the job checkpoint.
        """
        return {
            "GSI1PK": {"S": "CHECKPOINT"},
            "GSI1SK": {"S": f"JOB#{self.job_id}#{self.timestamp}"},
        }

    def to_item(self) -> Dict[str, Any]:
        """Converts the JobCheckpoint object to a DynamoDB item.

        Returns:
            dict: A dictionary representing the JobCheckpoint object as a
                DynamoDB item.
        """
        item = {
            **self.key,
            **self.gsi1_key(),
            "TYPE": {"S": "JOB_CHECKPOINT"},
            "job_id": {"S": self.job_id},
            "timestamp": {"S": self.timestamp},
            "s3_bucket": {"S": self.s3_bucket},
            "s3_key": {"S": self.s3_key},
            "size_bytes": {"N": str(self.size_bytes)},
            "step": {"N": str(self.step)},
            "epoch": {"N": str(self.epoch)},
            "model_state": {"BOOL": self.model_state},
            "optimizer_state": {"BOOL": self.optimizer_state},
            "is_best": {"BOOL": self.is_best},
        }

        if self.metrics:
            item["metrics"] = {"M": dict_to_dynamodb_map(self.metrics)}

        return item

    def __repr__(self) -> str:
        """Returns a string representation of the JobCheckpoint object.

        Returns:
            str: A string representation of the JobCheckpoint object.
        """
        return (
            "JobCheckpoint("
            f"job_id={_repr_str(self.job_id)}, "
            f"timestamp={_repr_str(self.timestamp)}, "
            f"s3_bucket={_repr_str(self.s3_bucket)}, "
            f"s3_key={_repr_str(self.s3_key)}, "
            f"size_bytes={self.size_bytes}, "
            f"step={self.step}, "
            f"epoch={self.epoch}, "
            f"model_state={self.model_state}, "
            f"optimizer_state={self.optimizer_state}, "
            f"is_best={self.is_best}, "
            f"metrics={self.metrics}"
            ")"
        )

    def __iter__(self) -> Generator[Tuple[str, Any], None, None]:
        """Returns an iterator over the JobCheckpoint object's attributes.

        Returns:
            Generator[Tuple[str, Any], None, None]:
                An iterator over the JobCheckpoint object's attribute
                name/value pairs.
        """
        yield "job_id", self.job_id
        yield "timestamp", self.timestamp
        yield "s3_bucket", self.s3_bucket
        yield "s3_key", self.s3_key
        yield "size_bytes", self.size_bytes
        yield "step", self.step
        yield "epoch", self.epoch
        yield "model_state", self.model_state
        yield "optimizer_state", self.optimizer_state
        yield "metrics", self.metrics
        yield "is_best", self.is_best

    def __hash__(self) -> int:
        """Returns the hash value of the JobCheckpoint object.

        Returns:
            int: The hash value of the JobCheckpoint object.
        """
        return hash(
            (
                self.job_id,
                self.timestamp,
                self.s3_bucket,
                self.s3_key,
                self.size_bytes,
                self.step,
                self.epoch,
                self.model_state,
                self.optimizer_state,
                # Can't hash dict, so we don't include metrics
                self.is_best,
            )
        )

    def _dict_to_dynamodb_map(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Converts a Python dictionary to a DynamoDB map format.

        This is a wrapper around the imported dict_to_dynamodb_map function
        to maintain backward compatibility with tests.

        Args:
            data: The Python dictionary to convert

        Returns:
            The dictionary in DynamoDB map format
        """
        return dict_to_dynamodb_map(data)


def item_to_job_checkpoint(item: Dict[str, Any]) -> JobCheckpoint:
    """Converts a DynamoDB item to a JobCheckpoint object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        JobCheckpoint: The JobCheckpoint object represented by the
            DynamoDB item.

    Raises:
        ValueError: If the DynamoDB item cannot be converted to a
            JobCheckpoint.
    """
    try:
        metrics: Dict[str, Any] = {}
        if "metrics" in item and "M" in item["metrics"]:
            metrics = parse_dynamodb_map(item["metrics"]["M"])

        size_bytes = int(item["size_bytes"]["N"])
        step = int(item["step"]["N"])
        epoch = int(item["epoch"]["N"])

        return JobCheckpoint(
            job_id=item["job_id"]["S"],
            timestamp=item["timestamp"]["S"],
            s3_bucket=item["s3_bucket"]["S"],
            s3_key=item["s3_key"]["S"],
            size_bytes=size_bytes,
            step=step,
            epoch=epoch,
            model_state=item["model_state"]["BOOL"],
            optimizer_state=item["optimizer_state"]["BOOL"],
            metrics=metrics,
            is_best=item["is_best"]["BOOL"],
        )
    except (KeyError, ValueError) as e:
        raise ValueError(f"Error converting item to JobCheckpoint: {e}") from e
