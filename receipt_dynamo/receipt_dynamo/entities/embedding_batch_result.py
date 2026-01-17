from dataclasses import dataclass
from typing import Any, Generator

from receipt_dynamo.constants import EmbeddingStatus
from receipt_dynamo.entities.entity_mixins import BatchResultGSIMixin
from receipt_dynamo.entities.util import (
    _repr_str,
    assert_type,
    assert_valid_uuid,
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
    # Expect exactly 8 segments: IMAGE, uuid, RECEIPT, padded receipt_id,
    # LINE, padded line_id, WORD, padded word_id
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
        if (
            parts[3] != f"{receipt_id:05d}"
            or parts[5] != f"{line_id:05d}"
            or parts[7] != f"{word_id:05d}"
        ):
            return False
    except (IndexError, ValueError):
        return False
    return True


@dataclass(eq=True, unsafe_hash=False)
class EmbeddingBatchResult(BatchResultGSIMixin):
    REQUIRED_KEYS = {
        "PK",
        "SK",
        "pinecone_id",
        "text",
        "error_message",
        "status",
        "image_id",
    }

    batch_id: str
    image_id: str
    receipt_id: int
    line_id: int
    word_id: int
    pinecone_id: str
    status: str
    text: str
    error_message: str | None = None

    def __post_init__(self):
        assert_valid_uuid(self.batch_id)

        assert_valid_uuid(self.image_id)

        assert_type("receipt_id", self.receipt_id, int, ValueError)
        if self.receipt_id <= 0:
            raise ValueError("receipt_id must be greater than zero")

        assert_type("line_id", self.line_id, int, ValueError)
        if self.line_id < 0:
            raise ValueError("line_id must be greater than or equal to zero")

        assert_type("word_id", self.word_id, int, ValueError)
        if self.word_id < 0:
            raise ValueError("word_id must be greater than or equal to zero")

        self.status = normalize_enum(self.status, EmbeddingStatus)

        assert_type("text", self.text, str, ValueError)

        if self.error_message is not None:
            assert_type("error_message", self.error_message, str, ValueError)

        # validate pinecone_id format
        if not validate_pinecone_id_format(
            self.pinecone_id, self.receipt_id, self.line_id, self.word_id
        ):
            raise ValueError(
                "pinecone_id must be in the format "
                "IMAGE#<uuid>#RECEIPT#<receipt_id>#LINE#<line_id>#"
                "WORD#<word_id> "
                f"\nGot: {self.pinecone_id}"
            )

    @property
    def key(self) -> dict[str, Any]:
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

    # gsi2_key and gsi3_key are provided by BatchResultGSIMixin

    def to_item(self) -> dict[str, Any]:
        return {
            **self.key,
            **self.gsi2_key,
            **self.gsi3_key,
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

    def __iter__(self) -> Generator[tuple[str, Any], None, None]:
        yield "batch_id", self.batch_id
        yield "image_id", self.image_id
        yield "receipt_id", self.receipt_id
        yield "line_id", self.line_id
        yield "word_id", self.word_id
        yield "pinecone_id", self.pinecone_id
        yield "text", self.text
        yield "error_message", self.error_message
        yield "status", self.status

    # __eq__ is automatically generated by dataclass(eq=True)

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


    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "EmbeddingBatchResult":
        """Converts a DynamoDB item to an EmbeddingBatchResult object.

        Args:
            item: The DynamoDB item to convert.

        Returns:
            EmbeddingBatchResult: The EmbeddingBatchResult object.

        Raises:
            ValueError: When the item format is invalid.
        """
        if not cls.REQUIRED_KEYS.issubset(item.keys()):
            missing_keys = cls.REQUIRED_KEYS - item.keys()
            additional_keys = item.keys() - cls.REQUIRED_KEYS
            raise ValueError(
                f"Invalid item format\nmissing keys: {missing_keys}\n"
                f"additional keys: {additional_keys}"
            )
        try:
            # Extract batch_id from PK
            batch_id = item["PK"]["S"].split("#", 1)[1]

            # Split SK into parts for flexible parsing
            sk_parts: list[str] = item["SK"]["S"].split("#")

            # Helper to find the value after a given key in SK
            def sk_value(key: str) -> str:
                # Find all positions of the key and use the last one to avoid
                # prefix collisions
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

            return cls(
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
            raise ValueError(
                f"Error converting item to EmbeddingBatchResult: {e}"
            ) from e


def item_to_embedding_batch_result(
    item: dict[str, Any],
) -> EmbeddingBatchResult:
    """Converts a DynamoDB item to an EmbeddingBatchResult object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        EmbeddingBatchResult: The EmbeddingBatchResult object.

    Raises:
        ValueError: When the item format is invalid.
    """
    return EmbeddingBatchResult.from_item(item)
