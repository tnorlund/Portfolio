"""Formatting utilities for embedding context.

This module provides functions for formatting line and word context
for embedding operations, including row-based line embeddings.
"""

from receipt_chroma.embedding.formatting.line_format import (
    LineLike,
    format_line_context_embedding_input,
    format_row_embedding_input,
    format_visual_row,
    get_primary_line_id,
    get_row_embedding_inputs,
    group_lines_into_visual_rows,
    parse_prev_next_from_formatted,
)
from receipt_chroma.embedding.formatting.receipt_rows import (
    GROUPING_VERSION,
    LabelAmountPair,
    PriceColumn,
    WordLike,
    build_receipt_rows,
    detect_price_column,
    is_amount_text,
    pair_row_label_amount,
)
from receipt_chroma.embedding.formatting.word_format import (
    format_word_context_embedding_input,
    get_word_neighbors,
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
    # Persisted rows and price-column pairing
    "GROUPING_VERSION",
    "WordLike",
    "PriceColumn",
    "LabelAmountPair",
    "is_amount_text",
    "detect_price_column",
    "pair_row_label_amount",
    "build_receipt_rows",
    # Word formatting
    "format_word_context_embedding_input",
    "get_word_neighbors",
]
