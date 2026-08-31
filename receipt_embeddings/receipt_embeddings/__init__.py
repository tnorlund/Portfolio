"""Backend-agnostic vector search for receipt embeddings."""

from receipt_embeddings.vector_client import (
    LINES_INDEX,
    WORDS_INDEX,
    ScoredItem,
    VectorSearchClient,
)

__version__ = "0.1.0"

__all__ = [
    "LINES_INDEX",
    "WORDS_INDEX",
    "ScoredItem",
    "VectorSearchClient",
]
