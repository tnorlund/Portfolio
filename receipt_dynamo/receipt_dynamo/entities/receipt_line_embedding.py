"""Dedicated line-embedding items (SPEC §3.1). No GSI1–4 keys."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from receipt_dynamo.entities.entity_factory import (
    EntityFactory,
    create_image_receipt_pk_parser,
)
from receipt_dynamo.entities.util import (
    assert_valid_uuid,
    build_base_item,
    serialize_number_list,
)

LINE_EMBEDDING_TYPE = "RECEIPT_LINE_EMBEDDING"
LINE_VECTOR_ATTR = "line_vector"
GSI_KEY_NAMES = ("GSI1PK", "GSI1SK", "GSI2PK", "GSI2SK", "GSI3PK", "GSI3SK")
GSI_KEY_NAMES = GSI_KEY_NAMES + ("GSI4PK", "GSI4SK")


@dataclass(kw_only=True)
class ReceiptLineEmbedding:
    """One visual-row embedding stored beside its parent receipt lines."""

    image_id: str
    receipt_id: int
    line_id: int
    line_vector: list[float]
    text: str | None = None
    merchant_name: str | None = None
    place_id: str | None = None
    row_line_ids: list[int] | None = None
    section_type: str | None = None

    REQUIRED_KEYS: ClassVar[set[str]] = {
        "PK",
        "SK",
        "TYPE",
        LINE_VECTOR_ATTR,
    }

    def __post_init__(self) -> None:
        assert_valid_uuid(self.image_id)
        if isinstance(self.receipt_id, bool) or not isinstance(
            self.receipt_id, int
        ):
            raise ValueError("receipt_id must be an integer")
        if self.receipt_id <= 0:
            raise ValueError("receipt_id must be positive")
        if isinstance(self.line_id, bool) or not isinstance(self.line_id, int):
            raise ValueError("line_id must be an integer")
        if self.line_id < 0:
            raise ValueError("line_id must be non-negative")
        if not isinstance(self.line_vector, list) or not self.line_vector:
            raise ValueError("line_vector must be a non-empty list")
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            for value in self.line_vector
        ):
            raise ValueError("line_vector must contain only numbers")
        self.line_vector = [float(value) for value in self.line_vector]
        if not self.row_line_ids:
            self.row_line_ids = [self.line_id]
        if self.row_line_ids[0] != self.line_id:
            raise ValueError("row_line_ids[0] must equal line_id")

    @property
    def key(self) -> dict[str, Any]:
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {
                "S": (
                    f"RECEIPT#{self.receipt_id:05d}"
                    f"#LINE#{self.line_id:05d}#EMBEDDING"
                )
            },
        }

    def to_item(self) -> dict[str, Any]:
        item = {
            **build_base_item(self, LINE_EMBEDDING_TYPE),
            LINE_VECTOR_ATTR: serialize_number_list(self.line_vector),
        }
        if self.text is not None:
            item["text"] = {"S": self.text}
        if self.merchant_name is not None:
            item["merchant_name"] = {"S": self.merchant_name}
        if self.place_id is not None:
            item["place_id"] = {"S": self.place_id}
        if self.row_line_ids is not None:
            item["row_line_ids"] = serialize_number_list(self.row_line_ids)
        if self.section_type is not None:
            item["section_type"] = {"S": self.section_type}
        for banned in GSI_KEY_NAMES:
            item.pop(banned, None)
        return item

    def harness_key(self) -> str:
        return (
            f"IMAGE#{self.image_id}"
            f"#RECEIPT#{self.receipt_id:05d}"
            f"#LINE#{self.line_id:05d}"
        )


def item_to_receipt_line_embedding(
    item: dict[str, Any],
) -> ReceiptLineEmbedding:
    """Convert a DynamoDB item to a ReceiptLineEmbedding."""

    def parse_sk(sk: str) -> dict[str, Any]:
        parts = sk.split("#")
        if (
            len(parts) != 5
            or parts[0] != "RECEIPT"
            or parts[2] != "LINE"
            or parts[4] != "EMBEDDING"
        ):
            raise ValueError(f"Invalid SK for ReceiptLineEmbedding: {sk}")
        return {"receipt_id": int(parts[1]), "line_id": int(parts[3])}

    return EntityFactory.create_entity(
        ReceiptLineEmbedding,
        item,
        ReceiptLineEmbedding.REQUIRED_KEYS,
        custom_extractors={
            LINE_VECTOR_ATTR: EntityFactory.extract_float_list_field(
                LINE_VECTOR_ATTR
            ),
            "text": EntityFactory.extract_string_field("text", default=None),
            "merchant_name": EntityFactory.extract_string_field(
                "merchant_name", default=None
            ),
            "place_id": EntityFactory.extract_string_field(
                "place_id", default=None
            ),
            "section_type": EntityFactory.extract_string_field(
                "section_type", default=None
            ),
            "row_line_ids": EntityFactory.extract_int_list_field(
                "row_line_ids"
            ),
        },
        key_parsers={
            "PK": create_image_receipt_pk_parser(),
            "SK": parse_sk,
        },
    )


__all__ = [
    "GSI_KEY_NAMES",
    "LINE_EMBEDDING_TYPE",
    "LINE_VECTOR_ATTR",
    "ReceiptLineEmbedding",
    "item_to_receipt_line_embedding",
]
