"""Chroma Cloud service limits and real-client contract helpers.

The constants pin the Chroma Cloud quotas that a query-issuing code path
must never exceed (verified live 2026-08-31; exceeding the first trips
the ``NumQueryEmbeddings`` quota error). The where-builder pins the real
``chromadb`` filter contract: the ``VectorSearchClient`` protocol takes a
flat mapping of equality predicates joined with AND, but the real client
rejects a bare multi-key ``where`` dict — two or more predicates must be
wrapped in ``$and`` by the adapter.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence, Sized

from receipt_embeddings.vector_client import FilterValue

MAX_QUERY_EMBEDDINGS_PER_CALL = 20
MAX_GET_LIMIT = 250


def ensure_query_embeddings_within_quota(
    query_embeddings: Sequence[Sized],
) -> None:
    """Reject a query batch that would trip the NumQueryEmbeddings quota."""

    if len(query_embeddings) > MAX_QUERY_EMBEDDINGS_PER_CALL:
        raise ValueError(
            f"one Chroma query call accepts at most "
            f"{MAX_QUERY_EMBEDDINGS_PER_CALL} embeddings; "
            f"got {len(query_embeddings)}"
        )


def ensure_get_ids_within_quota(ids: Sequence[str]) -> None:
    """Reject a get batch larger than Chroma Cloud's get limit."""

    if len(ids) > MAX_GET_LIMIT:
        raise ValueError(
            f"one Chroma get call accepts at most {MAX_GET_LIMIT} ids; "
            f"got {len(ids)}"
        )


def build_chroma_where(
    filters: Mapping[str, FilterValue] | None,
) -> dict[str, object] | None:
    """Convert protocol filters into the real client's ``where`` shape.

    Zero filters build no clause, one filter builds a bare equality, and
    two or more build ``{"$and": [...]}`` — the shape real chromadb
    requires for multiple predicates. Keys are sorted so the built clause
    is deterministic. Operator keys are rejected: filters are flat
    equality predicates, never pre-built where syntax.
    """

    if not filters:
        return None
    for key in filters:
        if key.startswith("$"):
            raise ValueError(
                f"filters are flat equality predicates; operator key "
                f"{key!r} belongs to the adapter, not the caller"
            )
    items = sorted(filters.items())
    if len(items) == 1:
        key, value = items[0]
        return {key: value}
    return {"$and": [{key: value} for key, value in items]}


__all__ = [
    "MAX_GET_LIMIT",
    "MAX_QUERY_EMBEDDINGS_PER_CALL",
    "build_chroma_where",
    "ensure_get_ids_within_quota",
    "ensure_query_embeddings_within_quota",
]
