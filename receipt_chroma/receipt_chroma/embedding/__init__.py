"""ChromaDB embedding pipeline module.

This module provides functionality for creating embeddings and managing
the ChromaDB ingestion pipeline, including delta creation, OpenAI batch
orchestration, and metadata management.
"""

from receipt_chroma.embedding.delta.line_delta import (
    save_line_embeddings_as_delta,
)
from receipt_chroma.embedding.delta.producer import produce_embedding_delta
from receipt_chroma.embedding.delta.word_delta import (
    save_word_embeddings_as_delta,
)
from receipt_chroma.embedding.orchestration import (
    EmbeddingResult,
    create_embeddings_and_compaction_run,
)

__all__ = [
    "EmbeddingResult",
    "create_embeddings_and_compaction_run",
    "produce_embedding_delta",
    "save_line_embeddings_as_delta",
    "save_word_embeddings_as_delta",
]
