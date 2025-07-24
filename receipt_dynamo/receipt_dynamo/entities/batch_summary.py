from datetime import datetime
from typing import Any, Dict, Generator, Optional, Tuple

from receipt_dynamo.constants import BatchStatus, BatchType
from receipt_dynamo.entities.util import (
    _repr_str,
    assert_type,
    format_type_error,
    normalize_enum,
)


class BatchSummary:
    def __init__(
        self,
        batch_id: str,
        batch_type: str | BatchType,
        openai_batch_id: str,
        submitted_at: str | datetime,
        status: str | BatchStatus,
        result_file_id: str,
        receipt_refs: Optional[list[tuple[str, int]]] = None,
    ):
        assert_type("batch_id", batch_id, str, ValueError)
        self.batch_id = batch_id

        self.batch_type = normalize_enum(batch_type, BatchType)

        assert_type("openai_batch_id", openai_batch_id, str, ValueError)
        self.openai_batch_id = openai_batch_id

        if isinstance(submitted_at, str):
            try:
                submitted_at = datetime.fromisoformat(submitted_at)
            except ValueError:
                raise ValueError(
                    f"submitted_at must be a datetime object or a string in "
                    f"ISO format; got {submitted_at}"
                )
        elif not isinstance(submitted_at, datetime):
            raise ValueError(
                format_type_error(
                    "submitted_at", submitted_at, (datetime, str)
                )
            )
        self.submitted_at = submitted_at

        self.status = normalize_enum(status, BatchStatus)

        assert_type("result_file_id", result_file_id, str, ValueError)
        self.result_file_id = result_file_id

        if not isinstance(receipt_refs, list):
            raise ValueError(
                "receipt_refs must be a list of (image_id, receipt_id) tuples"
            )
        self.receipt_refs = receipt_refs

    @property
    def key(self) -> Dict[str, Any]:
        return {
            "PK": {"S": f"BATCH#{self.batch_id}"},
            "SK": {"S": "STATUS"},
        }

    def gsi1_key(self) -> Dict[str, Any]:
        return {
            "GSI1PK": {"S": f"STATUS#{self.status}"},
            "GSI1SK": {
                "S": f"BATCH_TYPE#{self.batch_type}#BATCH_ID#{self.batch_id}"
            },
        }

    def to_item(self) -> Dict[str, Any]:
        return {
            **self.key,
            **self.gsi1_key(),
            "TYPE": {"S": "BATCH_SUMMARY"},
            "batch_type": {"S": self.batch_type},
            "openai_batch_id": {"S": self.openai_batch_id},
            "submitted_at": {"S": self.submitted_at.isoformat()},
            "status": {"S": self.status},
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
            f"submitted_at={_repr_str(self.submitted_at.isoformat())}, "
            f"status={_repr_str(self.status)}, "
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
        yield "submitted_at", self.submitted_at.isoformat()
        yield "status", self.status
        yield "result_file_id", self.result_file_id
        yield "receipt_refs", self.receipt_refs

    def to_dict(self) -> Dict[str, Any]:
        """Return a dictionary representation of the BatchSummary."""
        return {k: v for k, v in self}

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
                self.result_file_id,
                tuple(self.receipt_refs),
            )
        )


def item_to_batch_summary(item: Dict[str, Any]) -> BatchSummary:
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
        "result_file_id",
        "receipt_refs",
    }
    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - item.keys()
        additional_keys = item.keys() - required_keys
        raise ValueError(
            f"Invalid item format\nmissing keys: {missing_keys}\n"
            f"additional keys: {additional_keys}"
        )
    try:
        batch_id = item["PK"]["S"].split("#")[1]
        batch_type = item["batch_type"]["S"]
        openai_batch_id = item["openai_batch_id"]["S"]
        submitted_at = datetime.fromisoformat(item["submitted_at"]["S"])
        status = item["status"]["S"]
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
            result_file_id=result_file_id,
            receipt_refs=receipt_refs,
        )
    except Exception as e:
        raise ValueError(f"Error converting item to BatchSummary: {e}")
