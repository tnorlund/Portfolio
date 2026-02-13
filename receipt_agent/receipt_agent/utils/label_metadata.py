"""Utilities for Chroma label metadata parsing and where-clause construction."""

from typing import Optional

from receipt_agent.utils.chroma_types import ChromaMetadata, ChromaWhereClause


def parse_labels_from_metadata(
    metadata: ChromaMetadata,
    array_field: str,
    legacy_field: str,
) -> list[str]:
    """Read labels (array-first) and normalize to uppercase."""
    array_val = metadata.get(array_field)
    if isinstance(array_val, list):
        return sorted(
            {
                str(label).strip().upper()
                for label in array_val
                if str(label).strip()
            }
        )

    legacy_val = metadata.get(legacy_field, "")
    if isinstance(legacy_val, str):
        return sorted(
            {
                label.strip().upper()
                for label in legacy_val.split(",")
                if label.strip()
            }
        )

    return []


def metadata_has_label(
    metadata: ChromaMetadata,
    label: str,
    *,
    array_field: str,
    legacy_field: str,
) -> bool:
    """Check label membership using array metadata with legacy string fallback."""
    normalized_label = label.strip().upper()
    return normalized_label in parse_labels_from_metadata(
        metadata,
        array_field=array_field,
        legacy_field=legacy_field,
    )


def metadata_matches_label_state(
    metadata: ChromaMetadata,
    label: str,
    label_state: str,
    *,
    valid_array_field: str = "valid_labels_array",
    valid_legacy_field: str = "valid_labels",
    invalid_array_field: str = "invalid_labels_array",
    invalid_legacy_field: str = "invalid_labels",
) -> bool:
    """Check label membership for valid/invalid/any state."""
    if label_state == "valid":
        return metadata_has_label(
            metadata,
            label,
            array_field=valid_array_field,
            legacy_field=valid_legacy_field,
        )
    if label_state == "invalid":
        return metadata_has_label(
            metadata,
            label,
            array_field=invalid_array_field,
            legacy_field=invalid_legacy_field,
        )
    return metadata_has_label(
        metadata,
        label,
        array_field=valid_array_field,
        legacy_field=valid_legacy_field,
    ) or metadata_has_label(
        metadata,
        label,
        array_field=invalid_array_field,
        legacy_field=invalid_legacy_field,
    )


def build_label_membership_clause(
    label: str,
    *,
    array_field: str,
    legacy_field: str,
) -> ChromaWhereClause:
    """Build a Chroma where-clause for label membership on array metadata.

    Note: Chroma metadata `$contains` performs array membership checks. It does
    not perform substring matching for scalar metadata strings, so legacy CSV
    filtering must be handled client-side after querying.
    """
    _ = legacy_field  # kept for call-site compatibility
    normalized_label = label.strip().upper()
    return {array_field: {"$contains": normalized_label}}


def build_label_state_clause(
    label: str,
    label_state: str,
    *,
    valid_array_field: str = "valid_labels_array",
    valid_legacy_field: str = "valid_labels",
    invalid_array_field: str = "invalid_labels_array",
    invalid_legacy_field: str = "invalid_labels",
) -> ChromaWhereClause:
    """Build a where-clause for valid/invalid/any label state."""
    normalized_label = label.strip().upper()
    if label_state == "valid":
        return build_label_membership_clause(
            normalized_label,
            array_field=valid_array_field,
            legacy_field=valid_legacy_field,
        )
    if label_state == "invalid":
        return build_label_membership_clause(
            normalized_label,
            array_field=invalid_array_field,
            legacy_field=invalid_legacy_field,
        )
    return {
        "$or": [
            build_label_membership_clause(
                normalized_label,
                array_field=valid_array_field,
                legacy_field=valid_legacy_field,
            ),
            build_label_membership_clause(
                normalized_label,
                array_field=invalid_array_field,
                legacy_field=invalid_legacy_field,
            ),
        ]
    }


def combine_where_clauses(
    clauses: list[Optional[ChromaWhereClause]],
) -> Optional[ChromaWhereClause]:
    """Combine optional where clauses with AND semantics."""
    filtered = [clause for clause in clauses if clause]
    if not filtered:
        return None
    if len(filtered) == 1:
        return filtered[0]
    return {"$and": filtered}
