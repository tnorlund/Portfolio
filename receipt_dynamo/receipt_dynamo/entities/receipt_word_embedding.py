"""Dedicated word-embedding items (SPEC §3.1). No GSI1–4 keys."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from receipt_dynamo.entities.entity_factory import (
    EntityFactory,
    create_image_receipt_pk_parser,
)
from receipt_dynamo.entities.receipt_line_embedding import GSI_KEY_NAMES
from receipt_dynamo.entities.util import (
    assert_valid_uuid,
    build_base_item,
    serialize_number_list,
)

WORD_EMBEDDING_TYPE = "RECEIPT_WORD_EMBEDDING"
WORD_VECTOR_ATTR = "word_vector"
LABEL_STATUSES = frozenset({"validated", "pending", "none"})


@dataclass(kw_only=True)
class ReceiptWordEmbedding:
    """One word embedding stored beside its parent receipt words."""

    image_id: str
    receipt_id: int
    line_id: int
    word_id: int
    word_vector: list[float]
    text: str | None = None
    merchant_name: str | None = None
    label_status: str | None = None
    primary_label: str | None = None

    REQUIRED_KEYS: ClassVar[set[str]] = {
        "PK",
        "SK",
        "TYPE",
        WORD_VECTOR_ATTR,
    }

    def __post_init__(self) -> None:
        assert_valid_uuid(self.image_id)
        if isinstance(self.receipt_id, bool) or not isinstance(
            self.receipt_id, int
        ):
            raise ValueError("receipt_id must be an integer")
        if self.receipt_id <= 0:
            raise ValueError("receipt_id must be positive")
        for name in ("line_id", "word_id"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise ValueError(f"{name} must be an integer")
            if value < 0:
                raise ValueError(f"{name} must be non-negative")
        if not isinstance(self.word_vector, list) or not self.word_vector:
            raise ValueError("word_vector must be a non-empty list")
        if any(
            isinstance(value, bool) or not isinstance(value, (int, float))
            for value in self.word_vector
        ):
            raise ValueError("word_vector must contain only numbers")
        self.word_vector = [float(value) for value in self.word_vector]
        if (
            self.label_status is not None
            and self.label_status not in LABEL_STATUSES
        ):
            raise ValueError(
                "label_status must be one of validated/pending/none"
            )

    @property
    def key(self) -> dict[str, Any]:
        return {
            "PK": {"S": f"IMAGE#{self.image_id}"},
            "SK": {
                "S": (
                    f"RECEIPT#{self.receipt_id:05d}"
                    f"#LINE#{self.line_id:05d}"
                    f"#WORD#{self.word_id:05d}#EMBEDDING"
                )
            },
        }

    def to_item(self) -> dict[str, Any]:
        item = {
            **build_base_item(self, WORD_EMBEDDING_TYPE),
            WORD_VECTOR_ATTR: serialize_number_list(self.word_vector),
        }
        if self.text is not None:
            item["text"] = {"S": self.text}
        if self.merchant_name is not None:
            item["merchant_name"] = {"S": self.merchant_name}
        if self.label_status is not None:
            item["label_status"] = {"S": self.label_status}
        if self.primary_label is not None:
            item["primary_label"] = {"S": self.primary_label}
        for banned in GSI_KEY_NAMES:
            item.pop(banned, None)
        return item

    def harness_key(self) -> str:
        return (
            f"IMAGE#{self.image_id}"
            f"#RECEIPT#{self.receipt_id:05d}"
            f"#LINE#{self.line_id:05d}"
            f"#WORD#{self.word_id:05d}"
        )


def item_to_receipt_word_embedding(
    item: dict[str, Any],
) -> ReceiptWordEmbedding:
    """Convert a DynamoDB item to a ReceiptWordEmbedding."""

    def parse_sk(sk: str) -> dict[str, Any]:
        parts = sk.split("#")
        if (
            len(parts) != 7
            or parts[0] != "RECEIPT"
            or parts[2] != "LINE"
            or parts[4] != "WORD"
            or parts[6] != "EMBEDDING"
        ):
            raise ValueError(f"Invalid SK for ReceiptWordEmbedding: {sk}")
        return {
            "receipt_id": int(parts[1]),
            "line_id": int(parts[3]),
            "word_id": int(parts[5]),
        }

    return EntityFactory.create_entity(
        ReceiptWordEmbedding,
        item,
        ReceiptWordEmbedding.REQUIRED_KEYS,
        custom_extractors={
            WORD_VECTOR_ATTR: EntityFactory.extract_float_list_field(
                WORD_VECTOR_ATTR
            ),
            "text": EntityFactory.extract_string_field("text", default=None),
            "merchant_name": EntityFactory.extract_string_field(
                "merchant_name", default=None
            ),
            "label_status": EntityFactory.extract_string_field(
                "label_status", default=None
            ),
            "primary_label": EntityFactory.extract_string_field(
                "primary_label", default=None
            ),
        },
        key_parsers={
            "PK": create_image_receipt_pk_parser(),
            "SK": parse_sk,
        },
    )


__all__ = [
    "LABEL_STATUSES",
    "WORD_EMBEDDING_TYPE",
    "WORD_VECTOR_ATTR",
    "ReceiptWordEmbedding",
    "item_to_receipt_word_embedding",
]
