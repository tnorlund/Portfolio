"""Backend-neutral receipt embedding interfaces."""

from receipt_embeddings.quotas import (
    MAX_GET_LIMIT,
    MAX_QUERY_EMBEDDINGS_PER_CALL,
    MAX_SEARCH_RESULTS,
    build_chroma_where,
    ensure_get_ids_within_quota,
    ensure_query_embeddings_within_quota,
)
from receipt_embeddings.vector_client import (
    FilterValue,
    ScoredItem,
    VectorItem,
    VectorSearchClient,
)

__all__ = [
    "FilterValue",
    "MAX_GET_LIMIT",
    "MAX_QUERY_EMBEDDINGS_PER_CALL",
    "MAX_SEARCH_RESULTS",
    "ScoredItem",
    "VectorItem",
    "VectorSearchClient",
    "build_chroma_where",
    "ensure_get_ids_within_quota",
    "ensure_query_embeddings_within_quota",
]
