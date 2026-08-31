from dataclasses import dataclass
from decimal import Decimal
from math import isfinite
from typing import Any, Generator

from receipt_dynamo.constants import SectionType
from receipt_dynamo.entities.util import (
    _repr_str,
    assert_valid_uuid,
    validate_non_negative_int,
    validate_positive_int,
)

# The dev/prod vector indexes are created with 1536 dimensions
# (text-embedding-3-small) and the dimension is immutable after index
# creation, so entities enforce it at construction time.
EMBEDDING_DIMENSIONS = 1536


def embedding_number_string(value: float) -> str:
    """Serialize one vector component as a DynamoDB Number string.

    Positional notation only: ``repr`` would emit scientific notation for
    small components (e.g. ``6.6e-05``), and the Decimal expansion of the
    shortest ``repr`` round-trips to the identical float.
    """
    return format(Decimal(repr(value)), "f")


def validate_embedding_vector(name: str, vector: Any) -> list[float]:
    """Validate a 1536-dimension embedding vector and return floats.

    Args:
        name: The attribute name used in error messages.
        vector: The candidate vector.

    Returns:
        list[float]: The validated vector as floats.

    Raises:
        ValueError: When the vector is not a 1536-long list of finite,
            not-all-zero numbers.
    """
    if not isinstance(vector, list):
        raise ValueError(f"{name} must be a list")
    if len(vector) != EMBEDDING_DIMENSIONS:
        raise ValueError(
            f"{name} must have exactly {EMBEDDING_DIMENSIONS} dimensions; "
            f"got {len(vector)}"
        )
    values: list[float] = []
    for value in vector:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must contain only numbers")
        value = float(value)
        if not isfinite(value):
            raise ValueError(f"{name} must contain only finite numbers")
        values.append(value)
    if not any(values):
        raise ValueError(f"{name} must not be the zero vector")
    return values


@dataclass(eq=True, unsafe_hash=False)
class ReceiptLineEmbedding:
    """
    Represents one visual-row line embedding stored in DynamoDB.

    Each item carries the vector for one visual row of a receipt, keyed by
    the row's primary line, plus the flattened attributes the
    ``line-embeddings`` vector index filters on (``section_type``) and
    projects (text, merchant identity, ids, ``row_line_ids``). The item
    deliberately carries **no GSI1-4 keys** so it stays out of every
    GSI-based access path; the vector index is its only reader.

    Attributes:
        receipt_id (int): Identifier for the receipt.
        image_id (str): UUID identifying the image the receipt belongs to.
        line_id (int): The visual row's primary line id (first line in the
            row); the embedding item is keyed by it.
        line_vector (list[float]): The 1536-dimension embedding of the
            visual row with its rows above/below as context.
        text (str): The visual row's text (display/debug field).
        row_line_ids (list[int]): Every line id in the visual row,
            including ``line_id``.
        merchant_name (str | None): Denormalized resolved merchant name.
        place_id (str | None): Denormalized Google Places id.
        section_type (str | None): The row's VALID receipt section type;
            the index's inline filter attribute.
    """

    REQUIRED_KEYS = {
        "PK",
        "SK",
        "line_vector",
        "text",
        "row_line_ids",
    }

    receipt_id: int
    image_id: str
    line_id: int
    line_vector: list[float]
    text: str
    row_line_ids: list[int]
    merchant_name: str | None = None
    place_id: str | None = None
    section_type: str | None = None

    def __post_init__(self):
        """Validate and initialize the ReceiptLineEmbedding instance."""
        validate_positive_int("receipt_id", self.receipt_id)
        assert_valid_uuid(self.image_id)
        validate_non_negative_int("line_id", self.line_id)

        self.line_vector = validate_embedding_vector(
            "line_vector", self.line_vector
        )

        if not isinstance(self.text, str) or not self.text:
            raise ValueError("text must be a non-empty string")

        if not isinstance(self.row_line_ids, list) or not self.row_line_ids:
            raise ValueError("row_line_ids must be a non-empty list")
        for row_line_id in self.row_line_ids:
            try:
                validate_non_negative_int("row_line_id", row_line_id)
            except ValueError as exc:
                raise ValueError(
                    "row_line_ids must contain only integers greater than "
                    "or equal to zero"
                ) from exc
        if len(set(self.row_line_ids)) != len(self.row_line_ids):
            raise ValueError("row_line_ids must not contain duplicates")
        if self.line_id not in self.row_line_ids:
            raise ValueError("row_line_ids must include line_id")
        self.row_line_ids = list(self.row_line_ids)

        if self.merchant_name is not None and not isinstance(
            self.merchant_name, str
        ):
            raise ValueError("merchant_name must be a string or None")
        if self.place_id is not None and not isinstance(self.place_id, str):
            raise ValueError("place_id must be a string or None")

        if self.section_type is not None:
            if isinstance(self.section_type, SectionType):
                self.section_type = self.section_type.value
            valid_section_types = {t.value for t in SectionType}
            if self.section_type not in valid_section_types:
                raise ValueError(
                    "section_type must be one of "
                    f"{sorted(valid_section_types)} or None; got "
                    f"{self.section_type!r}"
                )

    @property
    def key(self) -> dict[str, Any]:
        """Generate the primary key for the receipt line embedding."""
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {
                "S": (
                    f"RECEIPT#{self.receipt_id:05d}#"
                    f"LINE#{self.line_id:05d}#EMBEDDING"
                )
            },
        }

    def to_item(self) -> dict[str, Any]:
        """Convert the ReceiptLineEmbedding to a DynamoDB item.

        Optional attributes are only emitted when set, so the vector
        index's INCLUDE projection stays sparse for unknown values.
        """
        self.__post_init__()
        item = {
            **self.key,
            "TYPE": {"S": "RECEIPT_LINE_EMBEDDING"},
            "line_vector": {
                "L": [
                    {"N": embedding_number_string(value)}
                    for value in self.line_vector
                ]
            },
            "text": {"S": self.text},
            "image_id": {"S": self.image_id},
            "receipt_id": {"N": str(self.receipt_id)},
            "line_id": {"N": str(self.line_id)},
            "row_line_ids": {
                "L": [{"N": str(line_id)} for line_id in self.row_line_ids]
            },
        }
        if self.merchant_name is not None:
            item["merchant_name"] = {"S": self.merchant_name}
        if self.place_id is not None:
            item["place_id"] = {"S": self.place_id}
        if self.section_type is not None:
            item["section_type"] = {"S": self.section_type}
        return item

    def __repr__(self) -> str:
        """Returns a string representation of the ReceiptLineEmbedding."""
        return (
            f"ReceiptLineEmbedding("
            f"receipt_id={self.receipt_id}, "
            f"image_id={_repr_str(self.image_id)}, "
            f"line_id={self.line_id}, "
            f"line_vector=<{len(self.line_vector)} floats>, "
            f"text={_repr_str(self.text)}, "
            f"row_line_ids={self.row_line_ids}, "
            f"merchant_name={_repr_str(self.merchant_name)}, "
            f"place_id={_repr_str(self.place_id)}, "
            f"section_type={_repr_str(self.section_type)}"
            f")"
        )

    def __iter__(self) -> Generator[tuple[str, Any], None, None]:
        """Iterate over the attributes of the ReceiptLineEmbedding."""
        yield "image_id", self.image_id
        yield "receipt_id", self.receipt_id
        yield "line_id", self.line_id
        yield "line_vector", self.line_vector
        yield "text", self.text
        yield "row_line_ids", self.row_line_ids
        yield "merchant_name", self.merchant_name
        yield "place_id", self.place_id
        yield "section_type", self.section_type

    def __hash__(self) -> int:
        """Return a hash of the ReceiptLineEmbedding."""
        return hash(
            (
                self.receipt_id,
                self.image_id,
                self.line_id,
                tuple(self.line_vector),
                self.text,
                tuple(self.row_line_ids),
                self.merchant_name,
                self.place_id,
                self.section_type,
            )
        )

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "ReceiptLineEmbedding":
        """Converts a DynamoDB item to a ReceiptLineEmbedding object.

        Args:
            item: The DynamoDB item to convert.

        Returns:
            ReceiptLineEmbedding: The ReceiptLineEmbedding object.

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

            line_vector = [
                float(value["N"]) for value in item["line_vector"]["L"]
            ]
            text = item["text"]["S"]
            row_line_ids = [
                int(value["N"]) for value in item["row_line_ids"]["L"]
            ]
            merchant_name = (
                item["merchant_name"]["S"] if "merchant_name" in item else None
            )
            place_id = item["place_id"]["S"] if "place_id" in item else None
            section_type = (
                item["section_type"]["S"] if "section_type" in item else None
            )

            return cls(
                receipt_id=receipt_id,
                image_id=image_id,
                line_id=line_id,
                line_vector=line_vector,
                text=text,
                row_line_ids=row_line_ids,
                merchant_name=merchant_name,
                place_id=place_id,
                section_type=section_type,
            )
        except (KeyError, IndexError, ValueError) as e:
            raise ValueError(
                f"Error converting item to ReceiptLineEmbedding: {e}"
            ) from e


def item_to_receipt_line_embedding(
    item: dict[str, Any],
) -> ReceiptLineEmbedding:
    """Converts a DynamoDB item to a ReceiptLineEmbedding object.

    Args:
        item (dict): The DynamoDB item to convert.

    Returns:
        ReceiptLineEmbedding: The ReceiptLineEmbedding object.

    Raises:
        ValueError: When the item format is invalid.
    """
    return ReceiptLineEmbedding.from_item(item)
