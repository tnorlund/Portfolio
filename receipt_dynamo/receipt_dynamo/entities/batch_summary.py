from datetime import datetime
from typing import Optional, Generator, Tuple, Any

from receipt_dynamo.constants import BatchStatus, BatchType
from receipt_dynamo.entities.util import _repr_str


class BatchSummary:
    def __init__(
        self,
        batch_id: str,
        batch_type: str,
        openai_batch_id: str,
        submitted_at: datetime,
        status: str | BatchStatus,
        word_count: int,
        result_file_id: str,
        receipt_refs: list[tuple[str, int]] = None,
    ):
        if not isinstance(batch_id, str):
            raise ValueError("batch_id must be a string")
        self.batch_id = batch_id

        # Accept batch_type as either BatchType or str
        if isinstance(batch_type, BatchType):
            batch_type_str = batch_type.value
        elif isinstance(batch_type, str):
            batch_type_str = batch_type
        else:
            raise ValueError(
                f"batch_type must be either a BatchType enum or a string; got {type(batch_type).__name__}"
            )

        # Validate batch_type_str against allowed values
        valid_types = [t.value for t in BatchType]
        if batch_type_str not in valid_types:
            raise ValueError(
                f"Invalid batch type: {batch_type_str} must be one of {', '.join(valid_types)}"
            )
        self.batch_type = batch_type_str

        if not isinstance(openai_batch_id, str):
            raise ValueError("openai_batch_id must be a string")
        self.openai_batch_id = openai_batch_id

        if not isinstance(submitted_at, datetime):
            raise ValueError("submitted_at must be a datetime object")
        self.submitted_at = submitted_at

        # Accept status as either a BatchStatus enum or a string
        if isinstance(status, BatchStatus):
            status_str = status.value
        elif isinstance(status, str):
            status_str = status
        else:
            raise ValueError(
                f"status must be either a BatchStatus enum or a string; got {type(status).__name__}"
            )

        # Validate the string against allowed values
        valid_statuses = [s.value for s in BatchStatus]
        if status_str not in valid_statuses:
            raise ValueError(
                f"Invalid status: {status_str} must be one of {', '.join(valid_statuses)}"
            )
        self.status = status_str

        if not isinstance(word_count, int):
            raise ValueError("word_count must be an integer")
        self.word_count = word_count

        if not isinstance(result_file_id, str):
            raise ValueError("result_file_id must be a string")
        self.result_file_id = result_file_id

        if not isinstance(receipt_refs, list):
            raise ValueError(
                "receipt_refs must be a list of (image_id, receipt_id) tuples"
            )
        self.receipt_refs = receipt_refs

    def key(self) -> dict:
        return {
            "PK": {"S": f"BATCH#{self.batch_id}"},
            "SK": {"S": f"STATUS"},
        }

    def gsi1_key(self) -> dict:
        return {
            "GSI1PK": {"S": f"STATUS#{self.status}"},
            "GSI1SK": {"S": f"BATCH#{self.batch_id}"},
        }

    def to_item(self) -> dict:
        return {
            **self.key(),
            **self.gsi1_key(),
            "TYPE": {"S": "BATCH_SUMMARY"},
            "batch_type": {"S": self.batch_type},
            "openai_batch_id": {"S": self.openai_batch_id},
            "submitted_at": {"S": self.submitted_at.isoformat()},
            "status": {"S": self.status},
            "word_count": {"N": str(self.word_count)},
            "result_file_id": {"S": self.result_file_id},
            "receipt_refs": {
                "L": [
                    {
                        "M": {
                            "image_id": {"S": image_id},
                            "receipt_id": {"N": str(receipt_id)},
                        }
                    }
                    for image_id, receipt_id in self.receipt_refs
                ]
            },
        }

    def __repr__(self) -> str:
        """Returns a string representation of the BatchSummary object.

        Returns:
            str: A string representation of the BatchSummary object.
        """
        return (
            "BatchSummary("
            f"batch_id={_repr_str(self.batch_id)}, "
            f"batch_type={_repr_str(self.batch_type)}, "
            f"openai_batch_id={_repr_str(self.openai_batch_id)}, "
            f"submitted_at={_repr_str(self.submitted_at)}, "
            f"status={_repr_str(self.status)}, "
            f"word_count={self.word_count}, "
            f"result_file_id={_repr_str(self.result_file_id)}, "
            f"receipt_refs={self.receipt_refs}"
            ")"
        )

    def __str__(self) -> str:
        return self.__repr__()

    def __iter__(self) -> Generator[Tuple[str, Any], None, None]:
        yield "batch_id", self.batch_id
        yield "batch_type", self.batch_type
        yield "openai_batch_id", self.openai_batch_id
        yield "submitted_at", self.submitted_at
        yield "status", self.status
        yield "word_count", self.word_count
        yield "result_file_id", self.result_file_id
        yield "receipt_refs", self.receipt_refs

    def __eq__(self, other) -> bool:
        """Determines whether two BatchSummary objects are equal.

        Args:
            other (BatchSummary): The other BatchSummary object to compare.

        Returns:
            bool: True if the BatchSummary objects are equal, False otherwise.
        """
        if not isinstance(other, BatchSummary):
            return False
        return (
            self.batch_id == other.batch_id
            and self.batch_type == other.batch_type
            and self.openai_batch_id == other.openai_batch_id
            and self.submitted_at == other.submitted_at
            and self.status == other.status
            and self.word_count == other.word_count
            and self.result_file_id == other.result_file_id
            and self.receipt_refs == other.receipt_refs
        )

    def __hash__(self) -> int:
        return hash(
            (
                self.batch_id,
                self.batch_type,
                self.openai_batch_id,
                self.submitted_at,
                self.status,
                self.word_count,
                self.result_file_id,
                tuple(self.receipt_refs),
            )
        )


def itemToBatchSummary(item: dict) -> BatchSummary:
    """Converts a DynamoDB item to a BatchSummary object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        BatchSummary: The BatchSummary object.

    Raises:
        ValueError: When the item format is invalid.
    """
    required_keys = {
        "PK",
        "SK",
        "batch_type",
        "openai_batch_id",
        "submitted_at",
        "status",
        "word_count",
        "result_file_id",
        "receipt_refs",
    }
    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - item.keys()
        additional_keys = item.keys() - required_keys
        raise ValueError(
            f"Invalid item format\nmissing keys: {missing_keys}\nadditional keys: {additional_keys}"
        )
    try:
        batch_id = item["PK"]["S"].split("#")[1]
        batch_type = item["batch_type"]["S"]
        openai_batch_id = item["openai_batch_id"]["S"]
        submitted_at = datetime.fromisoformat(item["submitted_at"]["S"])
        status = item["status"]["S"]
        word_count = int(item["word_count"]["N"])
        result_file_id = item["result_file_id"]["S"]
        receipt_refs = [
            (ref["M"]["image_id"]["S"], int(ref["M"]["receipt_id"]["N"]))
            for ref in item["receipt_refs"]["L"]
        ]
        return BatchSummary(
            batch_id=batch_id,
            batch_type=batch_type,
            openai_batch_id=openai_batch_id,
            submitted_at=submitted_at,
            status=status,
            word_count=word_count,
            result_file_id=result_file_id,
            receipt_refs=receipt_refs,
        )
    except Exception as e:
        raise ValueError(f"Error converting item to BatchSummary: {e}")
