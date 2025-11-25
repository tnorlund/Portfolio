"""Formatting utilities for embedding context.

This module provides functions for formatting line and word context
for embedding operations.
"""

from receipt_chroma.embedding.formatting.line_format import (
    format_line_context_embedding_input,
    get_line_neighbors,
    parse_prev_next_from_formatted,
)
from receipt_chroma.embedding.formatting.word_format import (
    format_word_context_embedding_input,
    get_word_neighbors,
    parse_left_right_from_formatted,
)

__all__ = [
    "format_line_context_embedding_input",
    "parse_prev_next_from_formatted",
    "get_line_neighbors",
    "format_word_context_embedding_input",
    "parse_left_right_from_formatted",
    "get_word_neighbors",
]
