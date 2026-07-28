"""Shared type aliases and helpers for Chroma-compatible metadata."""

from collections.abc import Mapping
from typing import TypeAlias

# Chroma metadata supports scalar primitives and arrays of primitives.
# ``None`` is also permitted: Chroma merges metadata on write, so a key must
# be sent explicitly as ``None`` to clear it. Omitting the key leaves the old
# value in place. See ``data/operations.py:remove_word_labels``.
ChromaMetadataScalar: TypeAlias = str | int | float | bool
ChromaMetadataArray: TypeAlias = list[ChromaMetadataScalar]
ChromaMetadataValue: TypeAlias = (
    ChromaMetadataScalar | ChromaMetadataArray | None
)
ChromaMetadataInput: TypeAlias = Mapping[str, object]
ChromaMetadataDict: TypeAlias = dict[str, ChromaMetadataValue]


def _normalize_metadata_array(value: list[object]) -> ChromaMetadataArray:
    """Validate and normalize a list metadata value."""
    normalized: ChromaMetadataArray = []
    for item in value:
        if isinstance(item, (str, int, float, bool)):
            normalized.append(item)
        else:
            raise TypeError(
                "Chroma metadata list values must be scalar primitives"
            )
    return normalized


def to_chroma_metadata_dict(
    metadata: ChromaMetadataInput,
) -> ChromaMetadataDict:
    """Convert arbitrary mapping metadata to strict Chroma metadata typing."""
    normalized: ChromaMetadataDict = {}
    for key, value in metadata.items():
        if value is None:
            # Tombstone: instructs Chroma to clear the key on write.
            normalized[key] = None
        elif isinstance(value, (str, int, float, bool)):
            normalized[key] = value
        elif isinstance(value, list):
            normalized[key] = _normalize_metadata_array(value)
        else:
            raise TypeError(
                "Chroma metadata values must be scalar primitives or lists "
                f"(key={key!r}, value_type={type(value).__name__})"
            )
    return normalized
