from typing import Any, Generator, Optional, Tuple

from receipt_dynamo.entities.util import _repr_str, assert_valid_uuid
from receipt_dynamo.constants import EmbeddingStatus
import re


def validate_pinecone_id_format(
    pinecone_id: str, receipt_id: int, line_id: int, word_id: int
) -> bool:
    expected = f"RECEIPT#{receipt_id}#LINE#{line_id}#WORD#{word_id}"
    if pinecone_id != expected:
        return False

    pattern = r"^RECEIPT#\d+#LINE#\d+#WORD#\d+$"
    return bool(re.match(pattern, pinecone_id))


class EmbeddingBatchResult:
    def __init__(
        self,
        batch_id: str,
        image_id: str,
        receipt_id: int,
        line_id: int,
        word_id: int,
        pinecone_id: str,
        status: str,
        text: str,
        label: str,
        error_message: Optional[str] = None,
    ):
        assert_valid_uuid(batch_id)
        self.batch_id = batch_id

        assert_valid_uuid(image_id)
        self.image_id = image_id

        if not isinstance(receipt_id, int):
            raise ValueError("receipt_id must be an integer")
        if receipt_id <= 0:
            raise ValueError("receipt_id must be greater than zero")
        self.receipt_id = receipt_id

        if not isinstance(line_id, int):
            raise ValueError("line_id must be an integer")
        if line_id < 0:
            raise ValueError("line_id must be greater than or equal to zero")
        self.line_id = line_id

        if not isinstance(word_id, int):
            raise ValueError("word_id must be an integer")
        if word_id < 0:
            raise ValueError("word_id must be greater than or equal to zero")
        self.word_id = word_id

        if not validate_pinecone_id_format(
            pinecone_id, receipt_id, line_id, word_id
        ):
            raise ValueError(
                "pinecone_id must be in the format RECEIPT#<receipt_id>#LINE#<line_id>#WORD#<word_id>"
            )
        self.pinecone_id = pinecone_id

        if not isinstance(status, str):
            raise ValueError("status must be a string")
        if status not in [s.value for s in EmbeddingStatus]:
            raise ValueError(
                f"status must be one of: {', '.join(s.value for s in EmbeddingStatus)}"
            )
        self.status = status

        if not isinstance(text, str):
            raise ValueError("text must be a string")
        self.text = text

        if not isinstance(label, str):
            raise ValueError("label must be a string")
        self.label = label

        if error_message is not None and not isinstance(error_message, str):
            raise ValueError("error_message must be a string")
        self.error_message = error_message

    def key(self) -> dict:
        return {
            "PK": {"S": f"BATCH#{self.batch_id}"},
            "SK": {
                "S": f"RESULT#RECEIPT#{self.receipt_id}#LINE#{self.line_id}#WORD#{self.word_id}#LABEL#{self.label}"
            },
        }

    def gsi2_key(self) -> dict:
        return {
            "GSI2PK": {"S": f"BATCH#{self.batch_id}"},
            "GSI2SK": {"S": f"STATUS#{self.status}"},
        }

    def gsi3_key(self) -> dict:
        return {
            "GSI3PK": {"S": f"RECEIPT#{self.receipt_id}"},
            "GSI3SK": {
                "S": f"IMAGE#{self.image_id}#BATCH#{self.batch_id}#STATUS#{self.status}"
            },
        }

    def to_item(self) -> dict:
        return {
            **self.key(),
            **self.gsi2_key(),
            **self.gsi3_key(),
            "TYPE": {"S": "EMBEDDING_BATCH_RESULT"},
            "image_id": {"S": self.image_id},
            "pinecone_id": {"S": self.pinecone_id},
            "text": {"S": self.text},
            "label": {"S": self.label},
            "status": {"S": self.status},
            "error_message": (
                {"S": self.error_message}
                if self.error_message
                else {"NULL": True}
            ),
        }

    def __repr__(self) -> str:
        return (
            "EmbeddingBatchResult("
            f"batch_id={_repr_str(self.batch_id)}, "
            f"image_id={_repr_str(self.image_id)}, "
            f"receipt_id={_repr_str(str(self.receipt_id))}, "
            f"line_id={_repr_str(str(self.line_id))}, "
            f"word_id={_repr_str(str(self.word_id))}, "
            f"pinecone_id={_repr_str(self.pinecone_id)}, "
            f"text={_repr_str(self.text)}, "
            f"label={_repr_str(self.label)}, "
            f"error_message={_repr_str(self.error_message)}, "
            f"status={_repr_str(self.status)}"
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
        yield "pinecone_id", self.pinecone_id
        yield "text", self.text
        yield "label", self.label
        yield "error_message", self.error_message
        yield "status", self.status

    def __eq__(self, other) -> bool:
        if not isinstance(other, EmbeddingBatchResult):
            return False
        return (
            self.batch_id == other.batch_id
            and self.image_id == other.image_id
            and self.receipt_id == other.receipt_id
            and self.line_id == other.line_id
            and self.word_id == other.word_id
            and self.pinecone_id == other.pinecone_id
            and self.text == other.text
            and self.label == other.label
            and self.error_message == other.error_message
            and self.status == other.status
        )

    def __hash__(self) -> int:
        return hash(
            (
                self.batch_id,
                self.image_id,
                self.receipt_id,
                self.line_id,
                self.word_id,
                self.pinecone_id,
                self.text,
                self.label,
                self.error_message,
                self.status,
            )
        )


def itemToEmbeddingBatchResult(item: dict) -> EmbeddingBatchResult:
    """
    Converts an item from DynamoDB to an EmbeddingBatchResult object.
    """
    required_keys = {
        "PK",
        "SK",
        "pinecone_id",
        "text",
        "label",
        "error_message",
        "status",
    }
    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - item.keys()
        additional_keys = item.keys() - required_keys
        raise ValueError(
            f"Invalid item format\nmissing keys: {missing_keys}\nadditional keys: {additional_keys}"
        )
    try:
        batch_id = item["PK"]["S"].split("#")[1]
        sk_parts = item["SK"]["S"].split("#")
        image_id = item["image_id"]["S"]
        receipt_id = int(sk_parts[2])
        line_id = int(sk_parts[4])
        word_id = int(sk_parts[6])
        label = sk_parts[8]
        pinecone_id = item["pinecone_id"]["S"]
        text = item["text"]["S"]
        status = item["status"]["S"]

        if "error_message" in item and "S" in item["error_message"]:
            error_message = item["error_message"]["S"]
        else:
            error_message = None

        return EmbeddingBatchResult(
            batch_id=batch_id,
            image_id=image_id,
            receipt_id=receipt_id,
            line_id=line_id,
            word_id=word_id,
            pinecone_id=pinecone_id,
            status=status,
            text=text,
            label=label,
            error_message=error_message,
        )
    except Exception as e:
        raise ValueError("Error converting item to EmbeddingBatchResult")
