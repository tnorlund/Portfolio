"""Backend-neutral receipt embedding interfaces."""

from receipt_embeddings.backend import vector_search_client
from receipt_embeddings.chroma_client import ChromaVectorSearchClient
from receipt_embeddings.dynamo_client import DynamoVectorSearchClient
from receipt_embeddings.keys import (
    line_canonical_key,
    parse_canonical_key,
    word_canonical_key,
    word_vector_key,
)
from receipt_embeddings.label_status import (
    aggregate_word_label_status,
    word_label_statuses,
)
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
from receipt_embeddings.write_requests import build_embedding_write_requests
from receipt_embeddings.writer import (
    EmbeddingWriteFailure,
    EmbeddingWriter,
    EmbeddingWriteReport,
    EmbeddingWriteRequest,
    write_report_incomplete,
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
    "aggregate_word_label_status",
    "build_chroma_where",
    "build_embedding_write_requests",
    "ensure_get_ids_within_quota",
    "ensure_query_embeddings_within_quota",
    "line_canonical_key",
    "parse_canonical_key",
    "vector_search_client",
    "word_canonical_key",
    "word_label_statuses",
    "word_vector_key",
    "write_report_incomplete",
]
