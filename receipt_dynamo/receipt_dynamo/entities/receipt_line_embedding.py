"""Dedicated visual-row embedding item (SPEC §3.1).

Stored under the parent receipt's item collection with no GSI1–4 keys so
the vector never replicates into ALL-projection GSIs. ``delete_receipt``
sweeps it automatically via the ``RECEIPT#`` SK prefix.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from receipt_dynamo.entities.embedding_codec import (
    LINE_VECTOR_ATTR,
    deserialize_int_list,
    deserialize_vector,
    line_embedding_sk,
    optional_string,
    parse_image_pk,
    serialize_int_list,
    serialize_vector,
    vector_search_line_key,
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
class ReceiptLineEmbedding:
    """One visual-row embedding keyed by the row's primary line."""

    image_id: str
    receipt_id: int
    line_id: int
    line_vector: list[float]
    text: str
    row_line_ids: list[int]
    merchant_name: str | None = None
    place_id: str | None = None
    section_type: str | None = None

    REQUIRED_KEYS: ClassVar[set[str]] = {
        "PK",
        "SK",
        "TYPE",
        LINE_VECTOR_ATTR,
        "text",
        "row_line_ids",
        "image_id",
        "receipt_id",
        "line_id",
    }

    def __post_init__(self) -> None:
        assert_valid_uuid(self.image_id)
        validate_positive_int("receipt_id", self.receipt_id)
        validate_non_negative_int("line_id", self.line_id)
        if not isinstance(self.text, str):
            raise ValueError("text must be a string")
        self.line_vector = list(self.line_vector)
        self.row_line_ids = list(self.row_line_ids)
        serialize_vector(self.line_vector)
        serialize_int_list(self.row_line_ids)
        if self.line_id not in self.row_line_ids:
            raise ValueError("line_id must be present in row_line_ids")
        for name in ("merchant_name", "place_id", "section_type"):
            value = getattr(self, name)
            if value is not None and not isinstance(value, str):
                raise ValueError(f"{name} must be a string or None")
            if value == "":
                setattr(self, name, None)

    @property
    def key(self) -> dict[str, Any]:
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {"S": line_embedding_sk(self.receipt_id, self.line_id)},
        }

    @property
    def vector_search_key(self) -> str:
        return vector_search_line_key(
            self.image_id, self.receipt_id, self.line_id
        )

    def to_item(self) -> dict[str, Any]:
        """Serialize without GSI1–4 keys (vector index is the only reader)."""

        item = {
            **build_base_item(self, "RECEIPT_LINE_EMBEDDING"),
            LINE_VECTOR_ATTR: serialize_vector(self.line_vector),
            "text": {"S": self.text},
            "row_line_ids": serialize_int_list(self.row_line_ids),
            "image_id": {"S": self.image_id},
            "receipt_id": {"N": str(self.receipt_id)},
            "line_id": {"N": str(self.line_id)},
        }
        if self.merchant_name:
            item["merchant_name"] = {"S": self.merchant_name}
        if self.place_id:
            item["place_id"] = {"S": self.place_id}
        if self.section_type:
            item["section_type"] = {"S": self.section_type}
        leaked = _GSI_ATTRS.intersection(item)
        if leaked:
            raise RuntimeError(f"embedding item leaked GSI keys: {leaked}")
        return item

    @classmethod
    def from_item(cls, item: dict[str, Any]) -> "ReceiptLineEmbedding":
        missing = cls.REQUIRED_KEYS - set(item)
        if missing:
            raise ValueError(f"Item is missing required keys: {missing}")
        type_attr = item["TYPE"]
        if not isinstance(type_attr, dict) or type_attr.get("S") != (
            "RECEIPT_LINE_EMBEDDING"
        ):
            raise ValueError("TYPE must be RECEIPT_LINE_EMBEDDING")
        image_id = parse_image_pk(item["PK"]["S"])
        sk = item["SK"]["S"]
        parts = sk.split("#")
        if (
            len(parts) != 5
            or parts[0] != "RECEIPT"
            or parts[2] != "LINE"
            or parts[4] != "EMBEDDING"
        ):
            raise ValueError(f"invalid ReceiptLineEmbedding SK: {sk!r}")
        return cls(
            image_id=image_id,
            receipt_id=int(parts[1]),
            line_id=int(parts[3]),
            line_vector=deserialize_vector(
                item[LINE_VECTOR_ATTR], name=LINE_VECTOR_ATTR
            ),
            text=item["text"]["S"],
            row_line_ids=deserialize_int_list(
                item["row_line_ids"], name="row_line_ids"
            ),
            merchant_name=optional_string(item, "merchant_name"),
            place_id=optional_string(item, "place_id"),
            section_type=optional_string(item, "section_type"),
        )

    def __repr__(self) -> str:
        return (
            f"ReceiptLineEmbedding("
            f"image_id={_repr_str(self.image_id)}, "
            f"receipt_id={self.receipt_id}, "
            f"line_id={self.line_id}, "
            f"row_line_ids={self.row_line_ids}, "
            f"merchant_name={_repr_str(self.merchant_name)}, "
            f"section_type={_repr_str(self.section_type)}"
            f")"
        )


def item_to_receipt_line_embedding(
    item: dict[str, Any],
) -> ReceiptLineEmbedding:
    return ReceiptLineEmbedding.from_item(item)


__all__ = [
    "ReceiptLineEmbedding",
    "item_to_receipt_line_embedding",
]
