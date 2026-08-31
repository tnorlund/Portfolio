"""Backend-neutral receipt embedding interfaces."""

from receipt_embeddings.vector_client import (
    FilterValue,
    ScoredItem,
    VectorItem,
    VectorSearchClient,
)

__all__ = [
    "FilterValue",
    "ScoredItem",
    "VectorItem",
    "VectorSearchClient",
]
