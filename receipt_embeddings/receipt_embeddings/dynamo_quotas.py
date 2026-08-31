"""DynamoDB vector-search service quotas and index-name contract.

The constants pin the SearchVectors limits (verified against the GA
service and the judge-provisioned dev indexes, 2026-08-31) that every
query-issuing code path must respect. The guards raise the same error
shapes ``FakeVectorIndex`` raises, so the fake and the real backend stay
pinned to one contract (Round A standing amendment).
"""

from __future__ import annotations

from collections.abc import Mapping
from numbers import Real

from receipt_embeddings.vector_client import FilterValue

# One SearchVectors call returns at most 100 results (TopK is capped).
SEARCH_VECTORS_MAX_TOP_K = 100
SEARCH_VECTORS_MIN_TOP_K = 1
# Inline filters are equality-only: no ranges, no IN, no negation.
# The protocol already expresses filters as a flat equality mapping;
# operator keys are rejected outright.
SEARCH_VECTORS_MAX_VECTOR_DIMENSIONS = 4096
MAX_VECTOR_INDEXES_PER_TABLE = 5
# SearchVectors/VectorIndexUpdates first shipped in botocore 1.43.64.
MIN_BOTO3_VERSION = "1.43.64"

# The receipt corpus embeds with text-embedding-3-small; both live
# indexes were created 1536-dimension COSINE and are immutable.
EMBEDDING_DIMENSIONS = 1536

# Physical index and vector-attribute names on the receipts table
# (judge-provisioned; never create/alter/delete these).
LINE_EMBEDDINGS_INDEX = "line-embeddings"
WORD_EMBEDDINGS_INDEX = "word-embeddings"
LINE_VECTOR_ATTRIBUTE = "line_vector"
WORD_VECTOR_ATTRIBUTE = "word_vector"

# Protocol-level index names used by the Round A harness and fixtures.
PROTOCOL_LINE_INDEX = "lines-vectors"
PROTOCOL_WORD_INDEX = "words-vectors"

DYNAMO_INDEX_BY_PROTOCOL_INDEX = {
    PROTOCOL_LINE_INDEX: LINE_EMBEDDINGS_INDEX,
    PROTOCOL_WORD_INDEX: WORD_EMBEDDINGS_INDEX,
}
VECTOR_ATTRIBUTE_BY_DYNAMO_INDEX = {
    LINE_EMBEDDINGS_INDEX: LINE_VECTOR_ATTRIBUTE,
    WORD_EMBEDDINGS_INDEX: WORD_VECTOR_ATTRIBUTE,
}


def resolve_dynamo_index_name(index: str) -> str:
    """Map a protocol index name to its physical DynamoDB index.

    Physical names pass through unchanged so callers holding either
    name resolve identically; anything else is rejected rather than
    forwarded to the service.
    """

    if index in VECTOR_ATTRIBUTE_BY_DYNAMO_INDEX:
        return index
    try:
        return DYNAMO_INDEX_BY_PROTOCOL_INDEX[index]
    except KeyError:
        known = sorted(
            set(DYNAMO_INDEX_BY_PROTOCOL_INDEX)
            | set(VECTOR_ATTRIBUTE_BY_DYNAMO_INDEX)
        )
        raise ValueError(
            f"unknown vector index {index!r}; expected one of {known}"
        ) from None


def ensure_top_k_within_search_quota(top_k: object) -> int:
    """Validate ``top_k`` against the SearchVectors result cap.

    Raises the same error shapes ``FakeVectorIndex`` raises so both
    backends refuse identical inputs.
    """

    if isinstance(top_k, bool) or not isinstance(top_k, int):
        raise TypeError("top_k must be an integer")
    if not SEARCH_VECTORS_MIN_TOP_K <= top_k <= SEARCH_VECTORS_MAX_TOP_K:
        raise ValueError(
            f"top_k must be between {SEARCH_VECTORS_MIN_TOP_K} and "
            f"{SEARCH_VECTORS_MAX_TOP_K}"
        )
    return top_k


def ensure_equality_only_filters(
    filters: Mapping[str, FilterValue] | None,
) -> dict[str, FilterValue]:
    """Validate that filters are flat scalar equality predicates.

    SearchVectors inline filters support equality only; operator keys
    and non-scalar values never reach the service.
    """

    validated: dict[str, FilterValue] = {}
    for key, value in (filters or {}).items():
        if key.startswith("$"):
            raise ValueError(
                f"filters are flat equality predicates; operator key "
                f"{key!r} belongs to the adapter, not the caller"
            )
        if not isinstance(value, (str, bool)) and not isinstance(value, Real):
            raise ValueError(
                f"filter {key!r} must be a scalar equality value; "
                f"got {type(value).__name__}"
            )
        validated[key] = value
    return validated


__all__ = [
    "DYNAMO_INDEX_BY_PROTOCOL_INDEX",
    "EMBEDDING_DIMENSIONS",
    "LINE_EMBEDDINGS_INDEX",
    "LINE_VECTOR_ATTRIBUTE",
    "MAX_VECTOR_INDEXES_PER_TABLE",
    "MIN_BOTO3_VERSION",
    "PROTOCOL_LINE_INDEX",
    "PROTOCOL_WORD_INDEX",
    "SEARCH_VECTORS_MAX_TOP_K",
    "SEARCH_VECTORS_MAX_VECTOR_DIMENSIONS",
    "SEARCH_VECTORS_MIN_TOP_K",
    "VECTOR_ATTRIBUTE_BY_DYNAMO_INDEX",
    "WORD_EMBEDDINGS_INDEX",
    "WORD_VECTOR_ATTRIBUTE",
    "ensure_equality_only_filters",
    "ensure_top_k_within_search_quota",
    "resolve_dynamo_index_name",
]
