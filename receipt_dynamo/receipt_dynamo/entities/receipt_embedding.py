"""Dedicated DynamoDB items for receipt line and word embeddings."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any, ClassVar

from receipt_dynamo.entities.dynamodb_utils import parse_dynamodb_map
from receipt_dynamo.entities.util import (
    assert_valid_uuid,
    validate_non_negative_int,
    validate_positive_int,
)

EMBEDDING_DIMENSIONS = 1536
_LABEL_STATUSES = frozenset({"validated", "pending", "none"})


def _validate_text(
    name: str, value: object, *, allow_empty: bool = False
) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{name} must be a string")
    if not allow_empty and not value:
        raise ValueError(f"{name} must not be empty")
    return value


def _validate_vector(vector: object, *, name: str) -> list[float]:
    if not isinstance(vector, list):
        raise ValueError(f"{name} must be a list")
    if len(vector) != EMBEDDING_DIMENSIONS:
        raise ValueError(
            f"{name} must contain {EMBEDDING_DIMENSIONS} values; "
            f"got {len(vector)}"
        )
    normalized: list[float] = []
    for value in vector:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name} must contain only numbers")
        number = float(value)
        if not isfinite(number):
            raise ValueError(f"{name} must contain only finite numbers")
        normalized.append(number)
    return normalized


def _vector_attribute(vector: list[float]) -> dict[str, Any]:
    """Serialize an item vector as DynamoDB ``L`` of numeric values."""

    return {"L": [{"N": str(value)} for value in vector]}


def _validate_common(
    *,
    image_id: str,
    receipt_id: int,
    line_id: int,
    text: str,
    merchant_name: str,
) -> None:
    assert_valid_uuid(image_id)
    validate_positive_int("receipt_id", receipt_id)
    validate_non_negative_int("line_id", line_id)
    _validate_text("text", text)
    _validate_text("merchant_name", merchant_name, allow_empty=True)


@dataclass(eq=True)
class ReceiptLineEmbedding:
    """One visual-row embedding, keyed by its primary receipt line."""

    TYPE: ClassVar[str] = "RECEIPT_LINE_EMBEDDING"
    VECTOR_ATTRIBUTE: ClassVar[str] = "line_vector"

    image_id: str
    receipt_id: int
    line_id: int
    text: str
    merchant_name: str
    place_id: str
    row_line_ids: list[int]
    section_type: str
    line_vector: list[float]
    # Fetch-join metadata (spec §3.2/§3.3 amendment): ordinary unprojected
    # attributes the resolver's phone/address tiers read after a
    # SearchVectors -> BatchGetItem join. Computed the same way the Chroma
    # metadata writer computes its anchor fields; empty means "no anchor".
    normalized_phone_10: str = ""
    normalized_full_address: str = ""

    def __post_init__(self) -> None:
        _validate_common(
            image_id=self.image_id,
            receipt_id=self.receipt_id,
            line_id=self.line_id,
            text=self.text,
            merchant_name=self.merchant_name,
        )
        _validate_text("place_id", self.place_id, allow_empty=True)
        _validate_text("section_type", self.section_type, allow_empty=True)
        _validate_text(
            "normalized_phone_10", self.normalized_phone_10, allow_empty=True
        )
        _validate_text(
            "normalized_full_address",
            self.normalized_full_address,
            allow_empty=True,
        )
        if not isinstance(self.row_line_ids, list) or not self.row_line_ids:
            raise ValueError("row_line_ids must be a non-empty list")
        for row_line_id in self.row_line_ids:
            validate_non_negative_int("row_line_id", row_line_id)
        if len(set(self.row_line_ids)) != len(self.row_line_ids):
            raise ValueError("row_line_ids must not contain duplicates")
        if self.line_id not in self.row_line_ids:
            raise ValueError("row_line_ids must include line_id")
        self.row_line_ids = list(self.row_line_ids)
        self.line_vector = _validate_vector(
            self.line_vector, name=self.VECTOR_ATTRIBUTE
        )

    @property
    def key(self) -> dict[str, Any]:
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {
                "S": (
                    f"RECEIPT#{self.receipt_id:05d}#"
                    f"LINE#{self.line_id:05d}#EMBEDDING"
                )
            },
        }

    @property
    def canonical_key(self) -> str:
        return (
            f"IMAGE#{self.image_id}#RECEIPT#{self.receipt_id:05d}#"
            f"LINE#{self.line_id:05d}"
        )

    def to_item(self) -> dict[str, Any]:
        """Serialize without any GSI1-GSI4 keys.

        The normalized anchor attributes are sparse — present only when an
        anchor exists — mirroring the Chroma metadata writer, which sets
        ``normalized_phone_10`` / ``normalized_full_address`` keys only when
        the row carries the corresponding anchor.
        """

        item = {
            **self.key,
            "TYPE": {"S": self.TYPE},
            self.VECTOR_ATTRIBUTE: _vector_attribute(self.line_vector),
            "text": {"S": self.text},
            "merchant_name": {"S": self.merchant_name},
            "place_id": {"S": self.place_id},
            "image_id": {"S": self.image_id},
            "receipt_id": {"N": str(self.receipt_id)},
            "line_id": {"N": str(self.line_id)},
            "row_line_ids": {
                "L": [{"N": str(value)} for value in self.row_line_ids]
            },
            "section_type": {"S": self.section_type},
        }
        if self.normalized_phone_10:
            item["normalized_phone_10"] = {"S": self.normalized_phone_10}
        if self.normalized_full_address:
            item["normalized_full_address"] = {
                "S": self.normalized_full_address
            }
        return item

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "ReceiptLineEmbedding":
        values = parse_dynamodb_map(item)
        if values.get("TYPE") != cls.TYPE:
            raise ValueError(f"item TYPE must be {cls.TYPE}")
        return cls(
            image_id=values["image_id"],
            receipt_id=values["receipt_id"],
            line_id=values["line_id"],
            text=values["text"],
            merchant_name=values.get("merchant_name", ""),
            place_id=values.get("place_id", ""),
            row_line_ids=values["row_line_ids"],
            section_type=values.get("section_type", ""),
            line_vector=values[cls.VECTOR_ATTRIBUTE],
            normalized_phone_10=values.get("normalized_phone_10", ""),
            normalized_full_address=values.get("normalized_full_address", ""),
        )


@dataclass(eq=True)
class ReceiptWordEmbedding:
    """One word-context embedding adjacent to its receipt word item."""

    TYPE: ClassVar[str] = "RECEIPT_WORD_EMBEDDING"
    VECTOR_ATTRIBUTE: ClassVar[str] = "word_vector"

    image_id: str
    receipt_id: int
    line_id: int
    word_id: int
    text: str
    merchant_name: str
    label_status: str
    word_vector: list[float]

    def __post_init__(self) -> None:
        _validate_common(
            image_id=self.image_id,
            receipt_id=self.receipt_id,
            line_id=self.line_id,
            text=self.text,
            merchant_name=self.merchant_name,
        )
        validate_non_negative_int("word_id", self.word_id)
        if self.label_status not in _LABEL_STATUSES:
            raise ValueError(
                "label_status must be one of validated, pending, or none"
            )
        self.word_vector = _validate_vector(
            self.word_vector, name=self.VECTOR_ATTRIBUTE
        )

    @property
    def key(self) -> dict[str, Any]:
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {
                "S": (
                    f"RECEIPT#{self.receipt_id:05d}#"
                    f"LINE#{self.line_id:05d}#WORD#{self.word_id:05d}#EMBEDDING"
                )
            },
        }

    @property
    def canonical_key(self) -> str:
        return (
            f"IMAGE#{self.image_id}#RECEIPT#{self.receipt_id:05d}#"
            f"LINE#{self.line_id:05d}#WORD#{self.word_id:05d}"
        )

    def to_item(self) -> dict[str, Any]:
        """Serialize without any GSI1-GSI4 keys."""

        return {
            **self.key,
            "TYPE": {"S": self.TYPE},
            self.VECTOR_ATTRIBUTE: _vector_attribute(self.word_vector),
            "text": {"S": self.text},
            "merchant_name": {"S": self.merchant_name},
            "image_id": {"S": self.image_id},
            "receipt_id": {"N": str(self.receipt_id)},
            "line_id": {"N": str(self.line_id)},
            "word_id": {"N": str(self.word_id)},
            "label_status": {"S": self.label_status},
        }

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "ReceiptWordEmbedding":
        values = parse_dynamodb_map(item)
        if values.get("TYPE") != cls.TYPE:
            raise ValueError(f"item TYPE must be {cls.TYPE}")
        return cls(
            image_id=values["image_id"],
            receipt_id=values["receipt_id"],
            line_id=values["line_id"],
            word_id=values["word_id"],
            text=values["text"],
            merchant_name=values.get("merchant_name", ""),
            label_status=values["label_status"],
            word_vector=values[cls.VECTOR_ATTRIBUTE],
        )


ReceiptEmbedding = ReceiptLineEmbedding | ReceiptWordEmbedding


def item_to_receipt_line_embedding(
    item: dict[str, Any],
) -> ReceiptLineEmbedding:
    return ReceiptLineEmbedding.from_item(item)


def item_to_receipt_word_embedding(
    item: dict[str, Any],
) -> ReceiptWordEmbedding:
    return ReceiptWordEmbedding.from_item(item)


def item_to_receipt_embedding(item: dict[str, Any]) -> ReceiptEmbedding:
    item_type = item.get("TYPE", {}).get("S")
    if item_type == ReceiptLineEmbedding.TYPE:
        return item_to_receipt_line_embedding(item)
    if item_type == ReceiptWordEmbedding.TYPE:
        return item_to_receipt_word_embedding(item)
    raise ValueError(f"unsupported embedding item TYPE: {item_type!r}")


__all__ = [
    "EMBEDDING_DIMENSIONS",
    "ReceiptEmbedding",
    "ReceiptLineEmbedding",
    "ReceiptWordEmbedding",
    "item_to_receipt_embedding",
    "item_to_receipt_line_embedding",
    "item_to_receipt_word_embedding",
]
