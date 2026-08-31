"""Dedicated word embedding item (SPEC §3.1).

Stored under the parent receipt's item collection with no GSI1–4 keys.
``label_status`` is the words-index inline filter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from receipt_dynamo.entities.embedding_codec import (
    LABEL_STATUS_NONE,
    LABEL_STATUSES,
    WORD_VECTOR_ATTR,
    deserialize_vector,
    optional_string,
    parse_image_pk,
    serialize_vector,
    vector_search_word_key,
    word_embedding_sk,
)
from receipt_dynamo.entities.util import (
    _repr_str,
    assert_valid_uuid,
    build_base_item,
    validate_non_negative_int,
    validate_positive_int,
)

_GSI_ATTRS = frozenset(
    {
        "GSI1PK",
        "GSI1SK",
        "GSI2PK",
        "GSI2SK",
        "GSI3PK",
        "GSI3SK",
        "GSI4PK",
        "GSI4SK",
    }
)


@dataclass(kw_only=True)
class ReceiptWordEmbedding:
    """One ±2-word-context embedding keyed beside its parent word."""

    image_id: str
    receipt_id: int
    line_id: int
    word_id: int
    word_vector: list[float]
    text: str
    label_status: str = LABEL_STATUS_NONE
    merchant_name: str | None = None

    REQUIRED_KEYS: ClassVar[set[str]] = {
        "PK",
        "SK",
        "TYPE",
        WORD_VECTOR_ATTR,
        "text",
        "label_status",
        "image_id",
        "receipt_id",
        "line_id",
        "word_id",
    }

    def __post_init__(self) -> None:
        assert_valid_uuid(self.image_id)
        validate_positive_int("receipt_id", self.receipt_id)
        validate_non_negative_int("line_id", self.line_id)
        validate_non_negative_int("word_id", self.word_id)
        if not isinstance(self.text, str):
            raise ValueError("text must be a string")
        if self.label_status not in LABEL_STATUSES:
            raise ValueError(
                f"label_status must be one of {sorted(LABEL_STATUSES)}"
            )
        self.word_vector = list(self.word_vector)
        serialize_vector(self.word_vector)
        if self.merchant_name is not None and not isinstance(
            self.merchant_name, str
        ):
            raise ValueError("merchant_name must be a string or None")
        if self.merchant_name == "":
            self.merchant_name = None

    @property
    def key(self) -> dict[str, Any]:
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {
                "S": word_embedding_sk(
                    self.receipt_id, self.line_id, self.word_id
                )
            },
        }

    @property
    def vector_search_key(self) -> str:
        return vector_search_word_key(
            self.image_id, self.receipt_id, self.line_id, self.word_id
        )

    def to_item(self) -> dict[str, Any]:
        """Serialize without GSI1–4 keys (vector index is the only reader)."""

        item = {
            **build_base_item(self, "RECEIPT_WORD_EMBEDDING"),
            WORD_VECTOR_ATTR: serialize_vector(self.word_vector),
            "text": {"S": self.text},
            "label_status": {"S": self.label_status},
            "image_id": {"S": self.image_id},
            "receipt_id": {"N": str(self.receipt_id)},
            "line_id": {"N": str(self.line_id)},
            "word_id": {"N": str(self.word_id)},
        }
        if self.merchant_name:
            item["merchant_name"] = {"S": self.merchant_name}
        leaked = _GSI_ATTRS.intersection(item)
        if leaked:
            raise RuntimeError(f"embedding item leaked GSI keys: {leaked}")
        return item

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "ReceiptWordEmbedding":
        missing = cls.REQUIRED_KEYS - set(item)
        if missing:
            raise ValueError(f"Item is missing required keys: {missing}")
        type_attr = item["TYPE"]
        if not isinstance(type_attr, dict) or type_attr.get("S") != (
            "RECEIPT_WORD_EMBEDDING"
        ):
            raise ValueError("TYPE must be RECEIPT_WORD_EMBEDDING")
        image_id = parse_image_pk(item["PK"]["S"])
        sk = item["SK"]["S"]
        parts = sk.split("#")
        if (
            len(parts) != 7
            or parts[0] != "RECEIPT"
            or parts[2] != "LINE"
            or parts[4] != "WORD"
            or parts[6] != "EMBEDDING"
        ):
            raise ValueError(f"invalid ReceiptWordEmbedding SK: {sk!r}")
        return cls(
            image_id=image_id,
            receipt_id=int(parts[1]),
            line_id=int(parts[3]),
            word_id=int(parts[5]),
            word_vector=deserialize_vector(
                item[WORD_VECTOR_ATTR], name=WORD_VECTOR_ATTR
            ),
            text=item["text"]["S"],
            label_status=item["label_status"]["S"],
            merchant_name=optional_string(item, "merchant_name"),
        )

    def __repr__(self) -> str:
        return (
            f"ReceiptWordEmbedding("
            f"image_id={_repr_str(self.image_id)}, "
            f"receipt_id={self.receipt_id}, "
            f"line_id={self.line_id}, "
            f"word_id={self.word_id}, "
            f"label_status={_repr_str(self.label_status)}, "
            f"merchant_name={_repr_str(self.merchant_name)}"
            f")"
        )


def item_to_receipt_word_embedding(
    item: dict[str, Any],
) -> ReceiptWordEmbedding:
    return ReceiptWordEmbedding.from_item(item)


__all__ = [
    "ReceiptWordEmbedding",
    "item_to_receipt_word_embedding",
]
