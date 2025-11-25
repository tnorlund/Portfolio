"""Metadata creation for ChromaDB embeddings.

This module provides functions for creating consistent metadata structures
for line and word embeddings that will be stored in ChromaDB.
"""

from receipt_chroma.embedding.metadata.line_metadata import (
    create_line_metadata,
    enrich_line_metadata_with_anchors,
)
from receipt_chroma.embedding.metadata.word_metadata import (
    create_word_metadata,
    enrich_word_metadata_with_anchors,
)

__all__ = [
    "create_line_metadata",
    "enrich_line_metadata_with_anchors",
    "create_word_metadata",
    "enrich_word_metadata_with_anchors",
]
