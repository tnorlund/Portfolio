import re
from typing import Any, Dict, Generator, List, Optional, Tuple

from receipt_dynamo.constants import EmbeddingStatus
from receipt_dynamo.entities.util import (
    _repr_str,
    assert_type,
    assert_valid_uuid,
    format_type_error,
    normalize_enum,
)


def validate_pinecone_id_format(
    pinecone_id: str, receipt_id: int, line_id: int, word_id: int
) -> bool:
    """
    Validate that the pinecone_id matches the pattern:
    IMAGE#<uuid>#RECEIPT#<receipt_id:05d>#LINE#<line_id:05d>#WORD#<word_id:05d>
    """
    parts = pinecone_id.split("#")
    # Expect exactly 8 segments: IMAGE, uuid, RECEIPT, padded receipt_id, LINE, padded line_id, WORD, padded word_id
    if len(parts) != 8:
        return False
    if (
        parts[0] != "IMAGE"
        or parts[2] != "RECEIPT"
        or parts[4] != "LINE"
        or parts[6] != "WORD"
    ):
        return False
    # Validate numeric segments
    try:
        if parts[3] != f"{receipt_id:05d}":
            return False
        if parts[5] != f"{line_id:05d}":
            return False
        if parts[7] != f"{word_id:05d}":
            return False
    except Exception:
        return False
    return True


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
        error_message: Optional[str] = None,
    ):
        assert_valid_uuid(batch_id)
        self.batch_id = batch_id

        assert_valid_uuid(image_id)
        self.image_id = image_id

        assert_type("receipt_id", receipt_id, int, ValueError)
        if receipt_id <= 0:
            raise ValueError("receipt_id must be greater than zero")
        self.receipt_id = receipt_id

        assert_type("line_id", line_id, int, ValueError)
        if line_id < 0:
            raise ValueError("line_id must be greater than or equal to zero")
        self.line_id = line_id

        assert_type("word_id", word_id, int, ValueError)
        if word_id < 0:
            raise ValueError("word_id must be greater than or equal to zero")
        self.word_id = word_id

        self.status = normalize_enum(status, EmbeddingStatus)

        assert_type("text", text, str, ValueError)
        self.text = text

        if error_message is not None:
            assert_type("error_message", error_message, str, ValueError)
        self.error_message = error_message

        # validate pinecone_id format
        if not validate_pinecone_id_format(
            pinecone_id, receipt_id, line_id, word_id
        ):
            raise ValueError(
                "pinecone_id must be in the format "
                "IMAGE#<uuid>#RECEIPT#<receipt_id>#LINE#<line_id>#WORD#<word_id> "
                f"\nGot: {pinecone_id}"
            )
        self.pinecone_id = pinecone_id

    @property
    def key(self) -> Dict[str, Any]:
        sk = (
            f"RESULT#IMAGE#{self.image_id}"
            f"#RECEIPT#{self.receipt_id:05d}"
            f"#LINE#{self.line_id:03}"
            f"#WORD#{self.word_id:03}"
        )
        return {
            "PK": {"S": f"BATCH#{self.batch_id}"},
            "SK": {"S": sk},
        }

    def gsi2_key(self) -> Dict[str, Any]:
        return {
            "GSI2PK": {"S": f"BATCH#{self.batch_id}"},
            "GSI2SK": {"S": f"STATUS#{self.status}"},
        }

    def gsi3_key(self) -> Dict[str, Any]:
        return {
            "GSI3PK": {
                "S": f"IMAGE#{self.image_id}#RECEIPT#{self.receipt_id:05d}"
            },
            "GSI3SK": {"S": f"BATCH#{self.batch_id}#STATUS#{self.status}"},
        }

    def to_item(self) -> Dict[str, Any]:
        return {
            **self.key,
            **self.gsi2_key(),
            **self.gsi3_key(),
            "TYPE": {"S": "EMBEDDING_BATCH_RESULT"},
            "image_id": {"S": self.image_id},
            "pinecone_id": {"S": self.pinecone_id},
            "text": {"S": self.text},
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
                self.error_message,
                self.status,
            )
        )


def item_to_embedding_batch_result(
    item: Dict[str, Any],
) -> EmbeddingBatchResult:
    """
    Converts an item from DynamoDB to an EmbeddingBatchResult object.
    """
    required_keys = {
        "PK",
        "SK",
        "pinecone_id",
        "text",
        "error_message",
        "status",
        "image_id",
    }
    if not required_keys.issubset(item.keys()):
        missing_keys = required_keys - item.keys()
        additional_keys = item.keys() - required_keys
        raise ValueError(
            f"Invalid item format\nmissing keys: {missing_keys}\nadditional keys: {additional_keys}"
        )
    try:
        # Extract batch_id from PK
        batch_id = item["PK"]["S"].split("#", 1)[1]

        # Split SK into parts for flexible parsing
        sk_parts: List[str] = item["SK"]["S"].split("#")

        # Helper to find the value after a given key in SK
        def sk_value(key: str) -> str:
            # Find all positions of the key and use the last one to avoid prefix collisions
            idxs = [i for i, part in enumerate(sk_parts) if part == key]
            if idxs:
                idx = idxs[-1]
                return sk_parts[idx + 1]
            raise ValueError(
                f"SK missing expected key '{key}': {item['SK']['S']}"
            )

        image_id = item["image_id"]["S"]

        # Dynamically parse IDs
        receipt_id = int(sk_value("RECEIPT"))
        line_id = int(sk_value("LINE"))
        word_id = int(sk_value("WORD"))
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
            error_message=error_message,
        )
    except Exception as e:
        raise ValueError(f"Error converting item to EmbeddingBatchResult: {e}")
