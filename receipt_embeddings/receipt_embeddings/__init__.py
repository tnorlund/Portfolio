"""Backend-neutral receipt embedding interfaces."""

from receipt_embeddings.backend import vector_search_client
from receipt_embeddings.dynamo_client import DynamoVectorSearchClient
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
    report_incomplete,
)

__all__ = [
    "DynamoVectorSearchClient",
    "EmbeddingWriteFailure",
    "EmbeddingWriteReport",
    "EmbeddingWriteRequest",
    "EmbeddingWriter",
    "FilterValue",
    "RESOLVER_NEIGHBOR_METADATA_KEYS",
    "ScoredItem",
    "VectorItem",
    "VectorSearchClient",
    "report_incomplete",
    "vector_search_client",
]
