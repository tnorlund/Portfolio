"""Compatibility exports for the relocated formatting package."""

from receipt_embeddings.formatting import *  # noqa: F401,F403

__all__ = [
    "LineLike",
    "group_lines_into_visual_rows",
    "format_visual_row",
    "format_row_embedding_input",
    "get_row_embedding_inputs",
    "get_primary_line_id",
    "format_line_context_embedding_input",
    "parse_prev_next_from_formatted",
    "GROUPING_VERSION",
    "WordLike",
    "PriceColumn",
    "LabelAmountPair",
    "is_amount_text",
    "detect_price_column",
    "pair_row_label_amount",
    "build_receipt_rows",
    "format_word_context_embedding_input",
    "get_word_neighbors",
]
