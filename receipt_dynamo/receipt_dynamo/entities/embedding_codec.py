"""Shared DynamoDB codecs for dedicated embedding items.

Vector attributes are stored as ``L`` of ``N`` (the item-attribute form).
``SearchVectors`` uses a plain list of ``{"N": ...}`` dicts without the
``L`` wrapper — that request shape lives in ``receipt_embeddings``.
"""

from __future__ import annotations

from collections.abc import Sequence
from decimal import Decimal
from math import isfinite
from typing import Any

EMBEDDING_DIMENSIONS = 1536
LINE_VECTOR_ATTR = "line_vector"
WORD_VECTOR_ATTR = "word_vector"
LABEL_STATUS_VALIDATED = "validated"
LABEL_STATUS_PENDING = "pending"
LABEL_STATUS_NONE = "none"
LABEL_STATUSES = frozenset(
    {
        LABEL_STATUS_VALIDATED,
        LABEL_STATUS_PENDING,
        LABEL_STATUS_NONE,
    }
)


def format_vector_component(value: float) -> str:
    """Serialize one float as a DynamoDB decimal string."""

    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError("vector components must be finite numbers")
    number = float(value)
    if not isfinite(number):
        raise ValueError("vector components must be finite numbers")
    # Fixed-point: DynamoDB Number strings reject scientific notation.
    return format(Decimal(repr(number)), "f")


def serialize_vector(values: Sequence[float]) -> dict[str, Any]:
    """Store a vector as a DynamoDB ``L`` of ``N`` values."""

    _require_vector(values)
    return {"L": [{"N": format_vector_component(value)} for value in values]}


def deserialize_vector(attribute: object, *, name: str) -> list[float]:
    """Read a stored ``L`` of ``N`` vector attribute."""

    if not isinstance(attribute, dict) or "L" not in attribute:
        raise ValueError(f"{name} must be a DynamoDB list (L) of numbers")
    values: list[float] = []
    for position, component in enumerate(attribute["L"]):
        if not isinstance(component, dict) or "N" not in component:
            raise ValueError(f"{name}[{position}] must be a DynamoDB number")
        number = float(component["N"])
        if not isfinite(number):
            raise ValueError(f"{name}[{position}] must be finite")
        values.append(number)
    _require_vector(values, name=name)
    return values


def serialize_int_list(values: Sequence[int]) -> dict[str, Any]:
    """Store a list of integers as DynamoDB ``L`` of ``N``."""

    if not values:
        raise ValueError("integer list must not be empty")
    encoded: list[dict[str, str]] = []
    for value in values:
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            raise ValueError("integer list must contain non-negative ints")
        encoded.append({"N": str(value)})
    return {"L": encoded}


def deserialize_int_list(attribute: object, *, name: str) -> list[int]:
    """Read a stored ``L`` of ``N`` integer list."""

    if not isinstance(attribute, dict) or "L" not in attribute:
        raise ValueError(f"{name} must be a DynamoDB list (L) of numbers")
    values: list[int] = []
    for position, component in enumerate(attribute["L"]):
        if not isinstance(component, dict) or "N" not in component:
            raise ValueError(f"{name}[{position}] must be a DynamoDB number")
        try:
            number = int(component["N"])
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name}[{position}] must be an integer") from exc
        if number < 0:
            raise ValueError(f"{name}[{position}] must be non-negative")
        values.append(number)
    if not values:
        raise ValueError(f"{name} must not be empty")
    return values


def optional_string(item: dict[str, Any], name: str) -> str | None:
    """Read an optional DynamoDB string attribute."""

    if name not in item or item[name] == {"NULL": True}:
        return None
    attribute = item[name]
    if not isinstance(attribute, dict) or "S" not in attribute:
        raise ValueError(f"{name} must be a DynamoDB string (S)")
    value = attribute["S"]
    return value if value else None


def _require_vector(values: Sequence[float], *, name: str = "vector") -> None:
    if len(values) != EMBEDDING_DIMENSIONS:
        raise ValueError(
            f"{name} must have {EMBEDDING_DIMENSIONS} dimensions; "
            f"got {len(values)}"
        )
    for position, value in enumerate(values):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{name}[{position}] must be a finite number")
        if not isfinite(float(value)):
            raise ValueError(f"{name}[{position}] must be a finite number")


def parse_image_pk(pk: str) -> str:
    """Extract ``image_id`` from ``IMAGE#{{uuid}}``."""

    if not pk.startswith("IMAGE#"):
        raise ValueError(f"PK must start with IMAGE#, got {pk!r}")
    image_id = pk.split("#", 1)[1]
    if not image_id or "#" in image_id:
        raise ValueError(f"invalid IMAGE# partition key: {pk!r}")
    return image_id


def line_embedding_sk(receipt_id: int, line_id: int) -> str:
    return f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#EMBEDDING"


def word_embedding_sk(receipt_id: int, line_id: int, word_id: int) -> str:
    return (
        f"RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}#"
        f"WORD#{word_id:05d}#EMBEDDING"
    )


def vector_search_line_key(
    image_id: str, receipt_id: int, line_id: int
) -> str:
    """Harness / Chroma identity for a visual-row embedding."""

    return f"IMAGE#{image_id}#RECEIPT#{receipt_id:05d}#LINE#{line_id:05d}"


def vector_search_word_key(
    image_id: str, receipt_id: int, line_id: int, word_id: int
) -> str:
    """Harness / Chroma identity for a word embedding."""

    return (
        f"{vector_search_line_key(image_id, receipt_id, line_id)}"
        f"#WORD#{word_id:05d}"
    )


__all__ = [
    "EMBEDDING_DIMENSIONS",
    "LABEL_STATUSES",
    "LABEL_STATUS_NONE",
    "LABEL_STATUS_PENDING",
    "LABEL_STATUS_VALIDATED",
    "LINE_VECTOR_ATTR",
    "WORD_VECTOR_ATTR",
    "deserialize_int_list",
    "deserialize_vector",
    "format_vector_component",
    "line_embedding_sk",
    "optional_string",
    "parse_image_pk",
    "serialize_int_list",
    "serialize_vector",
    "vector_search_line_key",
    "vector_search_word_key",
    "word_embedding_sk",
]
