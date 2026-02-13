"""Strict typing helpers for Chroma metadata and where clauses."""

from collections.abc import Mapping
from typing import TypeAlias, TypedDict


class ChromaMetadata(TypedDict, total=False):
    """Normalized metadata fields commonly returned by Chroma queries."""

    image_id: str
    receipt_id: int
    line_id: int
    word_id: int
    text: str
    x: float
    y: float
    width: float
    height: float
    left: str
    right: str
    merchant_name: str
    label: str
    label_status: str
    validation_status: str
    label_proposed_by: str
    label_validated_at: str
    valid_labels: str
    invalid_labels: str
    valid_labels_array: list[str]
    invalid_labels_array: list[str]
    normalized_phone_10: str
    normalized_full_address: str
    normalized_url: str
    place_id: str


ChromaWhereClause: TypeAlias = dict[str, object]


def _as_str(value: object) -> str | None:
    if isinstance(value, str):
        return value
    return None


def _as_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        try:
            return int(value)
        except ValueError:
            return None
    return None


def _as_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _as_str_list(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    normalized: list[str] = []
    for item in value:
        if isinstance(item, str):
            normalized.append(item)
    return normalized


def coerce_chroma_metadata(raw: object) -> ChromaMetadata:
    """Coerce arbitrary Chroma metadata payloads into a typed dict."""
    metadata: ChromaMetadata = {}
    if not isinstance(raw, Mapping):
        return metadata

    image_id = _as_str(raw.get("image_id"))
    if image_id is not None:
        metadata["image_id"] = image_id
    text = _as_str(raw.get("text"))
    if text is not None:
        metadata["text"] = text
    left = _as_str(raw.get("left"))
    if left is not None:
        metadata["left"] = left
    right = _as_str(raw.get("right"))
    if right is not None:
        metadata["right"] = right
    merchant_name = _as_str(raw.get("merchant_name"))
    if merchant_name is not None:
        metadata["merchant_name"] = merchant_name
    label = _as_str(raw.get("label"))
    if label is not None:
        metadata["label"] = label
    label_status = _as_str(raw.get("label_status"))
    if label_status is not None:
        metadata["label_status"] = label_status
    validation_status = _as_str(raw.get("validation_status"))
    if validation_status is not None:
        metadata["validation_status"] = validation_status
    label_proposed_by = _as_str(raw.get("label_proposed_by"))
    if label_proposed_by is not None:
        metadata["label_proposed_by"] = label_proposed_by
    label_validated_at = _as_str(raw.get("label_validated_at"))
    if label_validated_at is not None:
        metadata["label_validated_at"] = label_validated_at
    valid_labels = _as_str(raw.get("valid_labels"))
    if valid_labels is not None:
        metadata["valid_labels"] = valid_labels
    invalid_labels = _as_str(raw.get("invalid_labels"))
    if invalid_labels is not None:
        metadata["invalid_labels"] = invalid_labels
    normalized_phone = _as_str(raw.get("normalized_phone_10"))
    if normalized_phone is not None:
        metadata["normalized_phone_10"] = normalized_phone
    normalized_address = _as_str(raw.get("normalized_full_address"))
    if normalized_address is not None:
        metadata["normalized_full_address"] = normalized_address
    normalized_url = _as_str(raw.get("normalized_url"))
    if normalized_url is not None:
        metadata["normalized_url"] = normalized_url
    place_id = _as_str(raw.get("place_id"))
    if place_id is not None:
        metadata["place_id"] = place_id

    receipt_id = _as_int(raw.get("receipt_id"))
    if receipt_id is not None:
        metadata["receipt_id"] = receipt_id
    line_id = _as_int(raw.get("line_id"))
    if line_id is not None:
        metadata["line_id"] = line_id
    word_id = _as_int(raw.get("word_id"))
    if word_id is not None:
        metadata["word_id"] = word_id

    x = _as_float(raw.get("x"))
    if x is not None:
        metadata["x"] = x
    y = _as_float(raw.get("y"))
    if y is not None:
        metadata["y"] = y
    width = _as_float(raw.get("width"))
    if width is not None:
        metadata["width"] = width
    height = _as_float(raw.get("height"))
    if height is not None:
        metadata["height"] = height

    valid_labels_array = _as_str_list(raw.get("valid_labels_array"))
    if valid_labels_array is not None:
        metadata["valid_labels_array"] = valid_labels_array

    invalid_labels_array = _as_str_list(raw.get("invalid_labels_array"))
    if invalid_labels_array is not None:
        metadata["invalid_labels_array"] = invalid_labels_array

    return metadata


def extract_query_metadata_rows(
    results: Mapping[str, object],
) -> list[ChromaMetadata]:
    """Extract nested metadata rows from Chroma query() results."""
    raw_metadatas = results.get("metadatas")
    if not isinstance(raw_metadatas, list) or not raw_metadatas:
        return []

    first_row = raw_metadatas[0]
    if not isinstance(first_row, list):
        return []

    return [coerce_chroma_metadata(item) for item in first_row]


def extract_get_metadata_rows(
    results: Mapping[str, object],
) -> list[ChromaMetadata]:
    """Extract metadata rows from Chroma get() results."""
    raw_metadatas = results.get("metadatas")
    if not isinstance(raw_metadatas, list):
        return []

    # Some wrappers return nested rows, align with query() shape.
    if raw_metadatas and isinstance(raw_metadatas[0], list):
        nested = raw_metadatas[0]
        if isinstance(nested, list):
            return [coerce_chroma_metadata(item) for item in nested]
        return []

    return [coerce_chroma_metadata(item) for item in raw_metadatas]
