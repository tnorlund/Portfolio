"""ChromaDB delta creation module.

This module provides functionality for creating ChromaDB delta files
that will be processed by the compaction pipeline.
"""

from receipt_chroma.embedding.delta.line_delta import (
    save_line_embeddings_as_delta,
)
from receipt_chroma.embedding.delta.producer import produce_embedding_delta
from receipt_chroma.embedding.delta.word_delta import (
    save_word_embeddings_as_delta,
)

__all__ = [
    "produce_embedding_delta",
    "save_line_embeddings_as_delta",
    "save_word_embeddings_as_delta",
]
