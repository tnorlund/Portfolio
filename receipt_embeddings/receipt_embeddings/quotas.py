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

# DynamoDB vector search (GA 2026-08-05). SearchVectors returns at most
# 100 neighbors; filters are equality-only; indexes are 1536-dim COSINE.
MAX_SEARCH_RESULTS = 100
MAX_VECTOR_DIMENSIONS = 4096
EMBEDDING_DIMENSIONS = 1536
DEV_TABLE_NAME = "ReceiptsTable-dc5be22"
PROD_TABLE_NAME = "ReceiptsTable-d7ff76a"
LINE_EMBEDDING_INDEX = "line-embeddings"
WORD_EMBEDDING_INDEX = "word-embeddings"
PROTOCOL_LINE_INDEX = "lines-vectors"
PROTOCOL_WORD_INDEX = "words-vectors"
LINE_VECTOR_ATTR = "line_vector"
WORD_VECTOR_ATTR = "word_vector"
VECTOR_SEARCH_REQUEST_BYTES_PER_1536 = 40_000

INDEX_NAME_MAP = {
    PROTOCOL_LINE_INDEX: LINE_EMBEDDING_INDEX,
    PROTOCOL_WORD_INDEX: WORD_EMBEDDING_INDEX,
    LINE_EMBEDDING_INDEX: LINE_EMBEDDING_INDEX,
    WORD_EMBEDDING_INDEX: WORD_EMBEDDING_INDEX,
}
VECTOR_ATTR_FOR_INDEX = {
    LINE_EMBEDDING_INDEX: LINE_VECTOR_ATTR,
    WORD_EMBEDDING_INDEX: WORD_VECTOR_ATTR,
}


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


def ensure_top_k_within_quota(top_k: int) -> None:
    """Reject a SearchVectors page larger than the service cap."""

    if isinstance(top_k, bool) or not isinstance(top_k, int):
        raise TypeError("top_k must be an integer")
    if not 1 <= top_k <= MAX_SEARCH_RESULTS:
        raise ValueError(f"top_k must be between 1 and {MAX_SEARCH_RESULTS}")


def dynamo_index_name(index: str) -> str:
    """Map a protocol index name onto the judge-provisioned Dynamo index."""

    try:
        return INDEX_NAME_MAP[index]
    except KeyError as exc:
        raise ValueError(f"unknown vector index: {index!r}") from exc


def require_dev_table(table_name: str) -> str:
    """Refuse any table other than the judge-provisioned dev table."""

    if table_name != DEV_TABLE_NAME:
        raise ValueError(
            f"refusing to query DynamoDB table {table_name!r}; "
            f"only {DEV_TABLE_NAME!r} is allowed"
        )
    return table_name


__all__ = [
    "DEV_TABLE_NAME",
    "EMBEDDING_DIMENSIONS",
    "INDEX_NAME_MAP",
    "LINE_EMBEDDING_INDEX",
    "LINE_VECTOR_ATTR",
    "MAX_GET_LIMIT",
    "MAX_QUERY_EMBEDDINGS_PER_CALL",
    "MAX_SEARCH_RESULTS",
    "MAX_VECTOR_DIMENSIONS",
    "PROD_TABLE_NAME",
    "PROTOCOL_LINE_INDEX",
    "PROTOCOL_WORD_INDEX",
    "VECTOR_ATTR_FOR_INDEX",
    "VECTOR_SEARCH_REQUEST_BYTES_PER_1536",
    "WORD_EMBEDDING_INDEX",
    "WORD_VECTOR_ATTR",
    "build_chroma_where",
    "dynamo_index_name",
    "ensure_get_ids_within_quota",
    "ensure_query_embeddings_within_quota",
    "ensure_top_k_within_quota",
    "require_dev_table",
]
