"""Similarity evaluation surface: protocol + exact-NN fake."""

from receipt_embeddings.testing.fake_index import FakeVectorIndex
from receipt_embeddings.vector_client import (
    LINE_EMBEDDINGS_INDEX,
    WORD_EMBEDDINGS_INDEX,
    ScoredItem,
    VectorSearchClient,
    index_for_key,
    line_item_key,
    word_item_key,
)

__version__ = "0.1.0"

__all__ = [
    "LINE_EMBEDDINGS_INDEX",
    "WORD_EMBEDDINGS_INDEX",
    "FakeVectorIndex",
    "ScoredItem",
    "VectorSearchClient",
    "index_for_key",
    "line_item_key",
    "word_item_key",
]
