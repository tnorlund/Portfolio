"""Backend-neutral receipt embedding interfaces."""

from receipt_embeddings.backend import vector_search_client
from receipt_embeddings.chroma_client import ChromaVectorSearchClient
from receipt_embeddings.dynamo_client import DynamoVectorSearchClient
from receipt_embeddings.quotas import (
    MAX_GET_LIMIT,
    MAX_QUERY_EMBEDDINGS_PER_CALL,
    build_chroma_where,
    ensure_get_ids_within_quota,
    ensure_query_embeddings_within_quota,
)
from receipt_embeddings.vector_client import (
    RESOLVER_NEIGHBOR_METADATA_KEYS,
    FilterValue,
    ScoredItem,
    VectorItem,
    VectorSearchClient,
)
from receipt_embeddings.writer import (
    EmbeddingWriteFailure,
    EmbeddingWriter,
    EmbeddingWriteReport,
    EmbeddingWriteRequest,
)

__all__ = [
    "ChromaVectorSearchClient",
    "DynamoVectorSearchClient",
    "EmbeddingWriteFailure",
    "EmbeddingWriteReport",
    "EmbeddingWriteRequest",
    "EmbeddingWriter",
    "FilterValue",
    "MAX_GET_LIMIT",
    "MAX_QUERY_EMBEDDINGS_PER_CALL",
    "RESOLVER_NEIGHBOR_METADATA_KEYS",
    "ScoredItem",
    "VectorItem",
    "VectorSearchClient",
    "build_chroma_where",
    "ensure_get_ids_within_quota",
    "ensure_query_embeddings_within_quota",
    "vector_search_client",
]
