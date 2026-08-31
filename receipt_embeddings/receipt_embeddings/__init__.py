"""Backend-neutral receipt embedding interfaces."""

from receipt_embeddings.dynamo_client import (
    DynamoVectorSearchClient,
    create_client_from_env,
)
from receipt_embeddings.dynamo_quotas import (
    LINE_EMBEDDINGS_INDEX,
    PROTOCOL_LINE_INDEX,
    PROTOCOL_WORD_INDEX,
    SEARCH_VECTORS_MAX_TOP_K,
    WORD_EMBEDDINGS_INDEX,
    ensure_equality_only_filters,
    ensure_top_k_within_search_quota,
    resolve_dynamo_index_name,
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
    "DynamoVectorSearchClient",
    "FilterValue",
    "LINE_EMBEDDINGS_INDEX",
    "MAX_GET_LIMIT",
    "MAX_QUERY_EMBEDDINGS_PER_CALL",
    "PROTOCOL_LINE_INDEX",
    "PROTOCOL_WORD_INDEX",
    "SEARCH_VECTORS_MAX_TOP_K",
    "ScoredItem",
    "VectorItem",
    "VectorSearchClient",
    "WORD_EMBEDDINGS_INDEX",
    "build_chroma_where",
    "create_client_from_env",
    "ensure_equality_only_filters",
    "ensure_get_ids_within_quota",
    "ensure_query_embeddings_within_quota",
    "ensure_top_k_within_search_quota",
    "resolve_dynamo_index_name",
]
