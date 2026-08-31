"""Back-compat re-export. Implementation lives in receipt_embeddings."""

from receipt_embeddings.formatting.word_format import *  # noqa: F403
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
