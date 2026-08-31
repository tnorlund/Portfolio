"""Backend-neutral receipt embedding interfaces."""

from receipt_embeddings.indexes import (
    EMBEDDING_DIMENSION,
    LINE_INDEX,
    MAX_SEARCH_VECTORS_TOP_K,
    WORD_INDEX,
)
from receipt_embeddings.quotas import (
    MAX_GET_LIMIT,
    MAX_QUERY_EMBEDDINGS_PER_CALL,
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
    "EMBEDDING_DIMENSION",
    "FilterValue",
    "LINE_INDEX",
    "MAX_GET_LIMIT",
    "MAX_QUERY_EMBEDDINGS_PER_CALL",
    "MAX_SEARCH_VECTORS_TOP_K",
    "ScoredItem",
    "VectorItem",
    "VectorSearchClient",
    "WORD_INDEX",
    "build_chroma_where",
    "ensure_get_ids_within_quota",
    "ensure_query_embeddings_within_quota",
]
