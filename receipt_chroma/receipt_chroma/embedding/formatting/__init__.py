"""Formatting utilities for embedding context.

This module provides functions for formatting line and word context
for embedding operations, including row-based line embeddings.
"""

from receipt_chroma.embedding.formatting.line_format import (
    LineLike,
    format_line_context_embedding_input,
    format_row_embedding_input,
    format_visual_row,
    get_line_neighbors,
    get_primary_line_id,
    get_row_embedding_inputs,
    group_lines_into_visual_rows,
    parse_prev_next_from_formatted,
)
from receipt_chroma.embedding.formatting.word_format import (
    format_word_context_embedding_input,
    get_word_neighbors,
    parse_left_right_from_formatted,
)

__all__ = [
    # Line formatting (row-based)
    "LineLike",
    "group_lines_into_visual_rows",
    "format_visual_row",
    "format_row_embedding_input",
    "get_row_embedding_inputs",
    "get_primary_line_id",
    # Line formatting (legacy)
    "format_line_context_embedding_input",
    "parse_prev_next_from_formatted",
    "get_line_neighbors",
    # Word formatting
    "format_word_context_embedding_input",
    "parse_left_right_from_formatted",
    "get_word_neighbors",
]
