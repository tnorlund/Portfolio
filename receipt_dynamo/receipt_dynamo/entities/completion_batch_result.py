from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Generator, Tuple

from receipt_dynamo.constants import BatchStatus
from receipt_dynamo.entities.util import (
    _repr_str,
    assert_type,
    assert_valid_uuid,
    format_type_error,
)


@dataclass(eq=True, unsafe_hash=False)
class CompletionBatchResult:
    """
    A completion batch result is a result of a completion batch.
    """

    batch_id: str
    image_id: str
    receipt_id: int
    line_id: int
    word_id: int
    original_label: str
    gpt_suggested_label: str | None
    status: str | BatchStatus
    validated_at: datetime

    def __post_init__(self):
        assert_valid_uuid(self.batch_id)

        assert_valid_uuid(self.image_id)

        assert_type("receipt_id", self.receipt_id, int, ValueError)

        assert_type("line_id", self.line_id, int, ValueError)

        assert_type("word_id", self.word_id, int, ValueError)

        assert_type("original_label", self.original_label, str, ValueError)

        if not isinstance(self.gpt_suggested_label, str | None):
            raise ValueError(
                format_type_error(
                    "gpt_suggested_label",
                    self.gpt_suggested_label,
                    (str, type(None)),
                )
            )

        if not isinstance(self.status, str | BatchStatus):
            raise ValueError(
                format_type_error("status", self.status, (str, BatchStatus))
            )
        if isinstance(self.status, str) and self.status not in [
            s.value for s in BatchStatus
        ]:
            raise ValueError(
                "status must be one of: " + ", ".join(s.value for s in BatchStatus)
            )
        if isinstance(self.status, BatchStatus):
            status_str = self.status.value
        else:
            status_str = self.status
        self.status = status_str

        assert_type("validated_at", self.validated_at, datetime, ValueError)

    @property
    def key(self) -> Dict[str, Any]:
        """
        The key for the completion batch result is a composite key that
        consists of the batch id and the receipt id, line id, and word id.

        PK: BATCH#<batch_id>
        SK: RESULT#RECEIPT#<receipt_id>#LINE#<line_id>#WORD#<word_id>

        Returns:
            dict: The key for the completion batch result.
        """
        return {
            "PK": {"S": f"BATCH#{self.batch_id}"},
            "SK": {
                "S": (
                    f"RESULT#RECEIPT#{self.receipt_id}#LINE#{self.line_id}#"
                    f"WORD#{self.word_id}#LABEL#{self.original_label}"
                )
            },
        }

    @property
    def gsi1_key(self) -> Dict[str, Any]:
        return {
            "GSI1PK": {"S": f"LABEL#{self.original_label}"},
            "GSI1SK": {"S": f"STATUS#{self.status}"},
        }

    @property
    def gsi2_key(self) -> Dict[str, Any]:
        return {
            "GSI2PK": {"S": f"BATCH#{self.batch_id}"},
            "GSI2SK": {"S": f"STATUS#{self.status}"},
        }

    @property
    def gsi3_key(self) -> Dict[str, Any]:
        return {
            "GSI3PK": {"S": f"IMAGE#{self.image_id}#RECEIPT#{self.receipt_id}"},
            "GSI3SK": {"S": f"BATCH#{self.batch_id}#STATUS#{self.status}"},
        }

    def to_item(self) -> Dict[str, Any]:
        """
        Converts the completion batch result to an item for DynamoDB.
        """
        return {
            **self.key,
            **self.gsi1_key,
            **self.gsi2_key,
            **self.gsi3_key,
            "TYPE": {"S": "COMPLETION_BATCH_RESULT"},
            "original_label": {"S": self.original_label},
            "gpt_suggested_label": (
                {"S": self.gpt_suggested_label}
                if self.gpt_suggested_label
                else {"NULL": True}
            ),
            "status": {"S": self.status},
            "validated_at": {"S": self.validated_at.isoformat()},
        }

    def __repr__(self) -> str:
        """
        Returns a string representation of the completion batch result.
        """
        return (
            "CompletionBatchResult("
            f"batch_id={_repr_str(self.batch_id)}, "
            f"image_id={_repr_str(self.image_id)}, "
            f"receipt_id={_repr_str(self.receipt_id)}, "
            f"line_id={_repr_str(self.line_id)}, "
            f"word_id={_repr_str(self.word_id)}, "
            f"original_label={_repr_str(self.original_label)}, "
            f"gpt_suggested_label={_repr_str(self.gpt_suggested_label)}, "
            f"status={_repr_str(self.status)}, "
            f"validated_at={_repr_str(self.validated_at)}, "
            ")"
        )

    def __str__(self) -> str:
        return self.__repr__()

    def __iter__(self) -> Generator[Tuple[str, Any], None, None]:
        yield "batch_id", self.batch_id
        yield "image_id", self.image_id
        yield "receipt_id", self.receipt_id
        yield "line_id", self.line_id
        yield "word_id", self.word_id
        yield "original_label", self.original_label
        yield "gpt_suggested_label", self.gpt_suggested_label
        yield "status", self.status
        yield "validated_at", self.validated_at

    # __eq__ is automatically generated by dataclass(eq=True)

    def __hash__(self) -> int:
        return hash(
            (
                self.batch_id,
                self.image_id,
                self.receipt_id,
                self.line_id,
                self.word_id,
                self.original_label,
                self.gpt_suggested_label,
                self.status,
                self.validated_at,
            )
        )


def item_to_completion_batch_result(
    item: Dict[str, Any],
) -> CompletionBatchResult:
    """
    Converts an item from DynamoDB to a CompletionBatchResult object.
    """
    required_keys = {
        "PK",
        "SK",
        "original_label",
        "gpt_suggested_label",
        "status",
        "validated_at",
    }
    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - item.keys()
        additional_keys = item.keys() - required_keys
        raise ValueError(
            f"Invalid item format\nmissing keys: {missing_keys}\n"
            f"additional keys: {additional_keys}"
        )
    try:
        batch_id = item["PK"]["S"].split("#")[1]  # From PK="BATCH#{batch_id}"
        sk_parts = item["SK"]["S"].split("#")
        receipt_id = int(sk_parts[2])
        line_id = int(sk_parts[4])
        word_id = int(sk_parts[6])
        original_label = sk_parts[8]
        gpt_suggested_label = (
            item["gpt_suggested_label"]["S"]
            if item["gpt_suggested_label"]["S"]
            else None
        )
        status = item["status"]["S"]
        validated_at = datetime.fromisoformat(item["validated_at"]["S"])
        image_id = item["GSI3PK"]["S"].split("#")[1]
        return CompletionBatchResult(
            batch_id=batch_id,
            image_id=image_id,
            receipt_id=receipt_id,
            line_id=line_id,
            word_id=word_id,
            original_label=original_label,
            gpt_suggested_label=gpt_suggested_label,
            status=status,
            validated_at=validated_at,
        )
    except Exception as e:
        raise ValueError(f"Error converting item to CompletionBatchResult: {e}") from e
