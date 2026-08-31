"""Vector-search client and (later) embedding formatters.

Round A ships the search protocol and exact-NN test fake. Round B relocates
``receipt_chroma.embedding.{formatting,openai}`` here. This package must not
import ``chromadb``.
"""

from receipt_embeddings.vector_client import (
    DISTANCE_ATOL,
    INDEX_ALIASES,
    INDEX_LINES,
    INDEX_WORDS,
    ScoredItem,
    VectorSearchClient,
    cosine_distance,
    cosine_distances,
    normalize_index_name,
)

__version__ = "0.1.0"

__all__ = [
    "DISTANCE_ATOL",
    "INDEX_ALIASES",
    "INDEX_LINES",
    "INDEX_WORDS",
    "ScoredItem",
    "VectorSearchClient",
    "__version__",
    "cosine_distance",
    "cosine_distances",
    "normalize_index_name",
]
