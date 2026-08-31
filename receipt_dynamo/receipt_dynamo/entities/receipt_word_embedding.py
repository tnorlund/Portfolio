from dataclasses import dataclass
from typing import Any, Generator

from receipt_dynamo.entities.receipt_line_embedding import (
    embedding_number_string,
    validate_embedding_vector,
)
from receipt_dynamo.entities.util import (
    _repr_str,
    assert_valid_uuid,
    validate_non_negative_int,
    validate_positive_int,
)

# The words-index inline filter attribute is equality-only, so the status
# vocabulary is flat and closed (SPEC chroma-removal §3.3).
VALID_LABEL_STATUSES = ("validated", "pending", "none")


@dataclass(eq=True, unsafe_hash=False)
class ReceiptWordEmbedding:
    """
    Represents one word-context embedding stored in DynamoDB.

    Each item carries the vector for one receipt word embedded with its
    ±2-word row context, plus the flattened attributes the
    ``word-embeddings`` vector index filters on (``label_status``) and
    projects (text, merchant name, ids). The item deliberately carries
    **no GSI1-4 keys** so it stays out of every GSI-based access path;
    the vector index is its only reader.

    Attributes:
        receipt_id (int): Identifier for the receipt.
        image_id (str): UUID identifying the image the receipt belongs to.
        line_id (int): The word's line id.
        word_id (int): The word's id within the line.
        word_vector (list[float]): The 1536-dimension embedding of the
            word with its ±2-word context.
        text (str): The word's text (display/debug field).
        label_status (str): validated / pending / none — the index's
            inline filter attribute. ``validated`` means at least one
            VALID word label, ``pending`` at least one PENDING label
            (and no VALID), ``none`` otherwise.
        merchant_name (str | None): Denormalized resolved merchant name.
        primary_label (str | None): The highest-confidence VALID label.
        valid_labels (list[str] | None): All VALID labels, for
            client-side post-filtering (not projected by the index).
    """

    REQUIRED_KEYS = {
        "PK",
        "SK",
        "word_vector",
        "text",
        "label_status",
    }

    receipt_id: int
    image_id: str
    line_id: int
    word_id: int
    word_vector: list[float]
    text: str
    label_status: str = "none"
    merchant_name: str | None = None
    primary_label: str | None = None
    valid_labels: list[str] | None = None

    def __post_init__(self):
        """Validate and initialize the ReceiptWordEmbedding instance."""
        validate_positive_int("receipt_id", self.receipt_id)
        assert_valid_uuid(self.image_id)
        validate_non_negative_int("line_id", self.line_id)
        validate_non_negative_int("word_id", self.word_id)

        self.word_vector = validate_embedding_vector(
            "word_vector", self.word_vector
        )

        if not isinstance(self.text, str) or not self.text:
            raise ValueError("text must be a non-empty string")

        if self.label_status not in VALID_LABEL_STATUSES:
            raise ValueError(
                "label_status must be one of "
                f"{list(VALID_LABEL_STATUSES)}; got {self.label_status!r}"
            )

        if self.merchant_name is not None and not isinstance(
            self.merchant_name, str
        ):
            raise ValueError("merchant_name must be a string or None")
        if self.primary_label is not None and not isinstance(
            self.primary_label, str
        ):
            raise ValueError("primary_label must be a string or None")

        if self.valid_labels is not None:
            if not isinstance(self.valid_labels, list) or not all(
                isinstance(label, str) and label for label in self.valid_labels
            ):
                raise ValueError(
                    "valid_labels must be a list of non-empty strings or None"
                )
            if len(set(self.valid_labels)) != len(self.valid_labels):
                raise ValueError("valid_labels must not contain duplicates")
            self.valid_labels = list(self.valid_labels)

    @property
    def key(self) -> dict[str, Any]:
        """Generate the primary key for the receipt word embedding."""
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {
                "S": (
                    f"RECEIPT#{self.receipt_id:05d}#"
                    f"LINE#{self.line_id:05d}#"
                    f"WORD#{self.word_id:05d}#EMBEDDING"
                )
            },
        }

    def to_item(self) -> dict[str, Any]:
        """Convert the ReceiptWordEmbedding to a DynamoDB item.

        Optional attributes are only emitted when set, so the vector
        index's INCLUDE projection stays sparse for unknown values.
        """
        self.__post_init__()
        item = {
            **self.key,
            "TYPE": {"S": "RECEIPT_WORD_EMBEDDING"},
            "word_vector": {
                "L": [
                    {"N": embedding_number_string(value)}
                    for value in self.word_vector
                ]
            },
            "text": {"S": self.text},
            "image_id": {"S": self.image_id},
            "receipt_id": {"N": str(self.receipt_id)},
            "line_id": {"N": str(self.line_id)},
            "word_id": {"N": str(self.word_id)},
            "label_status": {"S": self.label_status},
        }
        if self.merchant_name is not None:
            item["merchant_name"] = {"S": self.merchant_name}
        if self.primary_label is not None:
            item["primary_label"] = {"S": self.primary_label}
        if self.valid_labels is not None:
            item["valid_labels"] = {
                "L": [{"S": label} for label in self.valid_labels]
            }
        return item

    def __repr__(self) -> str:
        """Returns a string representation of the ReceiptWordEmbedding."""
        return (
            f"ReceiptWordEmbedding("
            f"receipt_id={self.receipt_id}, "
            f"image_id={_repr_str(self.image_id)}, "
            f"line_id={self.line_id}, "
            f"word_id={self.word_id}, "
            f"word_vector=<{len(self.word_vector)} floats>, "
            f"text={_repr_str(self.text)}, "
            f"label_status={_repr_str(self.label_status)}, "
            f"merchant_name={_repr_str(self.merchant_name)}, "
            f"primary_label={_repr_str(self.primary_label)}, "
            f"valid_labels={self.valid_labels}"
            f")"
        )

    def __iter__(self) -> Generator[tuple[str, Any], None, None]:
        """Iterate over the attributes of the ReceiptWordEmbedding."""
        yield "image_id", self.image_id
        yield "receipt_id", self.receipt_id
        yield "line_id", self.line_id
        yield "word_id", self.word_id
        yield "word_vector", self.word_vector
        yield "text", self.text
        yield "label_status", self.label_status
        yield "merchant_name", self.merchant_name
        yield "primary_label", self.primary_label
        yield "valid_labels", self.valid_labels

    def __hash__(self) -> int:
        """Return a hash of the ReceiptWordEmbedding."""
        return hash(
            (
                self.receipt_id,
                self.image_id,
                self.line_id,
                self.word_id,
                tuple(self.word_vector),
                self.text,
                self.label_status,
                self.merchant_name,
                self.primary_label,
                (
                    tuple(self.valid_labels)
                    if self.valid_labels is not None
                    else None
                ),
            )
        )

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "ReceiptWordEmbedding":
        """Converts a DynamoDB item to a ReceiptWordEmbedding object.

        Args:
            item: The DynamoDB item to convert.

        Returns:
            ReceiptWordEmbedding: The ReceiptWordEmbedding object.

        Raises:
            ValueError: When the item format is invalid.
        """
        if not cls.REQUIRED_KEYS.issubset(item.keys()):
            missing_keys = cls.REQUIRED_KEYS - set(item.keys())
            raise ValueError(f"Item is missing required keys: {missing_keys}")

        try:
            image_id = item["PK"]["S"].split("#")[1]
            sk_parts = item["SK"]["S"].split("#")
            receipt_id = int(sk_parts[1])
            line_id = int(sk_parts[3])
            word_id = int(sk_parts[5])

            word_vector = [
                float(value["N"]) for value in item["word_vector"]["L"]
            ]
            text = item["text"]["S"]
            label_status = item["label_status"]["S"]
            merchant_name = (
                item["merchant_name"]["S"] if "merchant_name" in item else None
            )
            primary_label = (
                item["primary_label"]["S"] if "primary_label" in item else None
            )
            valid_labels = (
                [value["S"] for value in item["valid_labels"]["L"]]
                if "valid_labels" in item
                else None
            )

            return cls(
                receipt_id=receipt_id,
                image_id=image_id,
                line_id=line_id,
                word_id=word_id,
                word_vector=word_vector,
                text=text,
                label_status=label_status,
                merchant_name=merchant_name,
                primary_label=primary_label,
                valid_labels=valid_labels,
            )
        except (KeyError, IndexError, ValueError) as e:
            raise ValueError(
                f"Error converting item to ReceiptWordEmbedding: {e}"
            ) from e


def item_to_receipt_word_embedding(
    item: dict[str, Any],
) -> ReceiptWordEmbedding:
    """Converts a DynamoDB item to a ReceiptWordEmbedding object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        ReceiptWordEmbedding: The ReceiptWordEmbedding object.

    Raises:
        ValueError: When the item format is invalid.
    """
    return ReceiptWordEmbedding.from_item(item)
