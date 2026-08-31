"""Back-compat re-export. Implementation: receipt_embeddings.formatting.word_format."""

from receipt_embeddings.formatting.word_format import (
    WordLike,
    format_word_context_embedding_input,
    get_word_neighbors,
)

__all__ = [
    "WordLike",
    "format_word_context_embedding_input",
    "get_word_neighbors",
]
