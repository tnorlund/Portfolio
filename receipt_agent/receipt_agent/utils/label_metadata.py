"""Utilities for Chroma label metadata parsing and where-clause construction."""

from typing import Any


def parse_labels_from_metadata(
    metadata: dict[str, Any],
    array_field: str,
    legacy_field: str,
) -> list[str]:
    """Read label arrays with fallback to legacy comma-delimited strings."""
    array_val = metadata.get(array_field)
    if isinstance(array_val, list):
        return sorted(
            {str(label).strip() for label in array_val if str(label).strip()}
        )

    legacy_val = metadata.get(legacy_field, "")
    if isinstance(legacy_val, str):
        return sorted(
            {label.strip() for label in legacy_val.split(",") if label.strip()}
        )

    return []


def build_label_membership_clause(
    label: str,
    *,
    array_field: str,
    legacy_field: str,
) -> dict[str, Any]:
    """Build a where-clause for label membership with backward compatibility."""
    normalized_label = label.strip().upper()
    return {
        "$or": [
            {array_field: {"$contains": normalized_label}},
            {legacy_field: {"$contains": f",{normalized_label},"}},
        ]
    }
